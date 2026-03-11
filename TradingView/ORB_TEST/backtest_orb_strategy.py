from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from typing import Optional

import pandas as pd
import yfinance as yf
from zoneinfo import ZoneInfo


SESSION_TZ = ZoneInfo("America/New_York")
SESSION_START = (9, 30)
SESSION_END = (16, 0)
OR_MINUTES = 30
FIRST_HALF_MINUTES = 195  # 6.5h session / 2


@dataclass
class Trade:
    session_date: str
    direction: str
    entry_time: pd.Timestamp
    entry_price: float
    close_price: float
    quantity: int
    pnl: float
    cash_after: float
    equity_after: float


def _normalize_ohlcv(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()

    out = df.copy()

    if isinstance(out.columns, pd.MultiIndex):
        if symbol in out.columns.get_level_values(-1):
            out = out.xs(symbol, axis=1, level=-1, drop_level=True)
        elif symbol in out.columns.get_level_values(0):
            out = out.xs(symbol, axis=1, level=0, drop_level=True)

    out = out.reset_index()

    if "Datetime" in out.columns:
        out = out.rename(columns={"Datetime": "time"})
    elif "Date" in out.columns:
        out = out.rename(columns={"Date": "time"})

    out = out.rename(
        columns={
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
    )

    required = {"time", "open", "high", "low", "close", "volume"}
    missing = required - set(out.columns)
    if missing:
        raise RuntimeError(f"Missing columns after normalization: {missing}")

    t = pd.to_datetime(out["time"])
    if t.dt.tz is None:
        t = t.dt.tz_localize("UTC")

    out["time_local"] = t.dt.tz_convert(SESSION_TZ)
    out = out.sort_values("time_local").reset_index(drop=True)

    return out[["time_local", "open", "high", "low", "close", "volume"]]


def _regular_session_only(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    local_day = df["time_local"].dt.floor("D")
    start = local_day + pd.Timedelta(hours=SESSION_START[0], minutes=SESSION_START[1])
    end = local_day + pd.Timedelta(hours=SESSION_END[0], minutes=SESSION_END[1])
    out = df[(df["time_local"] >= start) & (df["time_local"] < end)].copy()
    return out.reset_index(drop=True)


def _detect_first_confirmed_breakout_in_first_half(day_df: pd.DataFrame) -> Optional[dict]:
    if day_df.empty:
        return None

    session_start = day_df.iloc[0]["time_local"].normalize() + pd.Timedelta(
        hours=SESSION_START[0], minutes=SESSION_START[1]
    )
    or_end = session_start + pd.Timedelta(minutes=OR_MINUTES)
    first_half_end = session_start + pd.Timedelta(minutes=FIRST_HALF_MINUTES)

    orb = day_df[(day_df["time_local"] >= session_start) & (day_df["time_local"] < or_end)]
    if orb.empty:
        return None

    or_high = float(orb["high"].max())
    or_low = float(orb["low"].min())
    start_idx = int(len(orb))

    if len(day_df) < start_idx + 2:
        return None

    i = start_idx
    while i < len(day_df) - 1:
        b = day_df.iloc[i]
        c = day_df.iloc[i + 1]

        confirm_dt = c["time_local"]
        if confirm_dt > first_half_end:
            break

        b_close = float(b["close"])
        c_close = float(c["close"])

        if b_close > or_high and c_close > b_close:
            return {
                "direction": "UP_TRUE",
                "entry_time": confirm_dt,
                "entry_price": c_close,
            }

        if b_close < or_low and c_close < b_close:
            return {
                "direction": "DOWN_TRUE",
                "entry_time": confirm_dt,
                "entry_price": c_close,
            }

        i += 1

    return None


def run_backtest(
    symbol: str,
    period: str,
    interval: str,
    start_shares: int,
    start_cash: float,
    trade_fraction: float,
) -> tuple[pd.DataFrame, dict]:
    raw = yf.download(
        tickers=symbol,
        interval=interval,
        period=period,
        auto_adjust=False,
        prepost=False,
        progress=False,
        threads=False,
    )

    bars = _normalize_ohlcv(raw, symbol)
    bars = _regular_session_only(bars)

    if bars.empty:
        raise RuntimeError("No regular-session bars returned from Yahoo for the selected inputs.")

    bars["session_date"] = bars["time_local"].dt.date.astype(str)

    shares = int(start_shares)
    cash = float(start_cash)

    first_day = bars["session_date"].iloc[0]
    first_open = float(bars[bars["session_date"] == first_day].iloc[0]["open"])
    initial_equity = cash + shares * first_open

    trades: list[Trade] = []

    for session_date, day_df in bars.groupby("session_date", sort=True):
        day_df = day_df.reset_index(drop=True)
        signal = _detect_first_confirmed_breakout_in_first_half(day_df)
        day_close = float(day_df.iloc[-1]["close"])

        if signal is None:
            equity_after = cash + shares * day_close
            trades.append(
                Trade(
                    session_date=session_date,
                    direction="NO_SIGNAL",
                    entry_time=day_df.iloc[-1]["time_local"],
                    entry_price=day_close,
                    close_price=day_close,
                    quantity=0,
                    pnl=0.0,
                    cash_after=cash,
                    equity_after=equity_after,
                )
            )
            continue

        direction = str(signal["direction"])
        entry_time = signal["entry_time"]
        entry_price = float(signal["entry_price"])

        qty = 0
        pnl = 0.0

        if direction == "UP_TRUE":
            budget = cash * trade_fraction
            qty = int(math.floor(budget / entry_price))
            if qty > 0:
                cash -= qty * entry_price
                cash += qty * day_close
                pnl = (day_close - entry_price) * qty

        elif direction == "DOWN_TRUE":
            qty = int(math.floor(shares * trade_fraction))
            if qty > 0:
                cash += qty * entry_price
                cash -= qty * day_close
                pnl = (entry_price - day_close) * qty

        equity_after = cash + shares * day_close

        trades.append(
            Trade(
                session_date=session_date,
                direction=direction,
                entry_time=entry_time,
                entry_price=entry_price,
                close_price=day_close,
                quantity=qty,
                pnl=pnl,
                cash_after=cash,
                equity_after=equity_after,
            )
        )

    trades_df = pd.DataFrame(
        {
            "session_date": [t.session_date for t in trades],
            "direction": [t.direction for t in trades],
            "entry_time": [t.entry_time for t in trades],
            "entry_price": [t.entry_price for t in trades],
            "close_price": [t.close_price for t in trades],
            "quantity": [t.quantity for t in trades],
            "pnl": [t.pnl for t in trades],
            "cash_after": [t.cash_after for t in trades],
            "equity_after": [t.equity_after for t in trades],
        }
    )

    last_close = float(bars.iloc[-1]["close"])
    final_equity = cash + shares * last_close

    summary = {
        "symbol": symbol,
        "period": period,
        "interval": interval,
        "trading_days": int(trades_df.shape[0]),
        "days_with_signal": int((trades_df["direction"] != "NO_SIGNAL").sum()),
        "initial_shares": int(start_shares),
        "final_shares": int(shares),
        "initial_cash": float(start_cash),
        "final_cash": float(cash),
        "initial_equity": float(initial_equity),
        "final_equity": float(final_equity),
        "net_pnl": float(final_equity - initial_equity),
        "last_close": float(last_close),
        "start_date": str(trades_df.iloc[0]["session_date"]),
        "end_date": str(trades_df.iloc[-1]["session_date"]),
    }

    return trades_df, summary


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backtest first-half confirmed ORB strategy.")
    parser.add_argument("--symbol", default="QQQ")
    parser.add_argument("--period", default="60d")
    parser.add_argument("--interval", default="15m")
    parser.add_argument("--start-shares", type=int, default=100)
    parser.add_argument("--start-cash", type=float, default=10000.0)
    parser.add_argument("--trade-fraction", type=float, default=0.2)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    trades_df, summary = run_backtest(
        symbol=args.symbol,
        period=args.period,
        interval=args.interval,
        start_shares=args.start_shares,
        start_cash=args.start_cash,
        trade_fraction=args.trade_fraction,
    )

    print("=== ORB_TEST Summary ===")
    for k, v in summary.items():
        if isinstance(v, float):
            print(f"{k:>16}: {v:,.2f}")
        else:
            print(f"{k:>16}: {v}")

    print("\nRecent 10 days:")
    show_cols = ["session_date", "direction", "entry_price", "close_price", "quantity", "pnl", "cash_after", "equity_after"]
    print(trades_df[show_cols].tail(10).to_string(index=False))


if __name__ == "__main__":
    main()
