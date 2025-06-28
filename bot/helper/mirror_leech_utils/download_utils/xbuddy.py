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

async def write_debug_log(message: str):
    global debug_log_file
    if debug_log_file is None:
        debug_log_file = open("/root/Tera/logs/debug_log.txt", "a", encoding="utf-8")
    debug_log_file.write(message + "\n")
    debug_log_file.flush()
    print(message)

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
        await write_debug_log(f"[M3U8 Direct] Attempting direct M3U8 download to: {output_path}")
        m3u8_To_MP4.multithread_download(url, output_path)
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            await write_debug_log(f"[M3U8 Direct] Successfully downloaded HLS video to: {output_path}")
            return output_path
        else:
            await write_debug_log(f"[M3U8 Direct] Download completed but file is missing or empty")
            return None
    except Exception as e:
        await write_debug_log(f"[M3U8 Error] Failed to download M3U8 from {url}: {e}")
        return None

async def download_with_playwright(url: str, destination_dir: str, user_agent: str) -> str | None:
    """Download file using Playwright"""
    try:
        await write_debug_log(f"[Playwright Download] Attempting download with Playwright: {url}")
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
                await write_debug_log(f"[Playwright Download] Downloaded: {download_path}")
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
                        await write_debug_log(f"[Playwright Download] Found download element with selector: {selector}")
                        try:
                            await elements[0].click()
                            await asyncio.sleep(5)
                            break
                        except Exception as e:
                            await write_debug_log(f"[Playwright Download] Failed to click element: {e}")
                            continue
                await asyncio.sleep(5)
            except Exception as e:
                await write_debug_log(f"[Playwright Download] Error during page navigation: {e}")
            await browser.close()
            if download_path and os.path.exists(download_path) and os.path.getsize(download_path) > 0:
                return download_path
            else:
                await write_debug_log(f"[Playwright Download] Download failed or file is empty")
                return None
    except Exception as e:
        await write_debug_log(f"[Playwright Download] Error: {e}")
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
        await write_debug_log(f"[HTTPX Download] Attempting direct download of: {url}")
        timeout = httpx.Timeout(120.0, connect=30.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("GET", url, headers=headers, follow_redirects=True) as response:
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as e:
                    await e.response.aread()
                    await write_debug_log(f"[HTTPX Download] HTTP error during download from {url}: {e.response.status_code} - {e.response.text[:100]}...")
                    if e.response.status_code == 403:
                        await write_debug_log("[HTTPX Download] This is likely a Cloudflare/anti-bot block.")
                    return None
                filename = None
                if "content-disposition" in response.headers:
                    cd_header = response.headers["content-disposition"]
                    await write_debug_log(f"[HTTPX Download] Content-Disposition header: {cd_header}")
                    filename = extract_filename_from_content_disposition(cd_header)
                    if filename:
                        await write_debug_log(f"[HTTPX Download] Extracted filename from Content-Disposition: {filename}")
                if not filename:
                    try:
                        parsed_url = urlparse(url)
                        url_filename = os.path.basename(parsed_url.path)
                        if url_filename and "?" in url_filename:
                            url_filename = url_filename.split("?")[0]
                        if url_filename and url_filename not in ["", ".", "/", "download"]:
                            filename = sanitize_filename(url_filename)
                            await write_debug_log(f"[HTTPX Download] Using URL-based filename: {filename}")
                    except Exception as e:
                        await write_debug_log(f"[HTTPX Download] Error extracting filename from URL: {e}")
                if not filename:
                    url_hash = hashlib.md5(url.encode("utf-8")).hexdigest()
                    filename = f"download_{url_hash}"
                    await write_debug_log(f"[HTTPX Download] Generated hash-based filename: {filename}")
                content_type = response.headers.get("content-type", "")
                await write_debug_log(f"[HTTPX Download] Content-Type: {content_type}")
                if not os.path.splitext(filename)[1]:
                    extension = determine_file_extension(content_type, url)
                    filename += extension
                    await write_debug_log(f"[HTTPX Download] Added extension: {extension}")
                filename = sanitize_filename(filename)
                destination_path = os.path.join(destination_dir, filename)
                await write_debug_log(f"[HTTPX Download] Final destination path: {destination_path}")
                content_length = response.headers.get("content-length")
                if content_length:
                    total_size = int(content_length)
                    await write_debug_log(f"[HTTPX Download] File size: {total_size} bytes ({total_size/1024/1024:.2f} MB)")
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
                            await write_debug_log(f"[HTTPX Download] Downloaded: {downloaded/1024/1024:.2f} MB")
                if os.path.exists(destination_path) and os.path.getsize(destination_path) > 0:
                    file_size = os.path.getsize(destination_path)
                    await write_debug_log(f"[HTTPX Download] Successfully downloaded: {filename} ({file_size} bytes) to {destination_path}")
                    return destination_path
                else:
                    await write_debug_log(f"[HTTPX Download] Download failed - file missing or empty")
                    return None
    except httpx.RequestError as e:
        await write_debug_log(f"[HTTPX Download] Network error during download from {url}: {e}")
        return None
    except Exception as e:
        await write_debug_log(f"[HTTPX Download] An unexpected error occurred during download from {url}: {e}")
        return None

async def progress_callback(downloaded: int, total: int, progress: float):
    """Progress callback for download tracking"""
    await write_debug_log(f"[Progress] {downloaded/1024/1024:.2f}/{total/1024/1024:.2f} MB ({progress:.1f}%)")

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
    await write_debug_log(f"[Fallback Download] Starting download with fallback methods for: {download_url}")
    is_m3u8 = ".m3u8" in download_url or "/hls/" in download_url
    await write_debug_log(f"[Fallback Download] Method 1: Direct download")
    if is_m3u8:
        result = await download_m3u8_direct(download_url, destination_dir, user_agent)
        if result:
            if check_duplicate_file(result, existing_files):
                await write_debug_log(f"[Fallback Download] Duplicate file detected, removing: {result}")
                os.remove(result)
                return None
            return result
    else:
        result = await download_file_with_httpx(download_url, destination_dir, user_agent, referer, progress_callback)
        if result:
            if check_duplicate_file(result, existing_files):
                await write_debug_log(f"[Fallback Download] Duplicate file detected, removing: {result}")
                os.remove(result)
                return None
            return result
    await write_debug_log(f"[Fallback Download] Method 2: Using Playwright download")
    result = await download_with_playwright(download_url, destination_dir, user_agent)
    if result:
        if check_duplicate_file(result, existing_files):
            await write_debug_log(f"[Fallback Download] Duplicate file detected, removing: {result}")
            os.remove(result)
            return None
        return result
    await write_debug_log(f"[Fallback Download] All methods failed for: {download_url}")
    return None

async def scrape_and_download_9xbuddy(video_url: str):
    extracted_download_urls = []
    downloaded_file_paths = []
    async with async_playwright() as p:
        user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=user_agent)
        page = await context.new_page()
        page.on("console", lambda msg: asyncio.create_task(write_debug_log(f"[Console {msg.type.upper()}] {msg.text}")))
        page.on("pageerror", lambda err: asyncio.create_task(write_debug_log(f"[Page Error] {err}")))
        captured_requests = []
        captured_responses = []
        def request_handler(request: Request):
            captured_requests.append(request)
            asyncio.create_task(write_debug_log(f"[Request] {request.method} {request.url}"))
        def response_handler(response: Response):
            captured_responses.append(response)
            asyncio.create_task(write_debug_log(f"[Response] {response.status} {response.url}"))
        page.on("request", request_handler)
        page.on("response", response_handler)
        context.on("page", lambda popup_page: asyncio.create_task(handle_popup(popup_page)))
        process_url = f"https://9xbuddy.site/process?url={video_url}"
        await write_debug_log(f"[Scraping] Navigating to: {process_url} with User-Agent: {user_agent}")
        try:
            await write_debug_log("[Scraping] Navigating directly to 9xbuddy.")
            await page.goto(process_url, wait_until="domcontentloaded")
            await write_debug_log("[Scraping] Waiting for dynamic content to load...")
            await page.wait_for_selector("main#root section.w-full.max-w-4xl div.mb-4.mt-8.text-center", timeout=30000)
            await asyncio.sleep(5)
            html_content = await page.content()
            await write_debug_log(f"\n--- HTML Content for {process_url} ---\n{html_content}\n--- End HTML Content ---\n")
            download_button_selector = "a.btn.btn-success.btn-lg.w-full.mt-4"
            if await page.query_selector(download_button_selector):
                await write_debug_log("[Scraping] Found download button, attempting to click...")
                try:
                    async with page.expect_download() as download_info:
                        await page.click(download_button_selector)
                    download = await download_info.value
                    suggested_filename = download.suggested_filename or f"playwright_download_{hashlib.md5(video_url.encode()).hexdigest()}.mp4"
                    suggested_filename = sanitize_filename(suggested_filename)
                    download_path = os.path.join("/root/Tera/downloads", suggested_filename)
                    await download.save_as(download_path)
                    downloaded_file_paths.append(download_path)
                    await write_debug_log(f"[Scraping] Downloaded file using Playwright: {download_path}")
                except Exception as e:
                    await write_debug_log(f"[Scraping] Failed to download using Playwright button: {e}")
            else:
                await write_debug_log("[Scraping] No direct download button found, proceeding with link extraction.")
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
                                await write_debug_log(f"[Format Detection] MP4 format available")
                            elif text == "mpeg":
                                mpeg_available = True
                                await write_debug_log(f"[Format Detection] MPEG format available")
                except Exception as e:
                    await write_debug_log(f"[Format Detection] Error checking format with selector {selector}: {e}")
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
                                await write_debug_log(f"[Resolution Detection] 480p resolution found: {text}")
                                break
                        if resolution_480_found:
                            break
                    except Exception as e:
                        await write_debug_log(f"[Resolution Detection] Error checking resolution with selector {selector}: {e}")
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
                                    await write_debug_log(f"[Link Filtering] Found 480p link: {href}")
                                    continue
                        except Exception as e:
                            await write_debug_log(f"[Link Filtering] Error checking parent for 480p: {e}")
                        if ".workers.dev" in href:
                            workers_dev_links.append(href)
                            await write_debug_log(f"[Link Filtering] Found workers.dev link (PRIORITY): {href}")
                        elif ".9xbud.com" in href:
                            ninexbud_links.append(href)
                            await write_debug_log(f"[Link Filtering] Found 9xbud.com link: {href}")
                        else:
                            other_links.append(href)
                            await write_debug_log(f"[Link Filtering] Found other link: {href}")
            if resolution_480_links:
                extracted_download_urls = resolution_480_links
                await write_debug_log(f"[Link Filtering] Using 480p resolution links: {len(resolution_480_links)} found")
            elif workers_dev_links:
                extracted_download_urls = workers_dev_links[:1]
                await write_debug_log(f"[Link Filtering] Using workers.dev links (LIMITED TO 1): {len(extracted_download_urls)} selected")
            elif ninexbud_links:
                extracted_download_urls = ninexbud_links[:1]
                await write_debug_log(f"[Link Filtering] Using 9xbud.com links (LIMITED TO 1): {len(extracted_download_urls)} selected")
            else:
                extracted_download_urls = other_links[:1]
                await write_debug_log(f"[Link Filtering] Using other links (LIMITED TO 1): {len(extracted_download_urls)} selected")
            await write_debug_log(f"[Scraping] Found {len(extracted_download_urls)} relevant download URLs:")
            for link in extracted_download_urls:
                await write_debug_log(f"[Scraping] - {link}")
        except Exception as e:
            await write_debug_log(f"[Scraping] An error occurred during scraping or navigating: {e}")
        finally:
            await browser.close()
            await write_debug_log("[Scraping] Playwright browser closed.")
    if extracted_download_urls:
        await write_debug_log("\n[Download] Starting download process with fallback methods...")
        for i, download_url in enumerate(extracted_download_urls):
            await write_debug_log(f"[Download] Processing download link {i+1}/{len(extracted_download_urls)}: {download_url}")
            downloaded_path = await download_file_with_fallback(
                download_url, 
                "/root/Tera/downloads", 
                user_agent, 
                process_url,
                downloaded_file_paths
            )
            if downloaded_path:
                downloaded_file_paths.append(downloaded_path)
                await write_debug_log(f"[Download] Successfully downloaded: {downloaded_path}")
                await write_debug_log(f"[Download] First video downloaded successfully. Stopping further downloads to avoid duplicates.")
                break
            else:
                await write_debug_log(f"[Download] Failed to download from: {download_url}")
    else:
        await write_debug_log("[Download] No valid download links found to attempt download.")
    await write_debug_log("\n[Final Verification] Checking downloaded files...")
    for file_path in downloaded_file_paths:
        if os.path.exists(file_path):
            file_size = os.path.getsize(file_path)
            await write_debug_log(f"[Final Verification] ? {file_path} exists ({file_size} bytes)")
        else:
            await write_debug_log(f"[Final Verification] ? {file_path} does not exist!")
    return extracted_download_urls, downloaded_file_paths

