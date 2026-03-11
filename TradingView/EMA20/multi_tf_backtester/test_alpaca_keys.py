import requests
import json
import os

CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'alpaca_accounts.json')

def test_accounts():
    with open(CONFIG_PATH, 'r') as f:
        config = json.load(f)
        
    for acct in config['accounts']:
        print(f"\n--- Testing Connection to: {acct['name']} ---")
        headers = {
            "APCA-API-KEY-ID": acct['key'],
            "APCA-API-SECRET-KEY": acct['secret'],
            "accept": "application/json"
        }
        
        try:
            response = requests.get(f"{acct['base_url']}/v2/account", headers=headers)
            if response.status_code == 200:
                data = response.json()
                print(f"[SUCCESS] Connected!")
                print(f"Status: {data.get('status')}")
                print(f"Cash Equivalent: ${float(data.get('cash', 0)):,.2f}")
                print(f"Total Equity: ${float(data.get('equity', 0)):,.2f}")
                print(f"Trading Blocked: {data.get('trading_blocked')}")
            else:
                print(f"[FAILED] HTTP {response.status_code}: {response.text}")
        except Exception as e:
            print(f"[FAILED] Error: {e}")

if __name__ == '__main__':
    test_accounts()
