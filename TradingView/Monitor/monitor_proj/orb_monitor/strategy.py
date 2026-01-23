from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, List
import pandas as pd
from zoneinfo import ZoneInfo

from .config import Config
from .candle_debug import append_candle_debug_row

@dataclass
class BreakoutEvent:
    """
    A confirmed ORB "true breakout" event.

    direction:
      - "UP_TRUE": breakout above OR high, confirm candle closes higher than breakout close
      - "DOWN_TRUE": breakout below OR low, confirm candle closes lower than breakout close

    is_catchup:
      - True if detected during the catchup scan at startup
      - False if detected during live monitoring
    """
    symbol: str
    session_date: str
    or_high: float
    or_low: float
    direction: str
    breakout_dt: pd.Timestamp
    breakout_close: float
    breakout_volume: int
    confirm_dt: pd.Timestamp
    confirm_close: float
    confirm_volume: int
    is_catchup: bool = False

@dataclass
class SymbolState:
    """Stateful per-symbol tracking across the live session."""
    symbol: str
    session_date: str
    session_start: pd.Timestamp
    session_end: pd.Timestamp

    or_high: Optional[float] = None
    or_low: Optional[float] = None
    or_ready: bool = False

    # Notification guard: only send the "OR created" message once per symbol per session.
    or_notified: bool = False

    armed: bool = True  # can detect breakouts
    last_confirm_dt_processed: Optional[pd.Timestamp] = None
    events: List[BreakoutEvent] = field(default_factory=list)

def session_bounds(cfg: Config, day_local_midnight: pd.Timestamp) -> tuple[pd.Timestamp, pd.Timestamp]:
    """Build session start/end timestamps in cfg.tz for a given day."""
    day = pd.Timestamp(day_local_midnight).tz_convert(ZoneInfo(cfg.tz)).normalize()
    start = day + pd.Timedelta(hours=cfg.session_start_hm[0], minutes=cfg.session_start_hm[1])
    end = day + pd.Timedelta(hours=cfg.session_end_hm[0], minutes=cfg.session_end_hm[1])
    return start, end

