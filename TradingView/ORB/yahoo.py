from __future__ import annotations

import os
import pandas as pd
import yfinance as yf
from zoneinfo import ZoneInfo

# ===== CONFIG =====
SYMBOLS = ["SPY", "TSLA", "NVDA"]
INTERVAL = "5m"          # 1m,2m,5m,15m,30m,60m,...
PERIOD = "1d"            # use "5d" if you want more bars (still limited by Yahoo intraday rules)
DISPLAY_TZ = "America/Chicago"
SHOW_LAST_N = 20

def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")

def fetch_yahoo(symbol: str) -> pd.DataFrame:
    df = yf.download(
        tickers=symbol,
        interval=INTERVAL,
        period=PERIOD,
        auto_adjust=False,
        prepost=False,      # regular session only
        progress=False,
        threads=False,
    )

    if df is None or df.empty:
        return pd.DataFrame()

    # yfinance returns index tz-aware (usually US/Eastern) or tz-naive depending on environment.
    df = df.copy()
    df.reset_index(inplace=True)

    # Normalize column names
    # Index column can be "Datetime" or "Date"
    if "Datetime" in df.columns:
        df.rename(columns={"Datetime": "time"}, inplace=True)
    elif "Date" in df.columns:
        df.rename(columns={"Date": "time"}, inplace=True)

    df.rename(columns={
        "Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"
    }, inplace=True)

    # Convert to Chicago display timezone
    t = pd.to_datetime(df["time"])
    if t.dt.tz is None:
        # If tz-naive, assume it's UTC then convert (Yahoo usually returns tz-aware, but keep safe)
        t = t.dt.tz_localize("UTC")
    df["time_local"] = t.dt.tz_convert(ZoneInfo(DISPLAY_TZ))

    df = df.sort_values("time_local").reset_index(drop=True)
    return df[["time_local", "open", "high", "low", "close", "volume"]]

def render_dashboard(data: dict[str, pd.DataFrame]) -> str:
    lines = []
    lines.append(f"YFINANCE 5m TEST | tz={DISPLAY_TZ}")
    lines.append("-" * 120)
    lines.append(f"{'SYM':<6} {'BARS':>4} {'LAST_BAR':<19} {'CLOSE':>10} {'HIGH':>10} {'LOW':>10} {'VOL':>12}")
    lines.append("-" * 120)

    for sym, df in data.items():
        if df.empty:
            lines.append(f"{sym:<6} {0:>4} {'':<19} {'':>10} {'':>10} {'':>10} {'':>12}")
            continue

        # IMPORTANT: iloc[-1] gives a Series (single row)
        last = df.iloc[-1]

        # Force scalar values (prevents Series.__format__ error)
        last_time = str(last["time_local"])[:19]
        close = float(last["close"])
        high  = float(last["high"])
        low   = float(last["low"])
        vol   = int(float(last["volume"]))

        lines.append(
            f"{sym:<6} {len(df):>4} {last_time:<19} "
            f"{close:>10.2f} {high:>10.2f} {low:>10.2f} {vol:>12}"
        )

    lines.append("\n" + "=" * 120)
    lines.append(f"LAST {SHOW_LAST_N} BARS PER SYMBOL")
    lines.append("=" * 120)

    for sym, df in data.items():
        lines.append(f"\n>>> {sym}")
        if df.empty:
            lines.append("NO DATA")
            continue

        view = df.tail(SHOW_LAST_N).copy()
        view["time_local"] = view["time_local"].astype(str).str.slice(0, 19)
        lines.append(view.to_string(index=False))

    return "\n".join(lines)

def main():
    data = {}
    for sym in SYMBOLS:
        data[sym] = fetch_yahoo(sym)

    clear_screen()
    print(render_dashboard(data))

if __name__ == "__main__":
    # pip install yfinance pandas
    main()
