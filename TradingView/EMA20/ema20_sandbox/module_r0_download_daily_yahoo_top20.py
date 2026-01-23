from __future__ import annotations

import os
import pandas as pd

try:
    import yfinance as yf
except ImportError:
    raise SystemExit("Missing dependency: yfinance. Install with: pip install yfinance")


def ensure_dir(p: str) -> None:
    os.makedirs(p, exist_ok=True)


def load_universe_symbols(path: str) -> list[str]:
    if not os.path.exists(path):
        raise SystemExit(f"Universe file not found: {path}")
    df = pd.read_csv(path)
    if "symbol" not in df.columns:
        raise SystemExit(f"Universe file must contain 'symbol' column: {path}")
    return df["symbol"].astype(str).str.upper().tolist()


def download_daily(symbol: str, period: str = "6mo") -> pd.DataFrame:
    # auto_adjust=True gives adjusted prices; for regime/cross logic it's typically fine.
    # If you prefer raw OHLC, set auto_adjust=False.
    df = yf.download(
        tickers=symbol,
        period=period,
        interval="1d",
        auto_adjust=True,
        progress=False,
        threads=False
    )
    if df is None or df.empty:
        return pd.DataFrame()

    df = df.reset_index()
    # normalize column names
    df.columns = [
    (c[0] if isinstance(c, tuple) else c).lower().replace(" ", "_")
    for c in df.columns]
    # expected columns: date, open, high, low, close, volume
    if "date" not in df.columns:
        # yfinance sometimes uses "datetime"
        if "datetime" in df.columns:
            df = df.rename(columns={"datetime": "date"})
        else:
            raise ValueError(f"Unexpected daily columns for {symbol}: {df.columns.tolist()}")
    df["symbol"] = symbol
    df["date"] = pd.to_datetime(df["date"]).dt.date.astype(str)
    return df[["date", "symbol", "open", "high", "low", "close", "volume"]]


def main():
    universe_path = os.path.join("data", "research", "universe_watchlist_top20.csv")
    out_dir = os.path.join("data", "research", "daily_yahoo")
    ensure_dir(out_dir)

    symbols = load_universe_symbols(universe_path)
    print(f"Universe symbols: {len(symbols)}")

    all_rows = []
    for s in symbols:
        d = download_daily(s, period="6mo")
        if d.empty:
            print(f"⚠️ No daily data for {s}")
            continue
        all_rows.append(d)

    if not all_rows:
        raise SystemExit("No daily data downloaded. Check symbols / connectivity.")

    daily = pd.concat(all_rows, ignore_index=True)
    out_path = os.path.join(out_dir, "daily_top20_6mo.csv")
    daily.to_csv(out_path, index=False)

    print("\n✅ Saved daily data ->", out_path)
    print("Sample:")
    print(daily.head(10).to_string(index=False))


if __name__ == "__main__":
    main()
