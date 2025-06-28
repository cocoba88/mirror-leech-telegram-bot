import asyncio
import os
import re
import hashlib
from urllib.parse import urlparse
from pathlib import Path
from httpx import AsyncClient
from playwright.async_api import async_playwright, Page, Request, Response, ConsoleMessage


async def write_debug_log(message: str):
    print(f"[xbuddy] {message}")


def sanitize_filename(name: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1F]', "", name).strip()


def extract_filename_from_content_disposition(content_disposition: str) -> str:
    if "filename=" in content_disposition:
        filename = content_disposition.split("filename=")[-1].strip("\"'")
        return sanitize_filename(filename)
    return ""


async def is_valid_url(url: str) -> bool:
    try:
        async with AsyncClient(follow_redirects=True, timeout=10) as client:
            response = await client.head(url)
            content_type = response.headers.get("content-type", "")
            return response.status_code == 200 and "text/html" not in content_type
    except Exception:
        return False


async def download_file_with_httpx(download_url: str, destination_dir: str, user_agent: str, referer: str = None, progress_callback=None):
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
                    filename = extract_filename_from_content_disposition(cd_header)

                if not filename:
                    parsed_url = urlparse(download_url)
                    url_path = parsed_url.path.strip("/")
                    if "?" in url_path:
                        url_path = url_path.split("?")[0]
                    filename = url_path.split("/")[-1] or "video.mp4"
                    filename = sanitize_filename(filename)

                file_path = os.path.join(destination_dir, filename)

                with open(file_path, "wb") as f:
                    async for chunk in response.aiter_bytes(8192):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_callback:
                            await progress_callback(downloaded, content_length)

                await write_debug_log(f"[HTTPX Download] Berhasil simpan: {file_path}")
                return file_path
    except Exception as e:
        await write_debug_log(f"[HTTPX Download] Error: {e}")
        return None


async def download_with_playwright(download_url: str, destination_dir: str, user_agent: str):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_extra_http_headers({"User-Agent": user_agent})

        try:
            await page.goto(download_url, wait_until="domcontentloaded")
            await page.wait_for_event("download")
            download = await page.wait_for_download()

            path = await download.path()
            file_path = os.path.join(destination_dir, await download.filename())
            await download.save_as(file_path)

            await write_debug_log(f"[Playwright Download] Berhasil simpan: {file_path}")
            return file_path
        except Exception as e:
            await write_debug_log(f"[Playwright Download] Error: {e}")
            return None
        finally:
            await browser.close()


async def handle_popup(popup_page):
    await popup_page.wait_for_load_state("domcontentloaded")


async def scrape_and_download_9xbuddy(video_url: str):
    extracted_download_urls = []
    downloaded_file_paths = []

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
        context.on("page", handle_popup)

        process_url = f"https://9xbuddy.site/process?url={video_url}"
        await write_debug_log(f"[Scraping] Navigating to: {process_url}")

        try:
            await page.goto(process_url, wait_until="domcontentloaded")
            await page.wait_for_selector("a[rel=\"noreferrer nofollow noopener\"]", timeout=30000)

            unwanted_patterns = [
                r"facebook\.com/sharer",
                r"twitter\.com/intent",
                r"vk\.com/share\.php",
                r"//9xbud\.com/https://",
                r"offmp3\.net/process",
                r"savegif\.com/process",
                r"123sudo\.com",
                r"/process\?url=https://vstream\.id/embed/"
            ]

            all_potential_download_links = await page.query_selector_all(
                "main#root a[rel=\"noreferrer nofollow noopener\"]"
            )

            workers_dev_links = []
            ninexbud_links = []
            video_src_links = []
            other_links = []

            for element in all_potential_download_links:
                href = await element.get_attribute("href")
                if href:
                    is_unwanted = False
                    for pattern in unwanted_patterns:
                        if re.search(pattern, href):
                            is_unwanted = True
                            break
                    if not is_unwanted:
                        if ".workers.dev" in href:
                            workers_dev_links.append(href)
                        elif ".9xbud.com" in href:
                            ninexbud_links.append(href)
                        elif ".video-src.com" in href:
                            video_src_links.append(href)
                        else:
                            other_links.append(href)

            # Prioritas: workers.dev > 9xbud.com > video-src.com > lainnya
            candidates = workers_dev_links + ninexbud_links + video_src_links + other_links

            for candidate in candidates:
                if await is_valid_url(candidate):
                    extracted_download_urls.append(candidate)
                    break  # Hanya ambil satu yang pertama valid

            # Jika tidak ada yang valid, coba semua link
            if not extracted_download_urls:
                extracted_download_urls.extend(candidates)

            # Simpan file jika dibutuhkan
            if extracted_download_urls:
                download_path = "/root/scrape/download"
                for download_url in extracted_download_urls:
                    result = await download_file_with_httpx(download_url, download_path, user_agent, referer=process_url, progress_callback=None)
                    if result:
                        downloaded_file_paths.append(result)

        except Exception as e:
            await write_debug_log(f"[Scraping] Error: {e}")
        finally:
            await context.close()
            await browser.close()

    return extracted_download_urls, downloaded_file_paths


async def get_direct_url(video_url: str):
    """
    Ambil satu URL video langsung dari hasil scraping
    """
    extracted_urls = []

    try:
        _, extracted_urls = await scrape_and_download_9xbuddy(video_url)
    except Exception as e:
        await write_debug_log(f"[get_direct_url] Error: {e}")

    return extracted_urls[0] if extracted_urls else None


if __name__ == "__main__":
    test_url = " https://videq.stream/d/k1crs1xbltqm "
    asyncio.run(scrape_and_download_9xbuddy(test_url))
