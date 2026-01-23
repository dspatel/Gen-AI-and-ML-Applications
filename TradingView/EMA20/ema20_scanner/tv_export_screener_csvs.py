from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from datetime import datetime
import os
import re

SCREENER_URL = "https://www.tradingview.com/screener/"
STATE_FILE = "tv_state.json"

SCREENS_TO_EXPORT = [
    "Symbol List",
]

EXPORT_ROOT = os.path.join(os.getcwd(), "tv_exports")

H2_SELECTOR = "#\:r6\: > div > div > div.crop-KgzMMF6Z > div > div.mainScrollWrapper-KgzMMF6Z > div > div:nth-child(5) > div > div > div.middle-LSK1huUA.hasTitle-LSK1huUA.hasNoEndSlot-LSK1huUA > div"


def safe_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1F]+', "_", name)
    return re.sub(r"\s+", " ", name).strip()


def ensure_logged_in(page):
    try:
        if page.get_by_role("link", name=re.compile(r"Sign in", re.I)).is_visible(timeout=2000):
            raise RuntimeError("Session expired. Recreate tv_state.json.")
    except PlaywrightTimeoutError:
        pass


def open_screener_menu(page):
    h2 = page.locator(H2_SELECTOR)
    h2.wait_for(state="visible", timeout=8000)

    clickable = h2.locator("xpath=ancestor::*[self::button or @role='button'][1]")
    if clickable.count() > 0:
        clickable.first.click(timeout=5000, force=True)
    else:
        h2.click(timeout=5000, force=True)

    page.get_by_text("Download results as CSV", exact=True).wait_for(timeout=8000)


def get_open_popup_container(page):
    item = page.get_by_text("Download results as CSV", exact=True)
    return item.locator("xpath=ancestor::div[3]")


def select_saved_screen(page, screen_name: str):
    container = get_open_popup_container(page)

    # Preferred: list row title
    title = container.locator("div.title-LSK1huUA").filter(has_text=screen_name).first
    try:
        title.wait_for(state="visible", timeout=4000)
        title.click(timeout=8000)
        page.wait_for_timeout(700)
        return
    except PlaywrightTimeoutError:
        pass

    # Fallback: any element with text within popup
    target = container.locator(":scope *").filter(has_text=screen_name).first
    try:
        target.wait_for(state="visible", timeout=4000)
        target.click(timeout=8000)
        page.wait_for_timeout(700)
    except PlaywrightTimeoutError:
        raise RuntimeError(f"Could not select screener '{screen_name}' from popup list.")


def download_csv(page, out_dir: str, screen_name: str) -> str:
    os.makedirs(out_dir, exist_ok=True)

    with page.expect_download(timeout=60000) as dl:
        page.get_by_text("Download results as CSV", exact=True).click(timeout=8000)

    download = dl.value
    date_tag = datetime.now().strftime("%Y-%m-%d")
    filename = f"{safe_filename(screen_name)}_{date_tag}.csv"
    path = os.path.join(out_dir, filename)
    download.save_as(path)
    return path


def main(headless: bool = False):
    if not os.path.exists(STATE_FILE):
        raise FileNotFoundError("Missing tv_state.json. Create it first.")

    run_date = datetime.now().strftime("%Y-%m-%d")
    export_dir = os.path.join(EXPORT_ROOT, run_date)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(accept_downloads=True, storage_state=STATE_FILE)
        page = context.new_page()

        page.goto(SCREENER_URL, wait_until="networkidle")
        ensure_logged_in(page)

        saved = []
        for screen in SCREENS_TO_EXPORT:
            open_screener_menu(page)
            select_saved_screen(page, screen)

            # menu closes after selection; open again to download
            open_screener_menu(page)
            path = download_csv(page, export_dir, screen)
            saved.append(path)

            page.wait_for_timeout(800)

        context.close()
        browser.close()

    for s in saved:
        print(f"✅ Saved: {s}")


if __name__ == "__main__":
    # Start with headless=False to verify. Then switch to True later.
    main(headless=False)
