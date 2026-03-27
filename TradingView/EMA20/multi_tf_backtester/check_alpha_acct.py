import json
import requests
import os

key_path = os.path.join('e:\\', 'Machine Learning', 'TradingView', 'EMA20', 'multi_tf_backtester', 'alpaca_accounts.json')
with open(key_path, 'r') as f:
    accts = json.load(f)['accounts']

paper = next(a for a in accts if a['name'] == 'Paper Account')
headers = {
    'APCA-API-KEY-ID': paper['key'],
    'APCA-API-SECRET-KEY': paper['secret']
}
base_url = paper['base_url']

print("\n--- PINGING ALPHA ENGINE'S ISOLATED ACCOUNT ---")
print(f"Using API Key: {paper['key'][:8]}...{paper['key'][-4:]}")

r_acct = requests.get(f"{base_url}/v2/account", headers=headers).json()
try:
    print(f"Account ID: {r_acct.get('id')}")
    print(f"Account Status: {r_acct.get('status')}")
    print(f"Total Equity: ${float(r_acct.get('equity', 0)):,.2f}")
except Exception as e:
    print("Failed to pull account:", r_acct)

r_pos = requests.get(f"{base_url}/v2/positions", headers=headers).json()
print(f"\nTotal Live Positions in This Account: {len(r_pos)}")

if len(r_pos) > 0:
    for p in r_pos:
        print(f"- [Asset Class: {p.get('asset_class')}] {p['symbol']}: {p['qty']} shares @ ${float(p.get('current_price', 0)):.2f}")
else:
    print("Account is completely clean (100% Cash or newly spun up). Zero options detected.")
print("--------------------------------------------------\n")
