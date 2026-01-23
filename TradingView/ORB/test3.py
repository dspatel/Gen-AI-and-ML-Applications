from tvDatafeed import TvDatafeed, Interval

# Keyring
KEYRING_SERVICE = "tradingview"
KEYRING_USER_KEY = "username"
KEYRING_PASS_KEY = "password"



import keyring  # type: ignore
u = keyring.get_password(KEYRING_SERVICE, KEYRING_USER_KEY)
p = keyring.get_password(KEYRING_SERVICE, KEYRING_PASS_KEY)
if not u or not p:
    raise RuntimeError("Missing keyring creds for TradingView.")
print(u, p)
#my TV password contains special characters
tv = TvDatafeed(u, p)


tv = TvDatafeed(u, p)
df = tv.get_hist("TSLA", "NASDAQ", Interval.in_5_minute, n_bars=1500)

print(df)