
import asyncio
from playwright.async_api import async_playwright
import re

async def get_direct_url(video_url: str):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()
        process_url = f" https://9xbuddy.site/process?url={video_url}"
        
        try:
	    await write_debug_log(f"[Scraping] Navigating to: {process_url}")
            await page.goto(process_url, wait_until="domcontentloaded")
            await page.wait_for_selector("a[rel='noreferrer nofollow noopener']", timeout=30000)

            unwanted_patterns = [
                r"facebook\.com/sharer",
                r"twitter\.com/intent",
                r"vk\.com/share\.php"
            ]

            links = await page.query_selector_all("a[rel='noreferrer nofollow noopener']")
            valid_links = []

            for element in links:
                href = await element.get_attribute("href")
                if href and not any(re.search(p, href) for p in unwanted_patterns):
                    valid_links.append(href)

            return valid_links[0] if valid_links else None

        finally:
            await context.close()
            await browser.close()
