"""setup_keyring.py
Store TradingView credentials in OS keychain (no env vars).
"""
import getpass
import keyring

def main():
    username = input("TradingView username: ").strip()
    password = getpass.getpass("TradingView password (input hidden): ").strip()
    keyring.set_password("tradingview", "username", username)
    keyring.set_password("tradingview", "password", password)
    print("✅ Saved to OS keychain.")

if __name__ == "__main__":
    main()
