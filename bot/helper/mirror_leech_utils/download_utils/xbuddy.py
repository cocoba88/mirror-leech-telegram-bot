import asyncio
import re
import os
from pathlib import Path
from urllib.parse import urlparse, urljoin
from httpx import AsyncClient
from playwright.async_api import async_playwright, Page, Request, Response


# Fungsi untuk log debugging
async def write_debug_log(message: str):
    print(f"[xbuddy] {message}")


# Hilangkan karakter ilegal dalam nama file
def sanitize_filename(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1F]', "", name).strip()


# Cek apakah URL bisa diakses
async def is_valid_url(url: str) -> bool:
    try:
        async with AsyncClient(follow_redirects=True, timeout=10) as client:
            response = await client.head(url)
            content_type = response.headers.get("content-type", "")
            return response.status_code == 200 and "text/html" not in content_type
    except Exception as e:
        await write_debug_log(f"Error checking {url}: {e}")
        return False


# Download file via HTTPX
async def download_file_with_httpx(download_url: str, destination_dir: str, user_agent: str, referer: str = None):
    headers = {"User-Agent": user_agent}
    if referer:
        headers["Referer"] = referer

    os.makedirs(destination_dir, exist_ok=True)

    try:
        async with AsyncClient(headers=headers, follow_redirects=True, timeout=60) as client:
            async with client.stream("GET", download_url) as response:
                if response.status_code != 200:
                    await write_debug_log(f"[HTTPX Download] Gagal download: {response.status_code}")
                    return None

                content_length = int(response.headers.get("Content-Length", 0))
                downloaded = 0
                cd_header = response.headers.get("Content-Disposition")
                filename = ""

                if cd_header:
                    filename = cd_header.split("filename=")[-1].strip("\"'") if "filename=" in cd_header else ""
                if not filename:
                    parsed_url = urlparse(download_url)
                    filename = parsed_url.path.strip("/").split("?")[0].split("/")[-1]
                if not filename.endswith(".mp4"):
                    filename += ".mp4"
                filename = sanitize_filename(filename)

                file_path = Path(destination_dir) / filename

                with open(file_path, "wb") as f:
                    async for chunk in response.aiter_bytes(8192):  # 8KB per chunk
                        f.write(chunk)
                        downloaded += len(chunk)
                        percent = downloaded / content_length * 100
                        await write_debug_log(f"[Progress] {downloaded}/{content_length} bytes ({percent:.1f}%)")

                await write_debug_log(f"[HTTPX Download] File tersimpan: {file_path}")
                return str(file_path)

    except Exception as e:
        await write_debug_log(f"[HTTPX Download] Error: {e}")
        return None


# Scraping utama dari 9xbuddy.site
async def scrape_and_download_9xbuddy(video_url: str):
    """
    Ambil semua link download dari halaman 9xbuddy.site
    Prioritas: .workers.dev > .9xbud.com > .video-src.com > lainnya
    """
    workers_dev_links = []
    ninexbud_links = []
    video_src_links = []
    other_links = []

    user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=user_agent)
        page = await context.new_page()

        page.on("console", lambda msg: asyncio.create_task(write_debug_log(f"[Console {msg.type.upper()}] {msg.text}")))
        page.on("pageerror", lambda err: asyncio.create_task(write_debug_log(f"[Page Error] {err}")))

        def request_handler(req: Request):
            asyncio.create_task(write_debug_log(f"[Request] {req.method} {req.url}"))

        def response_handler(res: Response):
            asyncio.create_task(write_debug_log(f"[Response] {res.status} {res.url}"))

        page.on("request", request_handler)
        page.on("response", response_handler)

        process_url = f" https://9xbuddy.site/process?url={video_url}"
        await write_debug_log(f"[Scraping] Navigating to: {process_url}")

        try:
            # Muat halaman dan tunggu JS render
            await page.goto(process_url, wait_until="networkidle")
            await page.wait_for_timeout(10000)  # Tunggu 10 detik tambahan

            all_potential_download_links = await page.query_selector_all(
                "main#root a[rel=\"noreferrer nofollow noopener\"]"
            )

            unwanted_patterns = [
                r"facebook\.com/sharer",
                r"twitter\.com/intent",
                r"vk\.com/share\.php",
                r"offmp3\.net/process",
                r"savegif\.com/process",
                r"123sudo\.com",
            ]

            for element in all_potential_download_links:
                href = await element.get_attribute("href")
                if href:
                    is_unwanted = any(re.search(p, href) for p in unwanted_patterns)
                    if not is_unwanted:
                        if ".workers.dev" in href:
                            workers_dev_links.append(href)
                        elif ".9xbud.com" in href:
                            ninexbud_links.append(href)
                        elif ".video-src.com" in href:
                            video_src_links.append(href)
                        else:
                            other_links.append(href)

            # Urutan prioritas
            candidates = workers_dev_links + ninexbud_links + video_src_links + other_links

            extracted_urls = []
            for candidate in candidates:
                if await is_valid_url(candidate):
                    extracted_urls.append(candidate)
                    break  # Hanya ambil satu yang valid

            # Jika semua gagal, kembalikan satu link saja (fallback manual)
            if not extracted_urls and candidates:
                extracted_urls.append(candidates[0])

            # Simpan file jika dibutuhkan
            downloaded_files = []
            if extracted_urls:
                result = await download_file_with_httpx(extracted_urls[0], "/root/scrape/download", user_agent, referer=process_url)
                if result:
                    downloaded_files.append(result)

            await context.close()
            await browser.close()

            return extracted_urls, downloaded_files

        except Exception as e:
            await write_debug_log(f"[Scraping] Error saat navigasi: {e}")
            await context.close()
            await browser.close()
            return [], []


# Fungsi utama untuk bot: hanya mengembalikan satu URL langsung
async def get_direct_url(video_url: str):
    """
    Hanya ambil satu link video langsung dari hasil scraping.
    Cocok digunakan oleh bot Telegram/mirror bot.
    """
    try:
        extracted_urls, _ = await scrape_and_download_9xbuddy(video_url)
        if extracted_urls:
            return extracted_urls[0]
        else:
            await write_debug_log("Tidak ada link ditemukan")
            return None
    except Exception as e:
        await write_debug_log(f"Gagal mendapatkan link: {e}")
        return None


# Test fungsi secara mandiri
if __name__ == "__main__":
    test_video_url = " https://videq.stream/d/k1crs1xbltqm "

    async def main():
        direct_link = await get_direct_url(test_video_url)
        if direct_link:
            print("Direct URL:", direct_link)
        else:
            print("Tidak ada link ditemukan")

    asyncio.run(main())
