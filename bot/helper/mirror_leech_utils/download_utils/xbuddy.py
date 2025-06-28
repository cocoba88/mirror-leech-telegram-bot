import asyncio
import re
import os
import httpx
import hashlib
from urllib.parse import urlparse, unquote
from pathlib import Path
from playwright.async_api import async_playwright, Page, Request, Response, ConsoleMessage
import m3u8_To_MP4  # Pastikan sudah install: pip install m3u8-To-MP4
import tempfile
import shutil


# Global variable untuk logging
debug_log_file = None


async def write_debug_log(message: str):
    global debug_log_file
    if debug_log_file is None:
        debug_log_file = open("/root/Tera/debug_log.txt", "a", encoding="utf-8")
    debug_log_file.write(message + "\n")
    debug_log_file.flush()
    print(f"[xbuddy] {message}")


def sanitize_filename(filename: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1F]', "", filename).strip() or "video.mp4"


def extract_filename_from_content_disposition(content_disposition: str) -> str:
    if "filename=" in content_disposition:
        filename = content_disposition.split("filename=")[-1].strip("\"'")
        return sanitize_filename(filename)
    return ""


async def is_valid_url(url: str) -> bool:
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10) as client:
            response = await client.head(url)
            content_type = response.headers.get("content-type", "")
            return response.status_code == 200 and "text/html" not in content_type
    except Exception:
        return False


async def download_file_with_httpx(download_url: str, destination_dir: str, user_agent: str, referer: str = None, progress_callback=None):
    headers = {"User-Agent": user_agent}
    if referer:
        headers["Referer"] = referer

    try:
        async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=60) as client:
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

                if not filename.endswith(".mp4"):
                    filename += ".mp4"
                filename = sanitize_filename(filename)

                file_path = Path(destination_dir) / filename

                if file_path.exists():
                    await write_debug_log(f"[Download] File sudah ada: {file_path}")
                    return str(file_path)

                with open(file_path, "wb") as f:
                    async for chunk in response.aiter_bytes(8192):  # 8KB per chunk
                        f.write(chunk)
                        downloaded += len(chunk)
                        percent = downloaded / content_length * 100 if content_length else 0
                        await write_debug_log(f"[Progress] {downloaded}/{content_length} bytes ({percent:.2f}%))")

                await write_debug_log(f"[HTTPX Download] Berhasil simpan: {file_path}")
                return str(file_path)

    except Exception as e:
        await write_debug_log(f"[HTTPX Download] Error: {e}")
        return None


async def handle_popup(popup_page):
    await popup_page.wait_for_load_state("domcontentloaded")


async def scrape_and_download_9xbuddy(video_url: str):
    """
    Scraping halaman 9xbuddy.site dan langsung download ke server.
    Prioritas: .workers.dev > .9xbud.com > lainnya
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
        context.on("page", handle_popup)

        process_url = f"https://9xbuddy.site/process?url={video_url}"
        await write_debug_log(f"[Scraping] Navigating to: {process_url}")

        try:
            await page.goto(process_url, wait_until="networkidle")
            await page.wait_for_timeout(10000)

            links = await page.eval_on_selector_all(
                "a[rel='noreferrer nofollow noopener']",
                "els => els.map(el => el.href)"
            )

            unwanted_patterns = [
                r"facebook\.com/sharer",
                r"twitter\.com/intent",
                r"vk\.com/share\.php",
                r"offmp3\.net/process",
                r"savegif\.com/process",
                r"123sudo\.com",
            ]

            for href in links:
                is_unwanted = any(re.search(pattern, href) for pattern in unwanted_patterns)
                if not is_unwanted:
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
                    downloaded_path = await download_file_with_httpx(candidate, "/root/Tera/downloads", user_agent, referer=process_url)
                    if downloaded_path:
                        break

            if not downloaded_path and candidates:
                downloaded_path = await download_file_with_httpx(candidates[0], "/root/Tera/downloads", user_agent, referer=process_url)

            return downloaded_path

        except Exception as e:
            await write_debug_log(f"[Scraping] Error saat scraping: {e}")
            return None
        finally:
            await context.close()
            await browser.close()


async def get_direct_file(video_url: str):
    """
    Ambil path file lokal setelah selesai didownload.
    Cocok digunakan oleh bot Telegram/mirror bot
    """
    try:
        file_path = await scrape_and_download_9xbuddy(video_url)
        if not file_path:
            raise Exception("Gagal download dari xbuddy.py")
        return file_path
    except Exception as e:
        await write_debug_log(f"Gagal mendapatkan file: {e}")
        return None