async def handle_popup(popup_page: Page):
    await write_debug_log(f"[Popup Detected] New page opened: {popup_page.url}")
    try:
        await popup_page.wait_for_load_state("domcontentloaded", timeout=10000)
        await write_debug_log(f"[Popup Loaded] URL: {popup_page.url}")
        await popup_page.close()
        await write_debug_log("[Popup Closed]")
    except Exception as e:
        await write_debug_log(f"[Popup Error] Could not load or close popup: {e}")

async def get_direct_file(video_url: str):
    """Get local file path after downloading in format expected by direct_downloader.py"""
    try:
        extracted_urls, downloaded_files = await scrape_and_download_9xbuddy(video_url)
        if downloaded_files and os.path.exists(downloaded_files[0]):
            file_path = downloaded_files[0]
            file_size = os.path.getsize(file_path)
            filename = os.path.basename(file_path)
            await write_debug_log(f"[get_direct_file] File downloaded successfully: {file_path} ({file_size} bytes)")
            return {
                "contents": [file_path],
                "total_size": file_size,
                "title": filename
            }
        await write_debug_log(f"[get_direct_file] No valid files downloaded for {video_url}")
        raise Exception("Failed to download file")
    except Exception as e:
        await write_debug_log(f"[get_direct_file] Error: {str(e)}")
        raise
