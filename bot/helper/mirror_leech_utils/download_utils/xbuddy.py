import asyncio
import os
from pathlib import Path
from httpx import AsyncClient
from playwright.async_api import async_playwright


# Folder tujuan download
DOWNLOAD_DIR = "/root/Tera/downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)


async def write_debug_log(message: str):
    print(f"[xbuddy] {message}")


def sanitize_filename(name: str) -> str:
    return "".join(c for c in name if c.isalnum() or c in " .-_()").strip()


async def is_valid_url(url: str) -> bool:
    async with AsyncClient(timeout=10, follow_redirects=True) as client:
        response = await client.head(url)
        return response.status_code == 200 and "text/html" not in response.headers.get("content-type", "")


async def download_file_with_httpx(download_url: str, destination_dir: str, user_agent: str, referer: str = None):
    headers = {"User-Agent": user_agent}
    if referer:
        headers["Referer"] = referer

    filename = download_url.split("/")[-1].split("?")[0]
    if "." not in filename:
        filename = "video.mp4"

    filename = sanitize_filename(filename)
    file_path = os.path.join(destination_dir, filename)

    async with AsyncClient(headers=headers, follow_redirects=True, timeout=60) as client:
        async with client.stream("GET", download_url) as response:
            if response.status_code != 200:
                await write_debug_log(f"[Download] Gagal: {response.status_code}")
                return None

            with open(file_path, "wb") as f:
                async for chunk in response.aiter_bytes(8192):  # 8KB per chunk
                    f.write(chunk)

            await write_debug_log(f"[Download] Berhasil simpan: {file_path}")
            return file_path


async def scrape_and_download_9xbuddy(video_url: str):
    """
    Ambil link dari 9xbuddy.site dan langsung download ke server
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

        process_url = f"https://9xbuddy.site/process?url={video_url}"

        try:
            await page.goto(process_url, wait_until="networkidle")
            await page.wait_for_timeout(10000)  # Tunggu JS selesai

            links = await page.eval_on_selector_all(
                "a[rel='noreferrer nofollow noopener']",
                "els => els.map(el => el.href)"
            )

            unwanted_patterns = [
                r"facebook\.com/sharer",
                r"twitter\.com/intent",
                r"vk\.com/share\.php"
            ]

            for href in links:
                if any(re.search(p, href) for p in unwanted_patterns):
                    continue
                if ".workers.dev" in href:
                    workers_dev_links.append(href)
                elif ".9xbud.com" in href:
                    ninexbud_links.append(href)
                elif ".video-src.com" in href:
                    video_src_links.append(href)
                else:
                    other_links.append(href)

            candidates = workers_dev_links + ninexbud_links + video_src_links + other_links

            downloaded_path = None
            for candidate in candidates:
                if await is_valid_url(candidate):
                    downloaded_path = await download_file_with_httpx(candidate, DOWNLOAD_DIR, user_agent, referer=process_url)
                    if downloaded_path:
                        break

            return downloaded_path

        except Exception as e:
            await write_debug_log(f"[Scraping] Error: {e}")
            return None
        finally:
            await context.close()
            await browser.close()


async def get_direct_file(video_url: str):
    """
    Hanya kembalikan path file setelah selesai didownload
    Cocok untuk digunakan oleh bot Telegram/mirror bot
    """
    try:
        file_path = await scrape_and_download_9xbuddy(video_url)
        if not file_path:
            await write_debug_log("Tidak ada file berhasil didownload")
            return None
        return file_path
    except Exception as e:
        await write_debug_log(f"Gagal mendapatkan file: {e}")
        return None
