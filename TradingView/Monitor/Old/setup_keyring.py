"""One-time TradingView credential setup (stored in OS keychain via keyring)."""

import getpass
import keyring

def main():
    print("This stores your TradingView credentials in your OS keychain (Windows Credential Manager / macOS Keychain).")
    username = input("TradingView username: ").strip()
    password = getpass.getpass("TradingView password (input hidden): ").strip()

    keyring.set_password("tradingview", "username", username)
    keyring.set_password("tradingview", "password", password)
    print("✅ Saved. You can now run monitor_vcre.py without typing credentials.")

if __name__ == "__main__":
    main()
