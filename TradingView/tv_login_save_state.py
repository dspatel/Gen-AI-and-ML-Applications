from playwright.sync_api import sync_playwright

STATE_FILE = "tv_state.json"
SCREENER_URL = "https://www.tradingview.com/screener/"

def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()

        page.goto(SCREENER_URL, wait_until="networkidle")

        print("➡️ Log in to TradingView manually in the opened browser.")
        print("➡️ Make sure you can see your saved screeners.")
        input("➡️ Press ENTER here once you are fully logged in...")

        context.storage_state(path=STATE_FILE)
        print(f"✅ Session saved to {STATE_FILE}")

        context.close()
        browser.close()

if __name__ == "__main__":
    main()
