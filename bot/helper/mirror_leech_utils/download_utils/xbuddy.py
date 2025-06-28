import asyncio
import re
from playwright.async_api import async_playwright


async def get_direct_url(video_url: str):
    """
    Ambil URL video langsung dari hasil scraping 9xbuddy.site.
    Tidak mendownload ke server, hanya kembalikan link.
    """
    extracted_download_urls = []

    user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=user_agent)
        page = await context.new_page()

        process_url = f"https://9xbuddy.site/process?url={video_url}"

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
                        if ".workers.dev" in href:
                            workers_dev_links.append(href)
                        elif ".9xbud.com" in href:
                            ninexbud_links.append(href)
                        else:
                            other_links.append(href)

            # Prioritas: 480p > workers.dev > 9xbud.com > lainnya
            if resolution_480_links:
                extracted_download_urls = resolution_480_links
            elif workers_dev_links:
                extracted_download_urls = workers_dev_links[:1]
            elif ninexbud_links:
                extracted_download_urls = ninexbud_links[:1]
            else:
                extracted_download_urls = other_links[:1]

        except Exception as e:
            print(f"[Error] {e}")
        finally:
            await context.close()
            await browser.close()

    return extracted_download_urls[0] if extracted_download_urls else None
