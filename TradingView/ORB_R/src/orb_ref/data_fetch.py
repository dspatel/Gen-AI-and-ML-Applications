
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Optional, Dict, List, Tuple

import pandas as pd

from orb_ref.sessions import TradingSessions
from orb_ref.data_provider import IntradayProvider, YFinanceProvider


@dataclass(frozen=True)
class FetchSpec:
    symbol: str
    asof_date: date
    interval: str
    tz: str = "America/Chicago"
    exchange: str = "XNYS"
    cache_dir: str = "cache"
    use_cache: bool = True


def _ensure_tz_index(df: pd.DataFrame, tz: ZoneInfo) -> pd.DataFrame:
    if df.empty:
        return df
    if not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("Provider must return a DataFrame with a DatetimeIndex")

    out = df.copy()
    if out.index.tz is None:
        out.index = out.index.tz_localize("UTC").tz_convert(tz)
    else:
        out.index = out.index.tz_convert(tz)
    return out


def _cache_path(cache_dir: Path, symbol: str, session_date: date, interval: str) -> Path:
    safe_sym = symbol.replace("/", "_")
    return cache_dir / safe_sym / interval / f"{session_date.isoformat()}.csv"


def _read_cache(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    if df.empty:
        return df
    dt_col = "Datetime" if "Datetime" in df.columns else df.columns[0]
    df[dt_col] = pd.to_datetime(df[dt_col], utc=True, errors="coerce")
    df = df.set_index(dt_col)
    return df


def _write_cache(path: Path, df: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    out = df.copy()
    if out.index.tz is None:
        out.index = out.index.tz_localize("UTC")
    else:
        out.index = out.index.tz_convert("UTC")
    out.index.name = "Datetime"
    out.to_csv(path)


def fetch_session_bars(
    spec: FetchSpec,
    session_date: date,
    provider: Optional[IntradayProvider] = None,
) -> pd.DataFrame:
    tz = ZoneInfo(spec.tz)
    ts = TradingSessions(exchange=spec.exchange, tz=spec.tz)
    bounds = ts.get_session_bounds(session_date)

    cache_dir = Path(spec.cache_dir)
    cpath = _cache_path(cache_dir, spec.symbol, session_date, spec.interval)

    if spec.use_cache and cpath.exists():
        df = _read_cache(cpath)
        df = _ensure_tz_index(df, tz)
        return df.loc[(df.index >= bounds.open_dt) & (df.index <= bounds.close_dt)].copy()

    provider = provider or YFinanceProvider()

    start = bounds.open_dt - timedelta(minutes=5)
    end = bounds.close_dt + timedelta(minutes=5)

    df = provider.fetch(spec.symbol, start, end, spec.interval)
    if df.empty:
        return df

    df = _ensure_tz_index(df, tz)
    df = df.loc[(df.index >= bounds.open_dt) & (df.index <= bounds.close_dt)].copy()

    if spec.use_cache:
        _write_cache(cpath, df)

    return df


def fetch_lookback_bundle(
    spec: FetchSpec,
    historical_days: int,
    include_today_or: bool,
    provider: Optional[IntradayProvider] = None,
) -> Tuple[List[date], Dict[date, pd.DataFrame]]:
    ts = TradingSessions(exchange=spec.exchange, tz=spec.tz)
    anchor = spec.asof_date if ts.is_trading_day(spec.asof_date) else ts.get_prev_sessions(spec.asof_date, 1)[0]

    prev_sessions = ts.get_prev_sessions(anchor, historical_days)
    sessions: List[date] = list(prev_sessions)

    if include_today_or:
        sessions = [anchor] + sessions

    frames: Dict[date, pd.DataFrame] = {}
    for d in sessions:
        frames[d] = fetch_session_bars(spec, d, provider=provider)

    return sessions, frames
