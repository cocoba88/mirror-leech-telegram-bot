import asyncio
import re
import os
import httpx
import hashlib
from playwright.async_api import async_playwright, Page, Request, Response
from urllib.parse import urlparse, unquote
import m3u8_To_MP4
import tempfile
import shutil

# Global variable for the debug log file
debug_log_file = None

def sanitize_filename(filename: str) -> str:
    """Sanitize filename by removing invalid characters"""
    invalid_chars = '<>:"/\\|?*'
    for char in invalid_chars:
        filename = filename.replace(char, '_')
    filename = re.sub(r'\s+', ' ', filename).strip()
    if len(filename) > 150:
        name_part, ext_part = os.path.splitext(filename)
        filename = name_part[:140] + ext_part
    return filename

def extract_filename_from_content_disposition(cd_header: str) -> str | None:
    """Extract filename from Content-Disposition header"""
    try:
        patterns = [
            r'filename\*?=(?:UTF-8\'\')?["\']?([^;"\']+)["\']?',
            r'filename=([^;]+)',
            r'filename\*=UTF-8\'\'([^;]+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, cd_header, re.IGNORECASE)
            if match:
                filename = match.group(1).strip().strip('"\'')
                try:
                    filename = unquote(filename)
                except:
                    pass
                return sanitize_filename(filename)
    except Exception as e:
        print(f"Error extracting filename from Content-Disposition: {e}")
    return None

def determine_file_extension(content_type: str | None, url: str) -> str:
    """Determine appropriate file extension based on content type and URL"""
    parsed_url = urlparse(url)
    url_path = parsed_url.path
    if url_path and '.' in os.path.basename(url_path):
        url_ext = os.path.splitext(url_path)[1].lower()
        if url_ext in ['.mp4', '.mpeg', '.mpg', '.avi', '.mkv', '.mov', '.mp3', '.wav', '.m4a']:
            return url_ext
    if content_type:
        content_type = content_type.lower().split(';')[0].strip()
        if any(video_type in content_type for video_type in ['video/mp4', 'video/mpeg']):
            return '.mp4'
        elif 'video/quicktime' in content_type:
            return '.mov'
        elif 'video/x-msvideo' in content_type:
            return '.avi'
        elif 'video/x-matroska' in content_type:
            return '.mkv'
        elif 'video/' in content_type:
            return '.mp4'
        elif 'audio/mpeg' in content_type:
            return '.mp3'
        elif 'audio/wav' in content_type:
            return '.wav'
        elif 'audio/mp4' in content_type:
            return '.m4a'
        elif 'audio/' in content_type:
            return '.mp3'
    return '.mp4'

async def download_m3u8_direct(url: str, destination_dir: str, user_agent: str) -> str | None:
    """Download M3U8 content directly without FlareSolverr"""
    try:
        parsed_url = urlparse(url)
        filename = os.path.basename(parsed_url.path).replace(".m3u8", ".mp4")
        if not filename or filename == ".mp4":
            filename = f"hls_download_{hashlib.md5(url.encode('utf-8')).hexdigest()}.mp4"
        filename = sanitize_filename(filename)
        output_path = os.path.join(destination_dir, filename)
        m3u8_To_MP4.multithread_download(url, output_path)
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            return output_path
        else:
            return None
    except Exception as e:
        return None

async def download_with_playwright(url: str, destination_dir: str, user_agent: str) -> str | None:
    """Download file using Playwright"""
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(user_agent=user_agent)
            page = await context.new_page()
            download_path = None

            async def handle_download(download):
                nonlocal download_path
                filename = download.suggested_filename
                if not filename:
                    url_hash = hashlib.md5(url.encode("utf-8")).hexdigest()
                    filename = f"playwright_download_{url_hash}.mp4"
                filename = sanitize_filename(filename)
                download_path = os.path.join(destination_dir, filename)
                await download.save_as(download_path)

            page.on("download", handle_download)

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(5)
                download_selectors = [
                    'a[href*="download"]',
                    'button[onclick*="download"]',
                    '.download-btn',
                    '.btn-download',
                    'a[download]',
                    'input[type="submit"][value*="download"]'
                ]
                for selector in download_selectors:
                    elements = await page.query_selector_all(selector)
                    if elements:
                        try:
                            await elements[0].click()
                            await asyncio.sleep(5)
                            break
                        except Exception:
                            continue
                await asyncio.sleep(5)
            except Exception:
                pass
            finally:
                await browser.close()
            if download_path and os.path.exists(download_path) and os.path.getsize(download_path) > 0:
                return download_path
            else:
                return None
    except Exception:
        return None

async def download_file_with_httpx(url: str, destination_dir: str, user_agent: str, referer: str | None = None, progress_callback=None) -> str | None:
    os.makedirs(destination_dir, exist_ok=True)
    headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "cross-site",
        "Sec-Fetch-User": "?1",
    }
    if referer:
        headers["Referer"] = referer
    try:
        timeout = httpx.Timeout(120.0, connect=30.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("GET", url, headers=headers, follow_redirects=True) as response:
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as e:
                    await e.response.aread()
                    if e.response.status_code == 403:
                    return None
                filename = None
                if "content-disposition" in response.headers:
                    cd_header = response.headers["content-disposition"]
                    filename = extract_filename_from_content_disposition(cd_header)
                    if filename:
                if not filename:
                    try:
                        parsed_url = urlparse(url)
                        url_filename = os.path.basename(parsed_url.path)
                        if url_filename and "?" in url_filename:
                            url_filename = url_filename.split("?")[0]
                        if url_filename and url_filename not in ["", ".", "/", "download"]:
                            filename = sanitize_filename(url_filename)
                    except Exception as e:
                if not filename:
                    url_hash = hashlib.md5(url.encode("utf-8")).hexdigest()
                    filename = f"download_{url_hash}"
                content_type = response.headers.get("content-type", "")
                if not os.path.splitext(filename)[1]:
                    extension = determine_file_extension(content_type, url)
                    filename += extension
                filename = sanitize_filename(filename)
                destination_path = os.path.join(destination_dir, filename)
                content_length = response.headers.get("content-length")
                if content_length:
                    total_size = int(content_length)
                else:
                    total_size = 0
                downloaded = 0
                chunk_size = 8192
                with open(destination_path, "wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_callback and total_size > 0:
                            if downloaded % (1024 * 1024) == 0 or downloaded >= total_size:
                                progress = (downloaded / total_size) * 100
                                await progress_callback(downloaded, total_size, progress)
                        elif downloaded % (1024 * 1024) == 0:
                if os.path.exists(destination_path) and os.path.getsize(destination_path) > 0:
                    file_size = os.path.getsize(destination_path)
                    return destination_path
                else:
                    return None
    except httpx.RequestError as e:
        return None
    except Exception as e:
        return None

async def progress_callback(downloaded: int, total: int, progress: float):
    """Progress callback for download tracking"""

def check_duplicate_file(file_path: str, existing_files: list) -> bool:
    """Check if file is duplicate based on size and hash"""
    if not os.path.exists(file_path):
        return False
    file_size = os.path.getsize(file_path)
    for existing_file in existing_files:
        if os.path.exists(existing_file):
            existing_size = os.path.getsize(existing_file)
            if file_size == existing_size:
                return True
    return False

async def download_file_with_fallback(download_url: str, destination_dir: str, user_agent: str, referer: str, existing_files: list = None) -> str | None:
    """Download file with multiple fallback methods and duplicate checking"""
    if existing_files is None:
        existing_files = []
    is_m3u8 = ".m3u8" in download_url or "/hls/" in download_url
    if is_m3u8:
        result = await download_m3u8_direct(download_url, destination_dir, user_agent)
        if result:
            if check_duplicate_file(result, existing_files):
                os.remove(result)
                return None
            return result
    else:
        result = await download_file_with_httpx(download_url, destination_dir, user_agent, referer, progress_callback)
        if result:
            if check_duplicate_file(result, existing_files):
                os.remove(result)
                return None
            return result
    result = await download_with_playwright(download_url, destination_dir, user_agent)
    if result:
        if check_duplicate_file(result, existing_files):
            os.remove(result)
            return None
        return result
    return None

async def scrape_and_download_9xbuddy(video_url: str):
    extracted_download_urls = []
    downloaded_file_paths = []
    async with async_playwright() as p:
        user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=user_agent)
        page = await context.new_page()
        captured_requests = []
        captured_responses = []

        def request_handler(request: Request):
            captured_requests.append(request)

        def response_handler(response: Response):
            captured_responses.append(response)

        page.on("request", request_handler)
        page.on("response", response_handler)
        context.on("page", lambda popup_page: asyncio.create_task(handle_popup(popup_page)))

        process_url = f"https://9xbuddy.site/process?url={video_url}"
        try:
            await page.goto(process_url, wait_until="domcontentloaded")
            await page.wait_for_selector("main#root section.w-full.max-w-4xl div.mb-4.mt-8.text-center", timeout=30000)
            await asyncio.sleep(5)
            html_content = await page.content()
            download_button_selector = "a.btn.btn-success.btn-lg.w-full.mt-4"

            if await page.query_selector(download_button_selector):
                try:
                    async with page.expect_download() as download_info:
                        await page.click(download_button_selector)
                    download = await download_info.value
                    suggested_filename = download.suggested_filename or f"playwright_download_{hashlib.md5(video_url.encode()).hexdigest()}.mp4"
                    suggested_filename = sanitize_filename(suggested_filename)
                    download_path = os.path.join("/root/Tera/downloads", suggested_filename)
                    await download.save_as(download_path)
                    downloaded_file_paths.append(download_path)
                except Exception as e:
                    pass
            else:
                try:
                    unwanted_patterns = [
                        r"facebook\\.com/sharer",
                        r"twitter\\.com/intent",
                        r"vk\\.com/share\\.php",
                        r"//9xbud\\.com/https://",
                        r"offmp3\\.net/process",
                        r"savegif\\.com/process",
                        r"123sudo\\.com",
                        r"/process\\?url=https://vstream\\.id/embed/"
                    ]
                    all_potential_download_links = await page.query_selector_all("main#root a[rel=\"noreferrer nofollow noopener\"]")
                    mp4_available = False
                    mpeg_available = False
                    format_selectors = [
                        "#root > section > section.w-full.max-w-4xl.my-2.mx-auto > section.py-3.lg\\:py-6.px-4 > div.mb-4.mt-8.text-center > div:nth-child(2) > div.w-full.lg\\:w-2\\/3.flex.justify-center.items-center > div.w-24.sm\\:w-1\\/3.lg\\:w-24.text-blue-500.uppercase",
                        "div.w-24.sm\\:w-1\\/3.lg\\:w-24.text-blue-500.uppercase"
                    ]
                    for selector in format_selectors:
                        try:
                            format_elements = await page.query_selector_all(selector)
                            for element in format_elements:
                                text = await element.text_content()
                                if text:
                                    text = text.lower().strip()
                                    if text == "mp4":
                                        mp4_available = True
                                    elif text == "mpeg":
                                        mpeg_available = True
                        except Exception as e:
                            pass

                    resolution_480_found = False
                    if mp4_available or mpeg_available:
                        resolution_selectors = [
                            "#root > section > section.w-full.max-w-4xl.my-2.mx-auto > section.py-3.lg\\:py-6.px-4 > div.mb-4.mt-8.text-center > div:nth-child(2) > div.w-full.lg\\:w-2\\/3.flex.justify-center.items-center > div.w-1\\/2.sm\\:w-1\\/3.lg\\:w-1\\/2.truncate",
                            "div.w-1\\/2.sm\\:w-1\\/3.lg\\:w-1\\/2.truncate",
                            "div.truncate"
                        ]
                        for selector in resolution_selectors:
                            try:
                                resolution_elements = await page.query_selector_all(selector)
                                for element in resolution_elements:
                                    text = await element.text_content()
                                    if text and "480" in text:
                                        resolution_480_found = True
                                        break
                                if resolution_480_found:
                                    break
                            except Exception as e:
                                pass

                    workers_dev_links = []
                    ninexbud_links = []
                    other_links = []
                    resolution_480_links = []
                    for element in all_potential_download_links:
                        href = await element.get_attribute("href")
                        if href:
                            is_unwanted = False
                            for pattern in unwanted_patterns:
                                if re.search(pattern, href):
                                    is_unwanted = True
                                    break
                            if not is_unwanted:
                                try:
                                    parent_div = await element.query_selector("xpath=..")
                                    if parent_div:
                                        parent_text = await parent_div.text_content()
                                        if parent_text and "480" in parent_text:
                                            resolution_480_links.append(href)
                                            continue
                                except Exception as e:
                                    pass
                                if ".workers.dev" in href:
                                    workers_dev_links.append(href)
                                elif ".9xbud.com" in href:
                                    ninexbud_links.append(href)
                                else:
                                    other_links.append(href)

                    if resolution_480_links:
                        extracted_download_urls = resolution_480_links
                    elif workers_dev_links:
                        extracted_download_urls = workers_dev_links[:1]
                    elif ninexbud_links:
                        extracted_download_urls = ninexbud_links[:1]
                    else:
                        extracted_download_urls = other_links[:1]

                    for link in extracted_download_urls:
                        pass

                except Exception as e:
                    pass

        except Exception as e:
            pass
        finally:
            await browser.close()

    if extracted_download_urls:
        for i, download_url in enumerate(extracted_download_urls):
            downloaded_path = await download_file_with_fallback(
                download_url,
                "/root/Tera/downloads",
                user_agent,
                process_url,
                downloaded_file_paths
            )
            if downloaded_path:
                downloaded_file_paths.append(download_path)
                break
            else:
                pass
    else:
        pass

    for file_path in downloaded_file_paths:
        if os.path.exists(file_path):
            file_size = os.path.getsize(file_path)
        else:
            pass

    return extracted_download_urls, downloaded_file_paths

async def handle_popup(popup_page: Page):
    try:
        await popup_page.wait_for_load_state("domcontentloaded", timeout=10000)
        await popup_page.close()
    except Exception as e:

async def get_direct_file(video_url: str):
    """Get local file path after downloading in format expected by direct_downloader.py"""
    try:
        extracted_urls, downloaded_files = await scrape_and_download_9xbuddy(video_url)
        if downloaded_files and os.path.exists(downloaded_files[0]):
            file_path = downloaded_files[0]
            file_size = os.path.getsize(file_path)
            filename = os.path.basename(file_path)
            return {
                "contents": [file_path],
                "total_size": file_size,
                "title": filename
            }
        raise Exception("Failed to download file")
    except Exception as e:
        raise
