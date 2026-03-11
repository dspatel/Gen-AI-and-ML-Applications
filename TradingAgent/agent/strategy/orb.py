from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from itertools import permutations

import pandas as pd

from agent.config import CHICAGO_TZ, FORCED_EXIT_TIME, OR_END, SESSION_START


EXIT_VARIANTS = [
    "FIXED_2R",
    "EMA20_TRAIL",
    "PARTIAL_1R_TRAIL",
    "BREAKEVEN_RATCHET",
    "OR_REENTRY_FAIL",
    "TIME_STOP_NO_PROGRESS",
    "DUAL_TARGET_LADDER",
    "TIERED_0P5_1_1P5_2R_OR_GUARD",
]
STACK_COMPONENT_CODES = {
    "TSNP": "TIME_STOP_NO_PROGRESS",
    "ORRF": "OR_REENTRY_FAIL",
    "BER": "BREAKEVEN_RATCHET",
}
TIMEFRAMES = [5, 15]
TRADE_LIMITS = [None, 1]  # None => unlimited
LONG_CUTOFFS = [None, time(11, 30)]
NO_PROGRESS_TARGET_R = 0.5
NO_PROGRESS_BARS_BY_TF = {5: 12, 15: 4}
BREAKEVEN_TRIGGER_R = 0.75


def _build_stack_exit_variants() -> list[str]:
    codes = list(STACK_COMPONENT_CODES.keys())
    variants: list[str] = []
    for length in (1, 2, 3):
        for ordered in permutations(codes, length):
            variants.append("STACK_" + "_".join(ordered))
    return variants


EXIT_VARIANTS.extend(_build_stack_exit_variants())


@dataclass(frozen=True)
class EntrySignal:
    side: str
    entry_ts: pd.Timestamp
    entry_price: float
    stop_price: float
    target_2r: float
    risk: float
    or_high: float
    or_low: float


@dataclass(frozen=True)
class ExitResult:
    exit_ts: pd.Timestamp
    exit_price: float
    pnl: float
    reason: str
    exit_idx: int


