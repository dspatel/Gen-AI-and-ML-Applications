from websocket import create_connection
import json
import pandas as pd
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

SOCKET_URL = "wss://prodata.tradingview.com/socket.io/websocket"

# IDs can be any strings, just keep them consistent within the run
SESSION_ID = "cs_zLCqlT5LX9LF"
SYMBOL_ID  = "sym_1"
SERIES_ID  = "ser_1"
SERIES_NAME = "s1"

TV_SYMBOL  = "NASDAQ:NVDA"
INTERVAL   = "5"          # 5-minute candles
FETCH_BARS = 500          # pull enough to cover yesterday
LAST_N     = 10

EASTERN = ZoneInfo("America/New_York")


def tv_send(ws, method, params):
    payload = json.dumps({"m": method, "p": params})
    ws.send(f"~m~{len(payload)}~m~{payload}")


def split_tv_frames(raw: str):
    out, i, n = [], 0, len(raw)
    while i < n:
        if raw.startswith("~h~", i):  # heartbeat
            nxt = raw.find("~m~", i)
            if nxt == -1:
                break
            i = nxt
            continue

        if raw.startswith("~m~", i):
            i += 3
            j = raw.find("~m~", i)
            if j == -1:
                break
            try:
                msg_len = int(raw[i:j])
            except ValueError:
                break
            i = j + 3
            out.append(raw[i:i + msg_len])
            i += msg_len
            continue

        nxt = raw.find("~m~", i)
        if nxt == -1:
            break
        i = nxt
    return out


def most_recent_trading_date_eastern():
    # "yesterday", rolling back over weekends
    d = (datetime.now(EASTERN) - timedelta(days=1)).date()
    while d.weekday() >= 5:  # Sat/Sun
        d = (datetime.combine(d, datetime.min.time()) - timedelta(days=1)).date()
    return d


def extract_bars_from_ser1(series_block: dict):
    """
    Your actual shape:
      series_block["s"] = [ {"i": k, "v": [ts, o, h, l, c, vol]}, ... ]
    ts is epoch seconds (sometimes ms, so normalize).
    """
    bars = []
    s = series_block.get("s")
    if not isinstance(s, list):
        return bars

    for item in s:
        if not (isinstance(item, dict) and isinstance(item.get("v"), list)):
            continue
        v = item["v"]
        if len(v) < 5:
            continue

        ts = int(v[0])
        # normalize ms -> seconds if needed
        if ts > 10_000_000_000:
            ts //= 1000

        bars.append({
            "time": ts,
            "open": v[1],
            "high": v[2],
            "low":  v[3],
            "close": v[4],
            "volume": v[5] if len(v) >= 6 else None,
        })
    return bars


def fetch_recent_bars(symbol: str, interval: str, bar_count: int) -> pd.DataFrame:
    ws = create_connection(SOCKET_URL)

    tv_send(ws, "chart_create_session", [SESSION_ID, ""])

    symbol_payload = (
        "={"
        "\"adjustment\":\"splits\","
        "\"currency-id\":\"USD\","
        "\"metric\":\"price\","
        "\"session\":\"regular\","
        f"\"symbol\":\"{symbol}\""
        "}"
    )

    tv_send(ws, "resolve_symbol", [SESSION_ID, SYMBOL_ID, symbol_payload])
    tv_send(ws, "create_series", [SESSION_ID, SERIES_ID, SERIES_NAME, SYMBOL_ID, interval, bar_count, ""])

    all_bars = []
    completed = False

    while True:
        raw = ws.recv()
        print (raw)
        for payload in split_tv_frames(raw):
            try:
                msg = json.loads(payload)
            except json.JSONDecodeError:
                continue

            if msg.get("m") == "timescale_update":
                p = msg.get("p")
                if isinstance(p, list) and len(p) >= 2 and isinstance(p[1], dict):
                    # We now know the bars are inside p[1][SERIES_ID]["s"]
                    series_block = p[1].get(SERIES_ID)
                    if isinstance(series_block, dict):
                        all_bars.extend(extract_bars_from_ser1(series_block))

            if msg.get("m") == "series_completed":
                completed = True

        if completed:
            break

    ws.close()

    if not all_bars:
        return pd.DataFrame(columns=["time","open","high","low","close","volume","datetime_et","date_et"])

    df = pd.DataFrame(all_bars).drop_duplicates(subset=["time"]).sort_values("time").reset_index(drop=True)
    df["datetime_et"] = pd.to_datetime(df["time"], unit="s", utc=True).dt.tz_convert(EASTERN)
    df["date_et"] = df["datetime_et"].dt.date
    return df


if __name__ == "__main__":
    target_date = most_recent_trading_date_eastern()
    df = fetch_recent_bars(TV_SYMBOL, INTERVAL, FETCH_BARS)

    df_yday = df[df["date_et"] == target_date].copy()
    if df_yday.empty:
        print(f"No candles found for {target_date} (ET). Showing latest rows available:")
        print(df.tail(20)[["datetime_et","open","high","low","close","volume"]])
    else:
        out = df_yday.tail(LAST_N).reset_index(drop=True)
        print(f"Last {LAST_N} candles for {TV_SYMBOL} on {target_date} (ET):")
        print(out[["datetime_et","open","high","low","close","volume"]])
