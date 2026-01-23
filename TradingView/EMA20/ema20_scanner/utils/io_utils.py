import os
import re
import pandas as pd
from datetime import datetime

def ensure_dirs(*paths: str) -> None:
    for p in paths:
        os.makedirs(p, exist_ok=True)

def safe_filename(s: str) -> str:
    s = re.sub(r'[<>:"/\\|?*\x00-\x1F]+', "_", s)
    s = re.sub(r"\s+", " ", s).strip()
    return (s[:140] if s else "tradingview_screener")

def today_ymd() -> str:
    return datetime.now().strftime("%Y-%m-%d")

def load_tv_screener_csv(csv_path: str) -> pd.DataFrame:
    """
    TradingView CSV format can vary slightly by account/settings.
    We'll load it and attempt to standardize to a column named 'Symbol'.
    """
    df = pd.read_csv(csv_path)

    # Common possibilities: "Symbol", "Ticker", "symbol", etc.
    candidates = [c for c in df.columns if c.strip().lower() in ("symbol", "ticker")]
    if not candidates:
        raise ValueError(f"Could not find Symbol/Ticker column in {csv_path}. Columns: {list(df.columns)}")

    sym_col = candidates[0]
    df = df.rename(columns={sym_col: "Symbol"})
    df["Symbol"] = df["Symbol"].astype(str).str.strip()

    # Remove empty / weird rows
    df = df[df["Symbol"].str.len() > 0].copy()

    # Deduplicate
    df = df.drop_duplicates(subset=["Symbol"]).reset_index(drop=True)
    return df

def save_df(df: pd.DataFrame, path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    df.to_csv(path, index=False)

def read_df(path: str) -> pd.DataFrame:
    return pd.read_csv(path)
