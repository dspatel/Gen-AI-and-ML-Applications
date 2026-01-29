import os
from playwright.sync_api import sync_playwright
from config import CFG

LOGIN_URL = "https://www.tradingview.com/#signin"

def main():
    os.makedirs(CFG.TV_EXPORT_ROOT, exist_ok=True)
    os.makedirs(CFG.TV_DOWNLOAD_DIR, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)  # must be visible for login
        context = browser.new_context(accept_downloads=True)
        page = context.new_page()

        print("Opening TradingView login page...")
        page.goto(LOGIN_URL, wait_until="domcontentloaded")

        print("\n✅ Please log in manually in the browser window.")
        print("When you see you are logged in, come back here and press Enter.\n")
        input("Press Enter AFTER you are logged in...")

        context.storage_state(path=CFG.TV_STATE_FILE)
        print(f"\n✅ Saved TradingView session state to: {CFG.TV_STATE_FILE}")

        browser.close()

if __name__ == "__main__":
    main()
