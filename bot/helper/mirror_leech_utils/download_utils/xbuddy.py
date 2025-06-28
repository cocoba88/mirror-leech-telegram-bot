import asyncio
import re
import os
import httpx
import hashlib
from urllib.parse import urlparse, unquote
from pathlib import Path
from playwright.async_api import async_playwright, Page, Request, Response, ConsoleMessage
import m3u8_To_MP4

# Global variable untuk logging
debug_log_file = None

def write_debug_log_sync(message: str):
    """Versi sinkronus dari write_debug_log untuk fungsi non-async"""
    global debug_log_file
    if debug_log_file is None:
        debug_log_file = open("/root/Tera/debug_log.txt", "a", encoding="utf-8")
    debug_log_file.write(message + "\n")
    debug_log_file.flush()
    print(f"[xbuddy] {message}")

async def write_debug_log(message: str):
    """Versi asinkronus untuk fungsi async"""
    write_debug_log_sync(message)

def sanitize_filename(filename: str) -> str:
    """Sanitize filename by removing invalid characters"""
    invalid_chars = '<>:"/\\|?*'
    for char in invalid_chars:
        filename = filename.replace(char, '_')
    filename = re.sub(r'\s+', ' ', filename).strip()
    if len(filename) > 150:
        name_part, ext_part = os.path.splitext(filename)
        filename = name_part[:140] + ext_part
    return filename or "video.mp4"

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
        write_debug_log_sync(f"[Filename Extraction] Error: {e}")  # Gunakan versi sinkronus
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
        elif 'audio/mpeg' in content_type:
            return '.mp3'
        elif 'audio/wav' in content_type:
            return '.wav'
        elif 'audio/mp4' in content_type:
            return '.m4a'
    return '.mp4'

async def download_m3u8_direct(url: str, destination_dir: str, user_agent: str) -> str | None:
    """Download M3U8 content directly"""
    try:
        parsed_url = urlparse(url)
        filename = os.path.basename(parsed_url.path).replace(".m3u8", ".mp4")
        if not filename or filename == ".mp4":
            filename = f"hls_download_{hashlib.md5(url.encode('utf-8')).hexdigest()}.mp4"
        filename = sanitize_filename(filename)
        output_path = os.path.join(destination_dir, filename)
        await write_debug_log(f"[M3U8 Direct] Attempting M3U8 download to: {output_path}")
        m3u8_To_MP4.multithread_download(url, output_path)
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            await write_debug_log(f"[M3U8 Direct] Successfully downloaded: {output_path}")
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
        await write_debug_log(f"[Playwright Download] Attempting download: {url}")
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(user_agent=user_agent)
            page = await context.new_page()
            download_path = None
            async def handle_download(download):
                nonlocal download_path
                filename = download.suggested_filename or f"playwright_download_{hashlib.md5(url.encode('utf-8')).hexdigest()}.mp4"
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
                        await write_debug_log(f"[Playwright Download] Found download element: {selector}")
                        try:
                            await elements[0].click()
                            await asyncio.sleep(5)
                            break
                        except Exception as e:
                            await write_debug_log(f"[Playwright Download] Failed to click element: {e}")
                            continue
                await asyncio.sleep(5)
            except Exception as e:
                await write_debug_log(f"[Playwright Download] Error during navigation: {e}")
            await browser.close()
            if download_path and os.path.exists(download_path) and os.path.getsize(download_path) > 0:
                return download_path
            else:
                await write_debug_log(f"[Playwright Download] Download failed or file is empty")
                return None
    except Exception as e:
        await write_debug_log(f"[Playwright Download] Error: {e}")
        return None

