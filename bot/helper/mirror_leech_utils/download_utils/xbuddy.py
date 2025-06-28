async def get_all_valid_urls(video_url: str):
    """
    Ambil SEMUA link download yang tersedia dan filter yang VALID saja
    """
    valid_links = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        process_url = f"https://9xbuddy.site/process?url={video_url}"

        try:
            await page.goto(process_url, wait_until="domcontentloaded")
            await page.wait_for_selector("a[rel=\"noreferrer nofollow noopener\"]", timeout=30000)

            unwanted_patterns = [
                r"facebook\.com/sharer",
                r"twitter\.com/intent",
                r"vk\.com/share\.php",
                r"offmp3\.net/process",
                r"savegif\.com/process"
            ]

            all_potential_download_links = await page.query_selector_all(
                "main#root a[rel=\"noreferrer nofollow noopener\"]"
            )

            candidates = []
            for element in all_potential_download_links:
                href = await element.get_attribute("href")
                if href and not any(re.search(p, href) for p in unwanted_patterns):
                    candidates.append(href)

            # Cek satu per satu apakah bisa diakses
            for candidate in candidates:
                if await is_valid_url(candidate):
                    valid_links.append(candidate)

        finally:
            await context.close()
            await browser.close()

    return valid_links
