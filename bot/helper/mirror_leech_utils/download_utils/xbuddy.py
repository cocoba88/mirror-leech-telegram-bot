import asyncio
import re
import logging
from urllib.parse import urlparse
from playwright.async_api import async_playwright
from httpx import AsyncClient

# Setup logger
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def write_debug_log(message: str):
    logger.info(f"[xbuddy] {message}")


async def is_valid_url(url: str) -> bool:
    """
    Cek apakah URL bisa diakses.
    """
    try:
        async with AsyncClient(follow_redirects=True, timeout=10) as client:
            response = await client.head(url)
            content_type = response.headers.get("content-type", "")
            return response.status_code == 200 and "text/html" not in content_type
    except Exception as e:
        await write_debug_log(f"Error checking {url}: {e}")
        return False


async def scrape_and_download_9xbuddy(video_url: str):
    """
    Scraping halaman 9xbuddy.site untuk ambil link download.
    Prioritas: .workers.dev > .9xbud.com > .video-src.com > lainnya
    """
    extracted_download_urls = []

    user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=user_agent)
        page = await context.new_page()

        process_url = f" https://9xbuddy.site/process?url={video_url}"
        await write_debug_log(f"Navigating to: {process_url}")

        try:
            # Muat halaman + tunggu dynamic content
            await page.goto(process_url, wait_until="networkidle")
            await page.wait_for_timeout(10000)  # tunggu JS render

            # Coba ambil semua link download
            links = await page.eval_on_selector_all(
                "a[rel='noreferrer nofollow noopener']",
                "elements => elements.map(e => e.href)"
            )

            if not links:
                await write_debug_log("Tidak ada link ditemukan di halaman.")
                return [], []

            # Filter unwanted domains
            unwanted_patterns = [
                r"facebook\.com/sharer",
                r"twitter\.com/intent",
                r"vk\.com/share\.php",
                r"offmp3\.net/process",
                r"savegif\.com/process",
                r"123sudo\.com"
            ]

            workers_dev_links = []
            ninexbud_links = []
            video_src_links = []
            other_links = []

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

            # Urutan prioritas
            candidates = workers_dev_links + ninexbud_links + video_src_links + other_links

            if not candidates:
                await write_debug_log("Tidak ada link valid ditemukan setelah filtering.")
                return [], []

            # Validasi satu per satu
            for candidate in candidates:
                if await is_valid_url(candidate):
                    extracted_download_urls.append(candidate)
                    break  # Ambil yang pertama valid

            # Jika tetap tidak ketemu, kembalikan semua link tanpa filter
            if not extracted_download_urls:
                extracted_download_urls.extend(candidates[:1])

        except Exception as e:
            await write_debug_log(f"Error saat scraping: {e}")
        finally:
            await context.close()
            await browser.close()

    return extracted_download_urls, []  # Hanya kembalikan link, tidak download


async def get_direct_url(video_url: str):
    """
    Ambil satu link langsung dari hasil scraping.
    """
    try:
        extracted_urls, _ = await scrape_and_download_9xbuddy(video_url)
        if extracted_urls:
            return extracted_urls[0]
        else:
            await write_debug_log("Gagal ekstrak URL dari 9xbuddy.site")
            return None
    except Exception as e:
        await write_debug_log(f"Gagal mendapatkan link: {e}")
        return None
