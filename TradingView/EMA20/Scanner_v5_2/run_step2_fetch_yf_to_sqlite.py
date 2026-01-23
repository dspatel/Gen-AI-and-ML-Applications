# eod_scanner/run_step2_fetch_yf_to_sqlite.py

import os
import pandas as pd
import yfinance as yf

from config import CFG
from utils.io_utils import ensure_dirs, today_ymd, read_df
from utils.sqlite_store import connect_db, init_db
from utils.indicators import add_ema20_columns


def fetch_daily(symbol: str) -> pd.DataFrame:
    period = getattr(CFG, "YF_FETCH_PERIOD", "13mo")

    df = yf.download(
        symbol,
        period=period,
        interval="1d",
        auto_adjust=False,
        progress=False,
        group_by="column",
        threads=True,
    )

    if df is None or df.empty:
        return pd.DataFrame()

    # Flatten MultiIndex if it appears
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]

    df = df.reset_index()

    keep = ["Date", "Open", "High", "Low", "Close", "Volume"]
    missing = [c for c in keep if c not in df.columns]
    if missing:
        raise ValueError(
            f"{symbol}: Missing expected columns from yfinance: {missing}. "
            f"Got columns: {list(df.columns)}"
        )

    df = df[keep].copy()
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date").reset_index(drop=True)

    # Drop unusable rows
    df = df.dropna(subset=["Open", "High", "Low", "Close", "Volume"]).reset_index(drop=True)
    return df


def upsert_daily_bars_with_ema(conn, symbol: str, df: pd.DataFrame) -> int:
    if df.empty:
        return 0

    tmp = df.copy()
    tmp["Date"] = pd.to_datetime(tmp["Date"]).dt.date.astype(str)

    for c in ["Open", "High", "Low", "Close", "Volume", "EMA20", "EMA20_H", "EMA20_L"]:
        tmp[c] = pd.to_numeric(tmp[c], errors="coerce")

    tmp = tmp.dropna(subset=["Open", "High", "Low", "Close", "Volume", "EMA20", "EMA20_H", "EMA20_L"])
    if tmp.empty:
        return 0

    records = list(
        zip(
            [symbol] * len(tmp),
            tmp["Date"].tolist(),
            tmp["Open"].tolist(),
            tmp["High"].tolist(),
            tmp["Low"].tolist(),
            tmp["Close"].tolist(),
            tmp["Volume"].tolist(),
            tmp["EMA20"].tolist(),
            tmp["EMA20_H"].tolist(),
            tmp["EMA20_L"].tolist(),
        )
    )

    conn.executemany("""
        INSERT OR REPLACE INTO daily_bars
        (symbol, date, open, high, low, close, volume, ema20, ema20_h, ema20_l)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
    """, records)
    conn.commit()
    return len(records)


def main():
    ensure_dirs(CFG.SYMBOLS_DIR, os.path.dirname(CFG.DB_PATH))

    run_date = today_ymd()
    symbols_path = os.path.join(CFG.SYMBOLS_DIR, f"symbols_{run_date}.csv")
    if not os.path.exists(symbols_path):
        raise FileNotFoundError(f"Missing symbols file from Step 1: {symbols_path}")

    symbols = read_df(symbols_path)["Symbol"].astype(str).tolist()

    conn = connect_db(CFG.DB_PATH)
    init_db(conn, wal_mode=CFG.SQLITE_WAL_MODE)

    ok, bad = 0, 0

    for sym in symbols:
        try:
            df = fetch_daily(sym)
            if df.empty:
                bad += 1
                continue

            df = add_ema20_columns(df, CFG.EMA_PERIOD)

            # Bound rows BEFORE inserting (keeps DB size stable without extra prune function)
            df = df.tail(CFG.SQLITE_CACHE_DAYS_PER_SYMBOL).reset_index(drop=True)

            inserted = upsert_daily_bars_with_ema(conn, sym, df)
            if inserted == 0:
                bad += 1
            else:
                ok += 1

        except Exception as e:
            bad += 1
            print(f"❌ {sym}: {e}")

    conn.close()
    print(f"✅ Step 2 complete. Cached Yahoo daily + EMA into SQLite: {CFG.DB_PATH}")
    print(f"   Success: {ok}, Failed: {bad}")


if __name__ == "__main__":
    main()