async def download_file_with_httpx(url: str, destination_dir: str, user_agent: str, referer: str | None = None) -> str | None:
    """Download file using httpx"""
    os.makedirs(destination_dir, exist_ok=True)
    headers = {
        "User-Agent": user_agent,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
    }
    if referer:
        headers["Referer"] = referer
    try:
        await write_debug_log(f"[HTTPX Download] Attempting download: {url}")
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=30.0)) as client:
            async with client.stream("GET", url, headers=headers, follow_redirects=True) as response:
                response.raise_for_status()
                filename = None
                if "content-disposition" in response.headers:
                    filename = extract_filename_from_content_disposition(response.headers["content-disposition"])
                    await write_debug_log(f"[HTTPX Download] Extracted filename: {filename}")
                if not filename:
                    parsed_url = urlparse(url)
                    url_filename = os.path.basename(parsed_url.path).split("?")[0]
                    if url_filename and url_filename not in ["", ".", "/", "download"]:
                        filename = sanitize_filename(url_filename)
                        await write_debug_log(f"[HTTPX Download] Using URL-based filename: {filename}")
                if not filename:
                    filename = f"download_{hashlib.md5(url.encode('utf-8')).hexdigest()}"
                    await write_debug_log(f"[HTTPX Download] Generated hash-based filename: {filename}")
                if not os.path.splitext(filename)[1]:
                    content_type = response.headers.get("content-type", "")
                    extension = determine_file_extension(content_type, url)
                    filename += extension
                    await write_debug_log(f"[HTTPX Download] Added extension: {extension}")
                filename = sanitize_filename(filename)
                destination_path = os.path.join(destination_dir, filename)
                content_length = response.headers.get("content-length")
                total_size = int(content_length) if content_length else 0
                downloaded = 0
                with open(destination_path, "wb") as f:
                    async for chunk in response.aiter_bytes(8192):
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total_size > 0 and downloaded % (1024 * 1024) == 0:
                            progress = (downloaded / total_size) * 100
                            await write_debug_log(f"[HTTPX Download] Progress: {downloaded/1024/1024:.2f}/{total_size/1024/1024:.2f} MB ({progress:.1f}%)")
                if os.path.exists(destination_path) and os.path.getsize(destination_path) > 0:
                    file_size = os.path.getsize(destination_path)
                    await write_debug_log(f"[HTTPX Download] Successfully downloaded: {destination_path} ({file_size} bytes)")
                    return destination_path
                else:
                    await write_debug_log(f"[HTTPX Download] Download failed - file missing or empty")
                    return None
    except httpx.HTTPStatusError as e:
        await write_debug_log(f"[HTTPX Download] HTTP error: {e.response.status_code}")
        return None
    except Exception as e:
        await write_debug_log(f"[HTTPX Download] Error: {e}")
        return None

async def check_duplicate_file(file_path: str, existing_files: list) -> bool:
    """Check if file is duplicate based on size"""
    if not os.path.exists(file_path):
        return False
    file_size = os.path.getsize(file_path)
    for existing_file in existing_files:
        if os.path.exists(existing_file) and os.path.getsize(existing_file) == file_size:
            return True
    return False

async def download_file_with_fallback(download_url: str, destination_dir: str, user_agent: str, referer: str, existing_files: list = None) -> str | None:
    """Download file with fallback methods"""
    if existing_files is None:
        existing_files = []
    await write_debug_log(f"[Fallback Download] Starting for: {download_url}")
    is_m3u8 = ".m3u8" in download_url or "/hls/" in download_url
    if is_m3u8:
        await write_debug_log(f"[Fallback Download] Attempting M3U8 download")
        result = await download_m3u8_direct(download_url, destination_dir, user_agent)
        if result and not check_duplicate_file(result, existing_files):
            return result
    await write_debug_log(f"[Fallback Download] Attempting HTTPX download")
    result = await download_file_with_httpx(download_url, destination_dir, user_agent, referer)
    if result and not check_duplicate_file(result, existing_files):
        return result
    await write_debug_log(f"[Fallback Download] Attempting Playwright download")
    result = await download_with_playwright(download_url, destination_dir, user_agent)
    if result and not check_duplicate_file(result, existing_files):
        return result
    await write_debug_log(f"[Fallback Download] All methods failed")
    return None

async def handle_popup(popup_page: Page):
    """Handle popup pages"""
    await write_debug_log(f"[Popup Detected] URL: {popup_page.url}")
    try:
        await popup_page.wait_for_load_state("domcontentloaded", timeout=10000)
        await popup_page.close()
        await write_debug_log("[Popup Closed]")
    except Exception as e:
        await write_debug_log(f"[Popup Error] {e}")

