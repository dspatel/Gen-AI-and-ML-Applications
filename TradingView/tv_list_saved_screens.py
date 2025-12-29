from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
import os
import re

SCREENER_URL = "https://www.tradingview.com/screener/"
STATE_FILE = "tv_state.json"

def try_open_templates(page):
    # Try several likely buttons/areas that open the saved screens/templates menu
    candidates = [
        ("role_button_templates", lambda: page.get_by_role("button", name=re.compile(r"Templates?", re.I)).click(timeout=2500)),
        ("aria_template", lambda: page.locator("[aria-label*='template' i]").first.click(timeout=2500)),
        ("aria_screen", lambda: page.locator("[aria-label*='screen' i]").first.click(timeout=2500)),
    ]
    for name, fn in candidates:
        try:
            fn()
            page.wait_for_timeout(800)
            return True
        except PlaywrightTimeoutError:
            continue
    return False

def main():
    if not os.path.exists(STATE_FILE):
        raise FileNotFoundError("Missing tv_state.json. Create it with your login-state script first.")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(storage_state=STATE_FILE)
        page = context.new_page()

        page.goto(SCREENER_URL, wait_until="networkidle")

        opened = try_open_templates(page)

        if not opened:
            print("\nCould not confidently open the templates menu automatically.")
            print("Manually click your saved screen dropdown in the browser window now, then press Enter here.\n")
            input("Press Enter after the dropdown/list is visible... ")

        # Once the dropdown/list is visible, collect clickable items
        # We grab common menu/list roles and clickable elements.
        locs = [
            page.locator("[role='menuitem']"),
            page.locator("[role='option']"),
            page.locator("button"),
            page.locator("div[role='button']"),
        ]

        seen = set()
        for loc in locs:
            try:
                count = loc.count()
            except Exception:
                continue
            for i in range(min(count, 300)):
                try:
                    txt = (loc.nth(i).inner_text() or "").strip()
                except Exception:
                    continue
                if not txt:
                    continue
                # Filter out noisy UI text; keep reasonable-length candidates
                if 3 <= len(txt) <= 80:
                    seen.add(txt)

        print("\n--- CANDIDATE NAMES FOUND (copy exact text) ---")
        for t in sorted(seen):
            # You can eyeball your three screen names in this list
            print(t)

        print("\nIf you don't see your screen names above:")
        print("- Make sure the saved screens dropdown is open.")
        print("- Scroll inside the dropdown (it may be virtualized).")

        context.close()
        browser.close()

if __name__ == "__main__":
    main()
