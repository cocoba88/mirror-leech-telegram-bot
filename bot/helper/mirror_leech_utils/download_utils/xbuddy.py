import asyncio
import re
import httpx
from playwright.async_api import async_playwright


async def is_valid_url(url: str) -> bool:
    """
    Periksa apakah URL bisa diakses (return 200 OK)
    """
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10) as client:
            response = await client.head(url)
            content_type = response.headers.get("content-type", "")
            return response.status_code == 200 and "text/html" not in content_type
    except Exception as e:
        print(f"[URL Check] Error checking {url}: {e}")
        return False


async def get_direct_url(video_url: str):
    """
    Ambil URL video langsung dari hasil scraping 9xbuddy.site.
    Coba urutan: .workers.dev > .9xbud.com > .video-src.com > lainnya
    """
    extracted_download_urls = []

    user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=user_agent)
        page = await context.new_page()

        process_url = f" https://9xbuddy.site/process?url={video_url}"

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

            # Urutan prioritas
            candidates = (
                workers_dev_links +
                ninexbud_links +
                video_src_links +
                other_links
            )

            # Cek satu per satu hingga ketemu yang valid
            for candidate in candidates:
                if await is_valid_url(candidate):
                    extracted_download_urls = [candidate]
                    break

        except Exception as e:
            print(f"[Error] {e}")
        finally:
            await context.close()
            await browser.close()

    return extracted_download_urls[0] if extracted_download_urls else None