async def scrape_and_download_9xbuddy(video_url: str):
    """Scrape 9xbuddy.site and download file"""
    extracted_download_urls = []
    downloaded_file_paths = []
    user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    process_url = f"https://9xbuddy.site/process?url={video_url}"
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=user_agent)
        page = await context.new_page()
        page.on("console", lambda msg: asyncio.create_task(write_debug_log(f"[Console {msg.type.upper()}] {msg.text}")))
        page.on("pageerror", lambda err: asyncio.create_task(write_debug_log(f"[Page Error] {err}")))
        page.on("request", lambda req: asyncio.create_task(write_debug_log(f"[Request] {req.method} {req.url}")))
        page.on("response", lambda res: asyncio.create_task(write_debug_log(f"[Response] {res.status} {res.url}")))
        context.on("page", handle_popup)
        
        try:
            await write_debug_log(f"[Scraping] Navigating to: {process_url}")
            await page.goto(process_url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_selector("main#root section.w-full.max-w-4xl", timeout=30000)
            await asyncio.sleep(5)
            
            # Try direct download button
            download_button_selector = "a.btn.btn-success.btn-lg.w-full.mt-4"
            if await page.query_selector(download_button_selector):
                await write_debug_log("[Scraping] Found download button")
                try:
                    async with page.expect_download() as download_info:
                        await page.click(download_button_selector)
                    download = await download_info.value
                    filename = download.suggested_filename or f"download_{hashlib.md5(video_url.encode()).hexdigest()}.mp4"
                    filename = sanitize_filename(filename)
                    download_path = os.path.join("/root/Tera/downloads", filename)
                    await download.save_as(download_path)
                    if os.path.exists(download_path) and os.path.getsize(download_path) > 0:
                        downloaded_file_paths.append(download_path)
                        await write_debug_log(f"[Scraping] Downloaded via button: {download_path}")
                        await context.close()
                        await browser.close()
                        return download_path
                except Exception as e:
                    await write_debug_log(f"[Scraping] Failed to download via button: {e}")
            
            # Extract download links
            unwanted_patterns = [
                r"facebook\.com/sharer",
                r"twitter\.com/intent",
                r"vk\.com/share\.php",
                r"offmp3\.net/process",
                r"savegif\.com/process",
                r"123sudo\.com",
                r"/process\?url=https://vstream\.id/embed/"
            ]
            resolution_480_links = []
            workers_dev_links = []
            ninexbud_links = []
            other_links = []
            
            links = await page.query_selector_all("a[rel='noreferrer nofollow noopener']")
            for element in links:
                href = await element.get_attribute("href")
                if href and not any(re.search(pattern, href) for pattern in unwanted_patterns):
                    parent_div = await element.query_selector("xpath=..")
                    if parent_div:
                        parent_text = await parent_div.text_content()
                        if parent_text and "480" in parent_text:
                            resolution_480_links.append(href)
                            await write_debug_log(f"[Link Filtering] Found 480p link: {href}")
                            continue
                    if ".workers.dev" in href:
                        workers_dev_links.append(href)
                        await write_debug_log(f"[Link Filtering] Found workers.dev link: {href}")
                    elif ".9xbud.com" in href:
                        ninexbud_links.append(href)
                        await write_debug_log(f"[Link Filtering] Found 9xbud.com link: {href}")
                    else:
                        other_links.append(href)
                        await write_debug_log(f"[Link Filtering] Found other link: {href}")
            
            extracted_download_urls = resolution_480_links + workers_dev_links[:1] + ninexbud_links[:1] + other_links[:1]
            await write_debug_log(f"[Scraping] Found {len(extracted_download_urls)} download URLs")
            
            # Download files
            for download_url in extracted_download_urls:
                downloaded_path = await download_file_with_fallback(
                    download_url,
                    "/root/Tera/downloads",
                    user_agent,
                    process_url,
                    downloaded_file_paths
                )
                if downloaded_path:
                    downloaded_file_paths.append(downloaded_path)
                    await write_debug_log(f"[Download] Success: {downloaded_path}")
                    break
            
        except Exception as e:
            await write_debug_log(f"[Scraping] Error: {e}")
        finally:
            await context.close()
            await browser.close()
    
    return downloaded_file_paths[0] if downloaded_file_paths else None

async def get_direct_file(video_url: str):
    """Get local file path after downloading"""
    try:
        file_path = await scrape_and_download_9xbuddy(video_url)
        if not file_path:
            raise Exception("Failed to download file")
        return file_path
    except Exception as e:
        await write_debug_log(f"[get_direct_file] Error: {e}")
        return None