def run_orb_backtest(symbol: str, bars_5m: pd.DataFrame, run_id: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if bars_5m.empty:
        empty = pd.DataFrame()
        return empty, empty, empty

    base = _prepare_frame(bars_5m)
    frames = {5: base, 15: _resample_15m(base)}

    trades_rows: list[dict] = []

    for timeframe_min in TIMEFRAMES:
        tf_frame = frames[timeframe_min]
        if tf_frame.empty:
            continue

        for exit_variant in EXIT_VARIANTS:
            for trade_limit in TRADE_LIMITS:
                for long_cutoff in LONG_CUTOFFS:
                    strategy_id = _strategy_id(
                        timeframe_min=timeframe_min,
                        exit_variant=exit_variant,
                        trade_limit=trade_limit,
                        long_cutoff=long_cutoff,
                    )
                    strategy_trades = _run_one_strategy(
                        symbol=symbol,
                        run_id=run_id,
                        strategy_id=strategy_id,
                        frame=tf_frame,
                        timeframe_min=timeframe_min,
                        exit_variant=exit_variant,
                        trade_limit=trade_limit,
                        long_cutoff=long_cutoff,
                    )
                    trades_rows.extend(strategy_trades)

    trades = pd.DataFrame(trades_rows)
    metrics = _build_metrics(run_id=run_id, trades=trades)
    yearly = _build_yearly_returns(trades=trades)
    return trades, metrics, yearly


def _run_one_strategy(
    symbol: str,
    run_id: str,
    strategy_id: str,
    frame: pd.DataFrame,
    timeframe_min: int,
    exit_variant: str,
    trade_limit: int | None,
    long_cutoff: time | None,
) -> list[dict]:
    rows: list[dict] = []
    trade_no = 0

    for session_date, day in frame.groupby("session_date", sort=True):
        day = day.sort_values("ts").reset_index(drop=True)
        opening = day[(day["time_local"] >= SESSION_START) & (day["time_local"] < OR_END)].copy()
        min_bars = 6 if timeframe_min == 5 else 2
        if opening.shape[0] < min_bars:
            continue

        or_high = float(opening["h"].max())
        or_low = float(opening["l"].min())

        post = day[(day["time_local"] >= OR_END) & (day["time_local"] <= time(14, 45))].copy().reset_index(drop=True)
        if post.shape[0] < 3:
            continue

        trades_taken = 0
        i = 2
        while i < len(post):
            if trade_limit is not None and trades_taken >= trade_limit:
                break

            bar_prev2 = post.iloc[i - 2]
            bar_prev = post.iloc[i - 1]
            bar_cur = post.iloc[i]

            long_confirm = bool(bar_prev["c"] > or_high and bar_cur["c"] > or_high and bar_prev2["c"] <= or_high)
            short_confirm = bool(bar_prev["c"] < or_low and bar_cur["c"] < or_low and bar_prev2["c"] >= or_low)
            if not long_confirm and not short_confirm:
                i += 1
                continue

            side = "LONG" if long_confirm else "SHORT"
            if side == "LONG" and long_cutoff is not None and bar_cur["time_local"] > long_cutoff:
                i += 1
                continue

            signal = _build_entry_signal(side=side, prev=bar_prev, cur=bar_cur, or_high=or_high, or_low=or_low)
            if signal is None:
                i += 1
                continue

            post_exit = post.iloc[i + 1 :].copy().reset_index(drop=True)
            if post_exit.empty:
                break

            exit_result = _apply_exit(
                post=post_exit,
                signal=signal,
                variant=exit_variant,
                timeframe_min=timeframe_min,
            )
            if exit_result is None:
                break

            trade_no += 1
            trades_taken += 1
            pnl = float(exit_result.pnl)
            r_mult = pnl / signal.risk if signal.risk > 0 else 0.0
            ret_pct = pnl / signal.entry_price if signal.entry_price > 0 else 0.0

            rows.append(
                {
                    "trade_id": f"{run_id}-{strategy_id}-{trade_no}",
                    "run_id": run_id,
                    "strategy_id": strategy_id,
                    "symbol": symbol,
                    "timeframe_min": timeframe_min,
                    "session_date": session_date,
                    "side": signal.side,
                    "entry_ts": signal.entry_ts.strftime("%Y-%m-%d %H:%M:%S%z"),
                    "exit_ts": exit_result.exit_ts.strftime("%Y-%m-%d %H:%M:%S%z"),
                    "entry_price": float(signal.entry_price),
                    "exit_price": float(exit_result.exit_price),
                    "stop_price": float(signal.stop_price),
                    "target_price": float(signal.target_2r),
                    "risk": float(signal.risk),
                    "r_mult": float(r_mult),
                    "pnl": pnl,
                    "ret_pct": float(ret_pct),
                    "exit_reason": exit_result.reason,
                    "trade_limit_1d": int(trade_limit == 1),
                    "long_cutoff_ct": long_cutoff.strftime("%H:%M") if long_cutoff else "NONE",
                }
            )

            i = i + 1 + exit_result.exit_idx

    return rows


def _prepare_frame(bars_5m: pd.DataFrame) -> pd.DataFrame:
    frame = bars_5m.copy()
    frame["ts"] = pd.to_datetime(frame["ts"], utc=True)
    frame = frame.sort_values("ts").reset_index(drop=True)
    frame["ts_local"] = frame["ts"].dt.tz_convert(CHICAGO_TZ)
    frame["session_date"] = frame["ts_local"].dt.strftime("%Y-%m-%d")
    frame["time_local"] = frame["ts_local"].dt.time
    frame["ema20"] = frame["c"].ewm(span=20, adjust=False).mean()
    return frame


def _resample_15m(frame_5m: pd.DataFrame) -> pd.DataFrame:
    out_parts: list[pd.DataFrame] = []
    for session_date, grp in frame_5m.groupby("session_date", sort=True):
        g = grp.set_index("ts").sort_index()
        r = g.resample("15min").agg({"o": "first", "h": "max", "l": "min", "c": "last", "volume": "sum"}).dropna()
        if r.empty:
            continue
        r = r.reset_index()
        r["symbol"] = grp.iloc[0]["symbol"]
        r["ts_local"] = r["ts"].dt.tz_convert(CHICAGO_TZ)
        r["session_date"] = session_date
        r["time_local"] = r["ts_local"].dt.time
        out_parts.append(r[["symbol", "ts", "o", "h", "l", "c", "volume", "ts_local", "session_date", "time_local"]])

    if not out_parts:
        return pd.DataFrame(columns=["symbol", "ts", "o", "h", "l", "c", "volume", "ts_local", "session_date", "time_local", "ema20"])

    out = pd.concat(out_parts, ignore_index=True).sort_values("ts")
    out["ema20"] = out["c"].ewm(span=20, adjust=False).mean()
    return out


def _build_entry_signal(side: str, prev: pd.Series, cur: pd.Series, or_high: float, or_low: float) -> EntrySignal | None:
    if side == "LONG":
        entry_price = float(cur["c"])
        stop_price = min(float(prev["l"]), float(cur["l"])) - 0.01
        risk = entry_price - stop_price
        if risk <= 0:
            return None
        return EntrySignal(
            side="LONG",
            entry_ts=pd.Timestamp(cur["ts"]),
            entry_price=entry_price,
            stop_price=stop_price,
            target_2r=entry_price + 2.0 * risk,
            risk=risk,
            or_high=or_high,
            or_low=or_low,
        )

    entry_price = float(cur["c"])
    stop_price = max(float(prev["h"]), float(cur["h"])) + 0.01
    risk = stop_price - entry_price
    if risk <= 0:
        return None
    return EntrySignal(
        side="SHORT",
        entry_ts=pd.Timestamp(cur["ts"]),
        entry_price=entry_price,
        stop_price=stop_price,
        target_2r=entry_price - 2.0 * risk,
        risk=risk,
        or_high=or_high,
        or_low=or_low,
    )


def _apply_exit(post: pd.DataFrame, signal: EntrySignal, variant: str, timeframe_min: int) -> ExitResult | None:
    if post.empty:
        return None
    if variant.startswith("STACK_"):
        components = _parse_stack_components(variant)
        return _exit_stacked(post=post, signal=signal, timeframe_min=timeframe_min, components=components)
    if variant == "FIXED_2R":
        return _exit_fixed_2r(post, signal)
    if variant == "EMA20_TRAIL":
        return _exit_ema_trail(post, signal)
    if variant == "PARTIAL_1R_TRAIL":
        return _exit_partial_1r_trail(post, signal)
    if variant == "BREAKEVEN_RATCHET":
        return _exit_breakeven_ratchet(post, signal)
    if variant == "OR_REENTRY_FAIL":
        return _exit_or_reentry_fail(post, signal)
    if variant == "TIME_STOP_NO_PROGRESS":
        return _exit_time_stop_no_progress(post, signal, timeframe_min)
    if variant == "DUAL_TARGET_LADDER":
        return _exit_dual_target_ladder(post, signal)
    if variant == "TIERED_0P5_1_1P5_2R_OR_GUARD":
        return _exit_tiered_r_or_guard(post, signal)
    raise ValueError(f"Unknown variant: {variant}")


def _exit_fixed_2r(post: pd.DataFrame, signal: EntrySignal) -> ExitResult:
    for idx, bar in post.reset_index(drop=True).iterrows():
        if signal.side == "LONG":
            if float(bar["l"]) <= signal.stop_price:
                return ExitResult(pd.Timestamp(bar["ts"]), signal.stop_price, -signal.risk, "STOP", idx)
            if float(bar["h"]) >= signal.target_2r:
                return ExitResult(pd.Timestamp(bar["ts"]), signal.target_2r, 2.0 * signal.risk, "TARGET_2R", idx)
        else:
            if float(bar["h"]) >= signal.stop_price:
                return ExitResult(pd.Timestamp(bar["ts"]), signal.stop_price, -signal.risk, "STOP", idx)
            if float(bar["l"]) <= signal.target_2r:
                return ExitResult(pd.Timestamp(bar["ts"]), signal.target_2r, 2.0 * signal.risk, "TARGET_2R", idx)

        if pd.Timestamp(bar["ts"]).tz_convert(CHICAGO_TZ).time() >= FORCED_EXIT_TIME:
            close = float(bar["c"])
            pnl = (close - signal.entry_price) if signal.side == "LONG" else (signal.entry_price - close)
            return ExitResult(pd.Timestamp(bar["ts"]), close, pnl, "TIME_EXIT", idx)

    last = post.iloc[-1]
    close = float(last["c"])
    pnl = (close - signal.entry_price) if signal.side == "LONG" else (signal.entry_price - close)
    return ExitResult(pd.Timestamp(last["ts"]), close, pnl, "EOD", len(post) - 1)


def _exit_ema_trail(post: pd.DataFrame, signal: EntrySignal) -> ExitResult:
    trail = signal.stop_price
    for idx, bar in post.reset_index(drop=True).iterrows():
        ema = float(bar["ema20"]) if pd.notna(bar["ema20"]) else trail
        if signal.side == "LONG":
            trail = max(trail, ema)
            if float(bar["l"]) <= trail:
                return ExitResult(pd.Timestamp(bar["ts"]), trail, trail - signal.entry_price, "TRAIL_STOP", idx)
        else:
            trail = min(trail, ema)
            if float(bar["h"]) >= trail:
                return ExitResult(pd.Timestamp(bar["ts"]), trail, signal.entry_price - trail, "TRAIL_STOP", idx)

        if pd.Timestamp(bar["ts"]).tz_convert(CHICAGO_TZ).time() >= FORCED_EXIT_TIME:
            close = float(bar["c"])
            pnl = (close - signal.entry_price) if signal.side == "LONG" else (signal.entry_price - close)
            return ExitResult(pd.Timestamp(bar["ts"]), close, pnl, "TIME_EXIT", idx)

    last = post.iloc[-1]
    close = float(last["c"])
    pnl = (close - signal.entry_price) if signal.side == "LONG" else (signal.entry_price - close)
    return ExitResult(pd.Timestamp(last["ts"]), close, pnl, "EOD", len(post) - 1)


def _exit_partial_1r_trail(post: pd.DataFrame, signal: EntrySignal) -> ExitResult:
    trail = signal.stop_price
    one_r_target = signal.entry_price + signal.risk if signal.side == "LONG" else signal.entry_price - signal.risk
    partial = False
    remaining = 1.0
    pnl = 0.0

    for idx, bar in post.reset_index(drop=True).iterrows():
        if not partial:
            if signal.side == "LONG" and float(bar["h"]) >= one_r_target:
                pnl += 0.5 * signal.risk
                remaining = 0.5
                partial = True
            elif signal.side == "SHORT" and float(bar["l"]) <= one_r_target:
                pnl += 0.5 * signal.risk
                remaining = 0.5
                partial = True

        if partial:
            ema = float(bar["ema20"]) if pd.notna(bar["ema20"]) else trail
            trail = max(trail, ema) if signal.side == "LONG" else min(trail, ema)

        stop_hit = float(bar["l"]) <= trail if signal.side == "LONG" else float(bar["h"]) >= trail
        if stop_hit:
            pnl += remaining * ((trail - signal.entry_price) if signal.side == "LONG" else (signal.entry_price - trail))
            reason = "TRAIL_STOP" if partial else "STOP"
            eff_exit = signal.entry_price + pnl if signal.side == "LONG" else signal.entry_price - pnl
            return ExitResult(pd.Timestamp(bar["ts"]), eff_exit, pnl, reason, idx)

        if pd.Timestamp(bar["ts"]).tz_convert(CHICAGO_TZ).time() >= FORCED_EXIT_TIME:
            close = float(bar["c"])
            pnl += remaining * ((close - signal.entry_price) if signal.side == "LONG" else (signal.entry_price - close))
            eff_exit = signal.entry_price + pnl if signal.side == "LONG" else signal.entry_price - pnl
            return ExitResult(pd.Timestamp(bar["ts"]), eff_exit, pnl, "TIME_EXIT", idx)

    last = post.iloc[-1]
    close = float(last["c"])
    pnl += remaining * ((close - signal.entry_price) if signal.side == "LONG" else (signal.entry_price - close))
    eff_exit = signal.entry_price + pnl if signal.side == "LONG" else signal.entry_price - pnl
    return ExitResult(pd.Timestamp(last["ts"]), eff_exit, pnl, "EOD", len(post) - 1)


def _exit_breakeven_ratchet(post: pd.DataFrame, signal: EntrySignal) -> ExitResult:
    trail = signal.stop_price
    be_armed = False
    trigger = signal.entry_price + BREAKEVEN_TRIGGER_R * signal.risk if signal.side == "LONG" else signal.entry_price - BREAKEVEN_TRIGGER_R * signal.risk

    for idx, bar in post.reset_index(drop=True).iterrows():
        if signal.side == "LONG":
            if float(bar["h"]) >= trigger:
                be_armed = True
        else:
            if float(bar["l"]) <= trigger:
                be_armed = True

        ema = float(bar["ema20"]) if pd.notna(bar["ema20"]) else trail
        trail = max(trail, ema) if signal.side == "LONG" else min(trail, ema)
        if be_armed:
            trail = max(trail, signal.entry_price) if signal.side == "LONG" else min(trail, signal.entry_price)

        stop_hit = float(bar["l"]) <= trail if signal.side == "LONG" else float(bar["h"]) >= trail
        if stop_hit:
            pnl = (trail - signal.entry_price) if signal.side == "LONG" else (signal.entry_price - trail)
            return ExitResult(pd.Timestamp(bar["ts"]), trail, pnl, "BREAKEVEN_TRAIL_STOP" if be_armed else "TRAIL_STOP", idx)

        if pd.Timestamp(bar["ts"]).tz_convert(CHICAGO_TZ).time() >= FORCED_EXIT_TIME:
            close = float(bar["c"])
            pnl = (close - signal.entry_price) if signal.side == "LONG" else (signal.entry_price - close)
            return ExitResult(pd.Timestamp(bar["ts"]), close, pnl, "TIME_EXIT", idx)

    last = post.iloc[-1]
    close = float(last["c"])
    pnl = (close - signal.entry_price) if signal.side == "LONG" else (signal.entry_price - close)
    return ExitResult(pd.Timestamp(last["ts"]), close, pnl, "EOD", len(post) - 1)


def _exit_or_reentry_fail(post: pd.DataFrame, signal: EntrySignal) -> ExitResult:
    trail = signal.stop_price
    for idx, bar in post.reset_index(drop=True).iterrows():
        stop_hit = float(bar["l"]) <= trail if signal.side == "LONG" else float(bar["h"]) >= trail
        if stop_hit:
            pnl = (trail - signal.entry_price) if signal.side == "LONG" else (signal.entry_price - trail)
            return ExitResult(pd.Timestamp(bar["ts"]), trail, pnl, "STOP", idx)

        close = float(bar["c"])
        reentry = (close <= signal.or_high) if signal.side == "LONG" else (close >= signal.or_low)
        if reentry:
            pnl = (close - signal.entry_price) if signal.side == "LONG" else (signal.entry_price - close)
            return ExitResult(pd.Timestamp(bar["ts"]), close, pnl, "OR_REENTRY_FAIL", idx)

        ema = float(bar["ema20"]) if pd.notna(bar["ema20"]) else trail
        trail = max(trail, ema) if signal.side == "LONG" else min(trail, ema)

        if pd.Timestamp(bar["ts"]).tz_convert(CHICAGO_TZ).time() >= FORCED_EXIT_TIME:
            pnl = (close - signal.entry_price) if signal.side == "LONG" else (signal.entry_price - close)
            return ExitResult(pd.Timestamp(bar["ts"]), close, pnl, "TIME_EXIT", idx)

    last = post.iloc[-1]
    close = float(last["c"])
    pnl = (close - signal.entry_price) if signal.side == "LONG" else (signal.entry_price - close)
    return ExitResult(pd.Timestamp(last["ts"]), close, pnl, "EOD", len(post) - 1)


def _exit_time_stop_no_progress(post: pd.DataFrame, signal: EntrySignal, timeframe_min: int) -> ExitResult:
    trail = signal.stop_price
    progress_hit = False
    max_bars = NO_PROGRESS_BARS_BY_TF.get(timeframe_min, 4)
    progress_price = signal.entry_price + NO_PROGRESS_TARGET_R * signal.risk if signal.side == "LONG" else signal.entry_price - NO_PROGRESS_TARGET_R * signal.risk

    for idx, bar in post.reset_index(drop=True).iterrows():
        if not progress_hit:
            if signal.side == "LONG" and float(bar["h"]) >= progress_price:
                progress_hit = True
            if signal.side == "SHORT" and float(bar["l"]) <= progress_price:
                progress_hit = True

            if (idx + 1) >= max_bars and not progress_hit:
                close = float(bar["c"])
                pnl = (close - signal.entry_price) if signal.side == "LONG" else (signal.entry_price - close)
                return ExitResult(pd.Timestamp(bar["ts"]), close, pnl, "TIME_STOP_NO_PROGRESS", idx)

        ema = float(bar["ema20"]) if pd.notna(bar["ema20"]) else trail
        trail = max(trail, ema) if signal.side == "LONG" else min(trail, ema)
        stop_hit = float(bar["l"]) <= trail if signal.side == "LONG" else float(bar["h"]) >= trail
        if stop_hit:
            pnl = (trail - signal.entry_price) if signal.side == "LONG" else (signal.entry_price - trail)
            return ExitResult(pd.Timestamp(bar["ts"]), trail, pnl, "TRAIL_STOP", idx)

        if pd.Timestamp(bar["ts"]).tz_convert(CHICAGO_TZ).time() >= FORCED_EXIT_TIME:
            close = float(bar["c"])
            pnl = (close - signal.entry_price) if signal.side == "LONG" else (signal.entry_price - close)
            return ExitResult(pd.Timestamp(bar["ts"]), close, pnl, "TIME_EXIT", idx)

    last = post.iloc[-1]
    close = float(last["c"])
    pnl = (close - signal.entry_price) if signal.side == "LONG" else (signal.entry_price - close)
    return ExitResult(pd.Timestamp(last["ts"]), close, pnl, "EOD", len(post) - 1)


def _exit_dual_target_ladder(post: pd.DataFrame, signal: EntrySignal) -> ExitResult:
    trail = signal.stop_price
    t1 = signal.entry_price + signal.risk if signal.side == "LONG" else signal.entry_price - signal.risk
    t2 = signal.target_2r
    hit1 = False
    hit2 = False
    remaining = 1.0
    pnl = 0.0

    for idx, bar in post.reset_index(drop=True).iterrows():
        if signal.side == "LONG":
            if not hit1 and float(bar["h"]) >= t1:
                pnl += 0.5 * signal.risk
                remaining -= 0.5
                hit1 = True
                trail = max(trail, signal.entry_price)
            if hit1 and not hit2 and float(bar["h"]) >= t2:
                pnl += 0.3 * (2.0 * signal.risk)
                remaining -= 0.3
                hit2 = True
        else:
            if not hit1 and float(bar["l"]) <= t1:
                pnl += 0.5 * signal.risk
                remaining -= 0.5
                hit1 = True
                trail = min(trail, signal.entry_price)
            if hit1 and not hit2 and float(bar["l"]) <= t2:
                pnl += 0.3 * (2.0 * signal.risk)
                remaining -= 0.3
                hit2 = True

        if hit1 and remaining > 0:
            ema = float(bar["ema20"]) if pd.notna(bar["ema20"]) else trail
            trail = max(trail, ema) if signal.side == "LONG" else min(trail, ema)

        stop_hit = float(bar["l"]) <= trail if signal.side == "LONG" else float(bar["h"]) >= trail
        if stop_hit:
            pnl += remaining * ((trail - signal.entry_price) if signal.side == "LONG" else (signal.entry_price - trail))
            reason = "DUAL_LADDER_TRAIL_STOP" if hit1 else "STOP"
            effective_exit = signal.entry_price + pnl if signal.side == "LONG" else signal.entry_price - pnl
            return ExitResult(pd.Timestamp(bar["ts"]), effective_exit, pnl, reason, idx)

        if pd.Timestamp(bar["ts"]).tz_convert(CHICAGO_TZ).time() >= FORCED_EXIT_TIME:
            close = float(bar["c"])
            pnl += remaining * ((close - signal.entry_price) if signal.side == "LONG" else (signal.entry_price - close))
            effective_exit = signal.entry_price + pnl if signal.side == "LONG" else signal.entry_price - pnl
            return ExitResult(pd.Timestamp(bar["ts"]), effective_exit, pnl, "TIME_EXIT", idx)

    last = post.iloc[-1]
    close = float(last["c"])
    pnl += remaining * ((close - signal.entry_price) if signal.side == "LONG" else (signal.entry_price - close))
    effective_exit = signal.entry_price + pnl if signal.side == "LONG" else signal.entry_price - pnl
    return ExitResult(pd.Timestamp(last["ts"]), effective_exit, pnl, "EOD", len(post) - 1)


def _exit_tiered_r_or_guard(post: pd.DataFrame, signal: EntrySignal) -> ExitResult:
    trail = signal.stop_price
    remaining = 1.0
    pnl = 0.0

    # Tier levels in R units from entry.
    t05 = signal.entry_price + 0.5 * signal.risk if signal.side == "LONG" else signal.entry_price - 0.5 * signal.risk
    t10 = signal.entry_price + 1.0 * signal.risk if signal.side == "LONG" else signal.entry_price - 1.0 * signal.risk
    t15 = signal.entry_price + 1.5 * signal.risk if signal.side == "LONG" else signal.entry_price - 1.5 * signal.risk
    t20 = signal.entry_price + 2.0 * signal.risk if signal.side == "LONG" else signal.entry_price - 2.0 * signal.risk

    hit05 = False
    hit10 = False
    hit15 = False
    hit20 = False

    for idx, bar in post.reset_index(drop=True).iterrows():
        close = float(bar["c"])
        high = float(bar["h"])
        low = float(bar["l"])

        # OR guard: if breakout fails and re-enters opening range, flatten immediately.
        reentry = (close <= signal.or_high) if signal.side == "LONG" else (close >= signal.or_low)
        if reentry:
            pnl += remaining * ((close - signal.entry_price) if signal.side == "LONG" else (signal.entry_price - close))
            effective_exit = signal.entry_price + pnl if signal.side == "LONG" else signal.entry_price - pnl
            return ExitResult(pd.Timestamp(bar["ts"]), effective_exit, pnl, "TIER_OR_REENTRY_FAIL", idx)

        if signal.side == "LONG":
            if (not hit05) and high >= t05:
                hit05 = True
                trail = max(trail, signal.entry_price)  # move to breakeven at +0.5R
            if (not hit10) and high >= t10:
                hit10 = True
                pnl += 0.25 * (t10 - signal.entry_price)
                remaining -= 0.25
                trail = max(trail, signal.entry_price + 0.25 * signal.risk)
            if (not hit15) and high >= t15:
                hit15 = True
                pnl += 0.25 * (t15 - signal.entry_price)
                remaining -= 0.25
                trail = max(trail, signal.entry_price + 0.5 * signal.risk)
            if (not hit20) and high >= t20:
                hit20 = True
                pnl += 0.25 * (t20 - signal.entry_price)
                remaining -= 0.25
                trail = max(trail, signal.entry_price + 1.0 * signal.risk)
        else:
            if (not hit05) and low <= t05:
                hit05 = True
                trail = min(trail, signal.entry_price)  # move to breakeven at +0.5R
            if (not hit10) and low <= t10:
                hit10 = True
                pnl += 0.25 * (signal.entry_price - t10)
                remaining -= 0.25
                trail = min(trail, signal.entry_price - 0.25 * signal.risk)
            if (not hit15) and low <= t15:
                hit15 = True
                pnl += 0.25 * (signal.entry_price - t15)
                remaining -= 0.25
                trail = min(trail, signal.entry_price - 0.5 * signal.risk)
            if (not hit20) and low <= t20:
                hit20 = True
                pnl += 0.25 * (signal.entry_price - t20)
                remaining -= 0.25
                trail = min(trail, signal.entry_price - 1.0 * signal.risk)

        # After tiers, let remaining position trail with EMA20.
        if remaining > 0:
            ema = float(bar["ema20"]) if pd.notna(bar["ema20"]) else trail
            trail = max(trail, ema) if signal.side == "LONG" else min(trail, ema)

            stop_hit = low <= trail if signal.side == "LONG" else high >= trail
            if stop_hit:
                pnl += remaining * ((trail - signal.entry_price) if signal.side == "LONG" else (signal.entry_price - trail))
                effective_exit = signal.entry_price + pnl if signal.side == "LONG" else signal.entry_price - pnl
                return ExitResult(pd.Timestamp(bar["ts"]), effective_exit, pnl, "TIER_TRAIL_STOP", idx)
        else:
            # Fully exited via tier targets.
            effective_exit = signal.entry_price + pnl if signal.side == "LONG" else signal.entry_price - pnl
            return ExitResult(pd.Timestamp(bar["ts"]), effective_exit, pnl, "TIER_FULL_TARGET_EXIT", idx)

        if pd.Timestamp(bar["ts"]).tz_convert(CHICAGO_TZ).time() >= FORCED_EXIT_TIME:
            pnl += remaining * ((close - signal.entry_price) if signal.side == "LONG" else (signal.entry_price - close))
            effective_exit = signal.entry_price + pnl if signal.side == "LONG" else signal.entry_price - pnl
            return ExitResult(pd.Timestamp(bar["ts"]), effective_exit, pnl, "TIME_EXIT", idx)

    last = post.iloc[-1]
    close = float(last["c"])
    pnl += remaining * ((close - signal.entry_price) if signal.side == "LONG" else (signal.entry_price - close))
    effective_exit = signal.entry_price + pnl if signal.side == "LONG" else signal.entry_price - pnl
    return ExitResult(pd.Timestamp(last["ts"]), effective_exit, pnl, "EOD", len(post) - 1)


def _parse_stack_components(variant: str) -> list[str]:
    parts = variant.split("_")[1:]
    if not parts:
        raise ValueError(f"Invalid stack variant: {variant}")
    components: list[str] = []
    for code in parts:
        if code not in STACK_COMPONENT_CODES:
            raise ValueError(f"Unknown stack component '{code}' in {variant}")
        components.append(STACK_COMPONENT_CODES[code])
    return components


def _exit_stacked(post: pd.DataFrame, signal: EntrySignal, timeframe_min: int, components: list[str]) -> ExitResult:
    trail = signal.stop_price
    progress_hit = False
    be_armed = False
    progress_price = signal.entry_price + NO_PROGRESS_TARGET_R * signal.risk if signal.side == "LONG" else signal.entry_price - NO_PROGRESS_TARGET_R * signal.risk
    be_trigger = signal.entry_price + BREAKEVEN_TRIGGER_R * signal.risk if signal.side == "LONG" else signal.entry_price - BREAKEVEN_TRIGGER_R * signal.risk
    max_bars = NO_PROGRESS_BARS_BY_TF.get(timeframe_min, 4)

    for idx, bar in post.reset_index(drop=True).iterrows():
        close = float(bar["c"])
        high = float(bar["h"])
        low = float(bar["l"])

        if signal.side == "LONG":
            if high >= progress_price:
                progress_hit = True
            if high >= be_trigger:
                be_armed = True
        else:
            if low <= progress_price:
                progress_hit = True
            if low <= be_trigger:
                be_armed = True

        for component in components:
            if component == "TIME_STOP_NO_PROGRESS":
                if (idx + 1) >= max_bars and not progress_hit:
                    pnl = (close - signal.entry_price) if signal.side == "LONG" else (signal.entry_price - close)
                    return ExitResult(pd.Timestamp(bar["ts"]), close, pnl, "STACK_TIME_STOP_NO_PROGRESS", idx)
            elif component == "OR_REENTRY_FAIL":
                reentry = (close <= signal.or_high) if signal.side == "LONG" else (close >= signal.or_low)
                if reentry:
                    pnl = (close - signal.entry_price) if signal.side == "LONG" else (signal.entry_price - close)
                    return ExitResult(pd.Timestamp(bar["ts"]), close, pnl, "STACK_OR_REENTRY_FAIL", idx)
            elif component == "BREAKEVEN_RATCHET":
                if be_armed:
                    trail = max(trail, signal.entry_price) if signal.side == "LONG" else min(trail, signal.entry_price)

        ema = float(bar["ema20"]) if pd.notna(bar["ema20"]) else trail
        trail = max(trail, ema) if signal.side == "LONG" else min(trail, ema)

        stop_hit = low <= trail if signal.side == "LONG" else high >= trail
        if stop_hit:
            pnl = (trail - signal.entry_price) if signal.side == "LONG" else (signal.entry_price - trail)
            return ExitResult(pd.Timestamp(bar["ts"]), trail, pnl, "STACK_TRAIL_STOP", idx)

        if pd.Timestamp(bar["ts"]).tz_convert(CHICAGO_TZ).time() >= FORCED_EXIT_TIME:
            pnl = (close - signal.entry_price) if signal.side == "LONG" else (signal.entry_price - close)
            return ExitResult(pd.Timestamp(bar["ts"]), close, pnl, "TIME_EXIT", idx)

    last = post.iloc[-1]
    close = float(last["c"])
    pnl = (close - signal.entry_price) if signal.side == "LONG" else (signal.entry_price - close)
    return ExitResult(pd.Timestamp(last["ts"]), close, pnl, "EOD", len(post) - 1)


def _build_metrics(run_id: str, trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame(
            columns=[
                "metric_id",
                "run_id",
                "strategy_id",
                "timeframe_min",
                "trade_limit_1d",
                "long_cutoff_ct",
                "trades_count",
                "win_rate",
                "avg_r",
                "profit_factor",
                "max_drawdown",
                "total_return_pct",
            ]
        )

    rows: list[dict] = []
    for strategy_id, grp in trades.groupby("strategy_id", sort=True):
        g = grp.sort_values("exit_ts").copy()
        pnl = g["pnl"].astype(float)

        gross_profit = float(pnl[pnl > 0].sum())
        gross_loss = abs(float(pnl[pnl < 0].sum()))
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)

        equity = pnl.cumsum()
        drawdown = equity - equity.cummax()
        max_drawdown = abs(float(drawdown.min())) if len(drawdown) else 0.0

        compounded = float((1.0 + g["ret_pct"]).prod() - 1.0)
        first = g.iloc[0]
        rows.append(
            {
                "metric_id": f"{run_id}-{strategy_id}",
                "run_id": run_id,
                "strategy_id": strategy_id,
                "timeframe_min": int(first["timeframe_min"]),
                "trade_limit_1d": int(first["trade_limit_1d"]),
                "long_cutoff_ct": str(first["long_cutoff_ct"]),
                "trades_count": int(len(g)),
                "win_rate": float((g["r_mult"] > 0).mean()),
                "avg_r": float(g["r_mult"].mean()),
                "profit_factor": float(profit_factor),
                "max_drawdown": max_drawdown,
                "total_return_pct": compounded * 100.0,
            }
        )

    return pd.DataFrame(rows)


def _build_yearly_returns(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame(columns=["strategy_id", "year", "trades", "year_return_pct"]) 

    out = trades.copy()
    out["exit_dt"] = pd.to_datetime(out["exit_ts"], utc=True)
    out["year"] = out["exit_dt"].dt.year

    rows: list[dict] = []
    for (strategy_id, year), grp in out.groupby(["strategy_id", "year"], sort=True):
        ret = float((1.0 + grp["ret_pct"]).prod() - 1.0)
        rows.append(
            {
                "strategy_id": strategy_id,
                "year": int(year),
                "trades": int(len(grp)),
                "year_return_pct": ret * 100.0,
            }
        )

    return pd.DataFrame(rows)


def _strategy_id(timeframe_min: int, exit_variant: str, trade_limit: int | None, long_cutoff: time | None) -> str:
    limit_tag = "LIMIT1" if trade_limit == 1 else "UNLIMITED"
    cutoff_tag = f"LONG_CUTOFF_{long_cutoff.strftime('%H%M')}" if long_cutoff else "LONG_CUTOFF_NONE"
    return f"TF{timeframe_min}_{exit_variant}_{limit_tag}_{cutoff_tag}"
