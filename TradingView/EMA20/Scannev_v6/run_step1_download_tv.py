import os
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

from config import CFG
from utils.io_utils import ensure_dirs, safe_filename, load_tv_screener_csv, save_df, today_ymd

DOWNLOAD_TEXT = "Download results as CSV"

def get_screener_name(page) -> str:
    h2s = page.locator("#js-screener-container h2")
    count = h2s.count()
    for i in range(min(count, 20)):
        txt = (h2s.nth(i).inner_text() or "").strip()
        if txt:
            return txt
    title = (page.title() or "").strip()
    return title or "tradingview_screener"

def open_dropdown_from_header(page) -> None:
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

    clickable = target_h2.locator("xpath=ancestor::*[self::button or @role='button'][1]")
    try:
        if clickable.count() > 0:
            clickable.first.click(timeout=5000, force=True)
        else:
            target_h2.click(timeout=5000, force=True)
    except PlaywrightTimeoutError:
        raise RuntimeError("Failed to click screener header to open dropdown.")

    page.get_by_text(DOWNLOAD_TEXT, exact=True).wait_for(timeout=12000)

def click_download_csv(page, save_path: str) -> None:
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    with page.expect_download(timeout=60000) as dl_info:
        page.get_by_text(DOWNLOAD_TEXT, exact=True).click(timeout=8000)
    download = dl_info.value
    download.save_as(save_path)

def main():
    ensure_dirs(CFG.TV_EXPORT_ROOT, CFG.SYMBOLS_DIR)

    if not os.path.exists(CFG.TV_STATE_FILE):
        raise FileNotFoundError(
            f"Missing {CFG.TV_STATE_FILE}. Create it once by logging in with your Playwright login script."
        )

    run_date = today_ymd()
    export_dir = os.path.join(CFG.TV_EXPORT_ROOT, run_date)
    ensure_dirs(export_dir)

    downloaded_csv_paths = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=CFG.TV_HEADLESS)
        context = browser.new_context(accept_downloads=True, storage_state=CFG.TV_STATE_FILE)
        page = context.new_page()

        for url in CFG.TV_SCREEN_URLS:
            page.goto(url, wait_until="networkidle")
            page.wait_for_timeout(1200)

            screener_name = get_screener_name(page)
            open_dropdown_from_header(page)

            filename = f"{safe_filename(screener_name)}_{run_date}.csv"
            csv_path = os.path.join(export_dir, filename)
            click_download_csv(page, csv_path)
            downloaded_csv_paths.append(csv_path)

            page.wait_for_timeout(800)

        context.close()
        browser.close()

    # Parse + combine symbols from all downloaded CSVs
    all_symbols = []
    for pth in downloaded_csv_paths:
        df = load_tv_screener_csv(pth)
        all_symbols.append(df[["Symbol"]])

    combined = (all_symbols[0] if len(all_symbols) == 1
                else __import__("pandas").concat(all_symbols, ignore_index=True))
    combined = combined.drop_duplicates(subset=["Symbol"]).reset_index(drop=True)

    out_symbols_path = os.path.join(CFG.SYMBOLS_DIR, f"symbols_{run_date}.csv")
    save_df(combined, out_symbols_path)

    # Optionally delete raw downloaded files (live mode behavior)
    if CFG.TV_DELETE_DOWNLOADED_CSV_AFTER_PARSE:
        for pth in downloaded_csv_paths:
            try:
                os.remove(pth)
            except Exception:
                pass

    print(f"✅ Step 1 complete. Symbols saved to: {out_symbols_path}")
    print(f"   Symbols count: {len(combined)}")

if __name__ == "__main__":
    main()