def filter_session(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Keep bars within [start, end)."""
    if df.empty:
        return df
    out = df[(df["time_local"] >= start) & (df["time_local"] < end)].copy()
    return out.reset_index(drop=True)

def build_opening_range(cfg: Config, session_df: pd.DataFrame) -> tuple[float, float]:
    """Compute ORH/ORL from first cfg.orb_bars candles after market open."""
    if len(session_df) < cfg.orb_bars:
        raise RuntimeError(f"Need {cfg.orb_bars} bars for OR, got {len(session_df)}")
    orb = session_df.iloc[: cfg.orb_bars]
    return float(orb["high"].max()), float(orb["low"].min())

def is_within_range(cfg: Config, close: float, or_high: float, or_low: float) -> bool:
    """Used for re-arming after a true breakout."""
    if cfg.range_inclusive:
        return or_low <= close <= or_high
    return or_low < close < or_high

def detect_true_breakout(cfg: Config, or_high: float, or_low: float, breakout_close: float, confirm_close: float) -> Optional[str]:
    """Return 'UP_TRUE' / 'DOWN_TRUE' if confirmed, otherwise None."""
    if breakout_close > or_high:
        if (not cfg.require_2c_confirm) or (confirm_close > breakout_close):
            return "UP_TRUE"
    if breakout_close < or_low:
        if (not cfg.require_2c_confirm) or (confirm_close < breakout_close):
            return "DOWN_TRUE"
    return None

def scan_catchup_events(
    cfg: Config,
    symbol: str,
    session_date: str,
    session_df: pd.DataFrame,
    or_high: float,
    or_low: float,
    notify_until_dt: pd.Timestamp,
    min_confirm_dt_exclusive: Optional[pd.Timestamp] = None,
) -> list[BreakoutEvent]:
    """
    Catchup scan:
    - start after OR window
    - use same rules (2-candle confirm + optional re-arm)
    - stop when confirm candle time exceeds notify_until_dt
    """
    events: list[BreakoutEvent] = []
    armed = True
    i = cfg.orb_bars

    while i < len(session_df) - 1:
        b = session_df.iloc[i]
        c = session_df.iloc[i + 1]

        if c["time_local"] > notify_until_dt:
            break

        b_close = float(b["close"])
        c_close = float(c["close"])

        if cfg.rearm_after_reentry and not armed:
            if is_within_range(cfg, c_close, or_high, or_low):
                armed = True
            append_candle_debug_row(
                cfg,
                session_date,
                symbol,
                phase="CATCHUP",
                b=b,
                c=c,
                or_high=or_high,
                or_low=or_low,
                or_ready=True,
                armed_before=False,
                armed_after=armed,
                direction="",
                reason="REARM_WAIT",
            )
            i += 1
            continue

        direction = detect_true_breakout(cfg, or_high, or_low, b_close, c_close)
        if direction is None:
            append_candle_debug_row(
                cfg,
                session_date,
                symbol,
                phase="CATCHUP",
                b=b,
                c=c,
                or_high=or_high,
                or_low=or_low,
                or_ready=True,
                armed_before=armed,
                armed_after=armed,
                direction="",
                reason="NO_BREAKOUT",
            )
            i += 1
            continue

        # De-dupe: skip events at or before an already-processed confirmation timestamp
        if min_confirm_dt_exclusive is not None and c["time_local"] <= min_confirm_dt_exclusive:
            append_candle_debug_row(
                cfg,
                session_date,
                symbol,
                phase="CATCHUP",
                b=b,
                c=c,
                or_high=or_high,
                or_low=or_low,
                or_ready=True,
                armed_before=armed,
                armed_after=armed,
                direction=direction,
                reason="DEDUPED",
            )
            i += 1
            continue

        ev = BreakoutEvent(
            symbol=symbol,
            session_date=session_date,
            or_high=or_high,
            or_low=or_low,
            direction=direction,
            breakout_dt=b["time_local"],
            breakout_close=b_close,
            breakout_volume=int(float(b["volume"])),
            confirm_dt=c["time_local"],
            confirm_close=c_close,
            confirm_volume=int(float(c["volume"])),
            is_catchup=True,
        )
        events.append(ev)

        append_candle_debug_row(
            cfg,
            session_date,
            symbol,
            phase="CATCHUP",
            b=b,
            c=c,
            or_high=or_high,
            or_low=or_low,
            or_ready=True,
            armed_before=armed,
            armed_after=False if cfg.rearm_after_reentry else armed,
            direction=direction,
            reason="BREAKOUT",
        )

        if cfg.rearm_after_reentry:
            armed = False

        i += 2  # skip past confirmation candle

    return events

def process_latest_two_bars_live(cfg: Config, st: SymbolState, session_df: pd.DataFrame) -> Optional[BreakoutEvent]:
    """
    Live processing:
    - uses last two CLOSED bars
    - de-dupes using last_confirm_dt_processed
    - applies re-arm + true breakout confirmation
    """
    if not st.or_ready:
        if len(session_df) >= cfg.orb_bars:
            st.or_high, st.or_low = build_opening_range(cfg, session_df)
            st.or_ready = True
            # Log OR creation moment (no b/c pair yet).
            append_candle_debug_row(
                cfg,
                st.session_date,
                st.symbol,
                phase="LIVE",
                b=None,
                c=None,
                or_high=float(st.or_high),
                or_low=float(st.or_low),
                or_ready=True,
                armed_before=st.armed,
                armed_after=st.armed,
                direction="",
                reason="OR_READY",
            )
        else:
            append_candle_debug_row(
                cfg,
                st.session_date,
                st.symbol,
                phase="LIVE",
                b=None,
                c=None,
                or_high=st.or_high,
                or_low=st.or_low,
                or_ready=False,
                armed_before=st.armed,
                armed_after=st.armed,
                direction="",
                reason="OR_NOT_READY",
            )
            return None

    if len(session_df) < cfg.orb_bars + 2:
        append_candle_debug_row(
            cfg,
            st.session_date,
            st.symbol,
            phase="LIVE",
            b=None,
            c=None,
            or_high=float(st.or_high) if st.or_high is not None else None,
            or_low=float(st.or_low) if st.or_low is not None else None,
            or_ready=st.or_ready,
            armed_before=st.armed,
            armed_after=st.armed,
            direction="",
            reason="NOT_ENOUGH_BARS",
        )
        return None

    b = session_df.iloc[-2]
    c = session_df.iloc[-1]
    confirm_dt = c["time_local"]

    if st.last_confirm_dt_processed is not None and confirm_dt <= st.last_confirm_dt_processed:
        append_candle_debug_row(
            cfg,
            st.session_date,
            st.symbol,
            phase="LIVE",
            b=b,
            c=c,
            or_high=float(st.or_high) if st.or_high is not None else None,
            or_low=float(st.or_low) if st.or_low is not None else None,
            or_ready=st.or_ready,
            armed_before=st.armed,
            armed_after=st.armed,
            direction="",
            reason="DEDUPED",
        )
        return None

    or_high, or_low = float(st.or_high), float(st.or_low)
    b_close = float(b["close"])
    c_close = float(c["close"])

    # Re-arm gate: if disarmed, do not emit new signals until a candle CLOSES back inside the OR range.
    if cfg.rearm_after_reentry and not st.armed:
        if is_within_range(cfg, c_close, or_high, or_low):
            st.armed = True
        # Always advance watermark so we don't reprocess the same candle pair every loop.
        st.last_confirm_dt_processed = confirm_dt
        append_candle_debug_row(
            cfg,
            st.session_date,
            st.symbol,
            phase="LIVE",
            b=b,
            c=c,
            or_high=or_high,
            or_low=or_low,
            or_ready=True,
            armed_before=False,
            armed_after=st.armed,
            direction="",
            reason="REARM_WAIT",
        )
        return None

    armed_before = st.armed
    direction = detect_true_breakout(cfg, or_high, or_low, b_close, c_close)
    st.last_confirm_dt_processed = confirm_dt

    if direction is None:
        append_candle_debug_row(
            cfg,
            st.session_date,
            st.symbol,
            phase="LIVE",
            b=b,
            c=c,
            or_high=or_high,
            or_low=or_low,
            or_ready=True,
            armed_before=st.armed,
            armed_after=st.armed,
            direction="",
            reason="NO_BREAKOUT",
        )
        return None

    ev = BreakoutEvent(
        symbol=st.symbol,
        session_date=st.session_date,
        or_high=or_high,
        or_low=or_low,
        direction=direction,
        breakout_dt=b["time_local"],
        breakout_close=b_close,
        breakout_volume=int(float(b["volume"])),
        confirm_dt=c["time_local"],
        confirm_close=c_close,
        confirm_volume=int(float(c["volume"])),
        is_catchup=False,
    )

    if cfg.rearm_after_reentry:
        st.armed = False

    append_candle_debug_row(
        cfg,
        st.session_date,
        st.symbol,
        phase="LIVE",
        b=b,
        c=c,
        or_high=or_high,
        or_low=or_low,
        or_ready=True,
        armed_before=armed_before,
        armed_after=st.armed,
        direction=direction,
        reason="BREAKOUT",
    )

    return ev
