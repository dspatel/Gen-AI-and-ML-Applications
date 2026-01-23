from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from datetime import datetime
import os
import re

STATE_FILE = "tv_state.json"
EXPORT_ROOT = os.path.join(os.getcwd(), "tv_exports")

SCREEN_URLS = [
    "https://www.tradingview.com/screener/DEzUPE3I/",
]

DOWNLOAD_TEXT = "Download results as CSV"


def safe_filename(s: str) -> str:
    s = re.sub(r'[<>:"/\\|?*\x00-\x1F]+', "_", s)
    s = re.sub(r"\s+", " ", s).strip()
    return (s[:140] if s else "tradingview_screener")


def get_screener_name(page) -> str:
    """
    Grabs the visible screener name in the top bar (your H2 element).
    We pick the H2 inside the screener container that has non-empty text.
    """
    # TradingView often uses this container id for the screener app
    h2s = page.locator("#js-screener-container h2")
    count = h2s.count()
    for i in range(min(count, 20)):
        txt = (h2s.nth(i).inner_text() or "").strip()
        if txt:
            # The first meaningful H2 is typically the screener name
            return txt

    # Fallback: page title
    title = (page.title() or "").strip()
    return title or "tradingview_screener"


def open_dropdown_from_header(page) -> None:
    """
    Opens the dropdown menu by clicking the screener-name header.
    Works across TradingView DOM variations by:
    - finding the first non-empty H2 in #js-screener-container
    - clicking its clickable ancestor if available
    """
    h2s = page.locator("#js-screener-container h2")
    count = h2s.count()

    target_h2 = None
    for i in range(min(count, 20)):
        h2 = h2s.nth(i)
        try:
            txt = (h2.inner_text() or "").strip()
        except Exception:
            continue
        if txt:
            target_h2 = h2
            break

    if target_h2 is None:
        raise RuntimeError("Could not find screener header (h2) to open dropdown.")

    # Click a clickable ancestor if present (button/role=button), else click the H2 itself
    clickable = target_h2.locator("xpath=ancestor::*[self::button or @role='button'][1]")
    try:
        if clickable.count() > 0:
            clickable.first.click(timeout=5000, force=True)
        else:
            target_h2.click(timeout=5000, force=True)
    except PlaywrightTimeoutError:
        raise RuntimeError("Failed to click screener header to open dropdown.")

    # The menu is often rendered in a portal; wait globally
    page.get_by_text(DOWNLOAD_TEXT, exact=True).wait_for(timeout=12000)


def click_download_csv(page, save_path: str) -> None:
    os.makedirs(os.path.dirname(save_path), exist_ok=True)

    with page.expect_download(timeout=60000) as dl_info:
        page.get_by_text(DOWNLOAD_TEXT, exact=True).click(timeout=8000)

    download = dl_info.value
    download.save_as(save_path)


def main(headless: bool = False):
    if not os.path.exists(STATE_FILE):
        raise FileNotFoundError(
            f"Missing {STATE_FILE}. Create it once by logging in with your Playwright login script."
        )

    run_date = datetime.now().strftime("%Y-%m-%d")
    export_dir = os.path.join(EXPORT_ROOT, run_date)

    saved_paths = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(accept_downloads=True, storage_state=STATE_FILE)
        page = context.new_page()

        for url in SCREEN_URLS:
            page.goto(url, wait_until="networkidle")
            page.wait_for_timeout(1200)  # small settle time for UI

            screener_name = get_screener_name(page)
            slug = url.rstrip("/").split("/")[-1]  # SSOqQTWL, gyjiUSJS, rnzZa3T3

            # Open dropdown and click download
            open_dropdown_from_header(page)

            filename = f"{safe_filename(screener_name)}_{run_date}.csv"
            path = os.path.join(export_dir, filename)

            click_download_csv(page, path)
            saved_paths.append(path)

            # let UI settle between screens
            page.wait_for_timeout(800)

        context.close()
        browser.close()

    for pth in saved_paths:
        print(f"✅ Saved: {pth}")


if __name__ == "__main__":
    # First run: headless=False so you can see it working.
    # After success: switch to True for scheduling.
    main(headless=False)
