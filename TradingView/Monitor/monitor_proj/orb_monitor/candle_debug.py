from __future__ import annotations

import csv
from pathlib import Path
from typing import Optional, Any

import pandas as pd

from .config import Config


def _mode_dir(cfg: Config) -> Path:
    """Match existing output convention: output/TEST vs output/LIVE."""
    mode = "TEST" if cfg.test_mode else "LIVE"
    base = cfg.output_dir / mode
    base.mkdir(parents=True, exist_ok=True)
    return base


def candle_debug_path(cfg: Config, session_date: str, symbol: str) -> Path:
    """Per-symbol candle debug CSV path."""
    base = _mode_dir(cfg)
    return base / f"candles_{symbol}_{session_date}_{cfg.candle_minutes}m.csv"


def append_candle_debug_row(
    cfg: Config,
    session_date: str,
    symbol: str,
    phase: str,
    *,
    b: Optional[pd.Series] = None,
    c: Optional[pd.Series] = None,
    or_high: Optional[float] = None,
    or_low: Optional[float] = None,
    or_ready: Optional[bool] = None,
    armed_before: Optional[bool] = None,
    armed_after: Optional[bool] = None,
    direction: str = "",
    reason: str = "",
) -> None:
    """Append a single debug row capturing exactly what the strategy evaluated.

    This is intentionally lightweight and append-only so it can be enabled during live trading
    without changing strategy behavior.
    """
    if not getattr(cfg, "enable_candle_debug_log", False):
        return

    path = candle_debug_path(cfg, session_date, symbol)
    file_exists = path.exists()

    header = [
        "session_date",
        "symbol",
        "phase",
        "reason",
        "direction",
        "or_ready",
        "armed_before",
        "armed_after",
        "or_high",
        "or_low",
        # breakout bar (b)
        "b_time",
        "b_open",
        "b_high",
        "b_low",
        "b_close",
        "b_volume",
        # confirm bar (c)
        "c_time",
        "c_open",
        "c_high",
        "c_low",
        "c_close",
        "c_volume",
    ]

    def _get(s: Optional[pd.Series], key: str) -> Any:
        if s is None:
            return ""
        v = s.get(key, "")
        # timestamps -> stable string
        if isinstance(v, pd.Timestamp):
            return str(v)[:19]
        return v

    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if not file_exists:
            w.writerow(header)
        w.writerow(
            [
                session_date,
                symbol,
                phase,
                reason,
                direction,
                "" if or_ready is None else int(bool(or_ready)),
                "" if armed_before is None else int(bool(armed_before)),
                "" if armed_after is None else int(bool(armed_after)),
                "" if or_high is None else f"{float(or_high):.6f}",
                "" if or_low is None else f"{float(or_low):.6f}",
                _get(b, "time_local"),
                _get(b, "open"),
                _get(b, "high"),
                _get(b, "low"),
                _get(b, "close"),
                _get(b, "volume"),
                _get(c, "time_local"),
                _get(c, "open"),
                _get(c, "high"),
                _get(c, "low"),
                _get(c, "close"),
                _get(c, "volume"),
            ]
        )


# -----------------------------------------------------------------------------
# RAW CANDLE STREAM LOG
# -----------------------------------------------------------------------------

def raw_candle_path(cfg: Config, session_date: str, symbol: str) -> Path:
    """Per-symbol raw candle CSV path (data-layer, independent of strategy)."""
    base = _mode_dir(cfg)
    return base / f"candles_raw_{symbol}_{session_date}_{cfg.candle_minutes}m.csv"


def _read_last_logged_time(path: Path) -> Optional[pd.Timestamp]:
    """Return the last logged timestamp (time_local) from an existing raw candle CSV.

    We intentionally avoid pandas.read_csv here to keep this light for live polling.
    """
    if not path.exists():
        return None

    # Read a small tail chunk and grab the last non-empty data row.
    try:
        with path.open('rb') as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 8192), 0)
            chunk = f.read().decode('utf-8', errors='ignore')
        lines = [ln.strip() for ln in chunk.splitlines() if ln.strip()]
        if not lines:
            return None
        # Walk backwards to skip header if the file is small.
        for ln in reversed(lines):
            if ln.lower().startswith('time_local'):
                continue
            first = ln.split(',')[0].strip()
            if not first:
                continue
            try:
                return pd.to_datetime(first, utc=False)
            except Exception:
                return None
        return None
    except Exception:
        return None


def append_raw_session_candles(cfg: Config, session_date: str, symbol: str, session_df: pd.DataFrame) -> None:
    """Append raw candles for the trading session (open→now) for real-time debugging.

    - Append-only CSV
    - Deduped by time_local (only writes rows newer than the last logged row)
    - Independent of breakout logic (so you can validate feed + timezone alignment)
    """
    if not getattr(cfg, 'enable_raw_candle_log', False):
        return

    if session_df is None or session_df.empty:
        return

    path = raw_candle_path(cfg, session_date, symbol)
    file_exists = path.exists()

    # Ensure stable ordering and no duplicates in the input.
    df = session_df.drop_duplicates(subset=['time_local']).sort_values('time_local')

    last_logged = _read_last_logged_time(path)
    if last_logged is not None:
        # CSV timestamps are stored without tz info; our in-memory candles are tz-aware (America/Chicago).
        # Normalize before comparing to avoid tz-naive vs tz-aware errors.
        try:
            series_tz = df['time_local'].dt.tz
            if getattr(last_logged, 'tzinfo', None) is None:
                last_logged = last_logged.tz_localize(series_tz)
            else:
                last_logged = last_logged.tz_convert(series_tz)
        except Exception:
            # If anything unexpected happens, fall back to naive compare by stripping tz from the series.
            df['time_local'] = pd.to_datetime(df['time_local']).dt.tz_localize(None)
            try:
                if getattr(last_logged, 'tzinfo', None) is not None:
                    last_logged = pd.to_datetime(last_logged).tz_localize(None)
            except Exception:
                pass
        df = df[df['time_local'] > last_logged]

    if df.empty:
        return

    header = ['time_local', 'open', 'high', 'low', 'close', 'volume']

    with path.open('a', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        if not file_exists:
            w.writerow(header)
        for _, r in df.iterrows():
            t = r['time_local']
            if isinstance(t, pd.Timestamp):
                t = str(t)[:19]
            w.writerow([
                t,
                r.get('open', ''),
                r.get('high', ''),
                r.get('low', ''),
                r.get('close', ''),
                r.get('volume', ''),
            ])
