import json
import requests
import os
import time

key_file = os.path.join(os.path.dirname(__file__), "omega_keys.json")
keys = json.load(open(key_file))['accounts'][0]

headers = {
    'APCA-API-KEY-ID': keys['key'], 
    'APCA-API-SECRET-KEY': keys['secret'], 
    'accept': 'application/json'
}

print("Liquidating Alpha Stocks from Omega Account...")
res = requests.delete('https://paper-api.alpaca.markets/v2/positions', headers=headers)
print(f"Liquidated: HTTP {res.status_code}")
time.sleep(3)

res_acct = requests.get('https://paper-api.alpaca.markets/v2/account', headers=headers).json()
res_pos = requests.get('https://paper-api.alpaca.markets/v2/positions', headers=headers).json()

print(f"\n--- OMEGA ACCOUNT STATUS ---")
print(f"Account ID: {res_acct.get('account_number')}")
print(f"Total Equity: ${float(res_acct.get('equity', 0)):,.2f}")
print(f"Buying Power: ${float(res_acct.get('buying_power', 0)):,.2f}")
print(f"Total Positions Held: {len(res_pos)}")

for p in res_pos:
    print(f" - {p.get('symbol')} | Qty: {p.get('qty')} | Asset Type: {p.get('asset_class')}")
print("\n")
