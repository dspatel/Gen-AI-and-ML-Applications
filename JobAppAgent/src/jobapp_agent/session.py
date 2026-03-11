from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright


KNOWN_LOGIN_URLS = {
    "linkedin": "https://www.linkedin.com/login",
    "greenhouse": "https://boards.greenhouse.io/",
    "lever": "https://jobs.lever.co/",
    "workday": "https://www.myworkday.com/",
}


def save_login_state(
    site: str,
    state_path: Path,
    login_url: str = "",
    headless: bool = False,
) -> None:
    site = site.strip().lower()
    effective_url = login_url.strip() or KNOWN_LOGIN_URLS.get(site, "about:blank")

    state_path.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context()
        page = context.new_page()
        page.goto(effective_url)
        print("")
        print(f"Browser opened for site '{site}'.")
        print("1) Log in manually in the browser window.")
        print("2) Complete any MFA/challenge.")
        print("3) Once done, come back here and press Enter.")
        input("Press Enter to save session state...")
        context.storage_state(path=str(state_path))
        browser.close()


def resolve_state_path(state_dir: Path, site: str) -> Path:
    return state_dir / f"{site.strip().lower()}.json"
