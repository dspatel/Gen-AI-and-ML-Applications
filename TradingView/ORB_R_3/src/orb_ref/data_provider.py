
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

import pandas as pd


class IntradayProvider(Protocol):
    def fetch(self, symbol: str, start_dt: datetime, end_dt: datetime, interval: str) -> pd.DataFrame:
        """Return intraday OHLCV bars with a DatetimeIndex."""
        ...


@dataclass(frozen=True)
class YFinanceProvider:
    auto_adjust: bool = False

    def fetch(self, symbol: str, start_dt: datetime, end_dt: datetime, interval: str) -> pd.DataFrame:
        import yfinance as yf

        df = yf.download(
            tickers=symbol,
            start=start_dt,
            end=end_dt,
            interval=interval,
            auto_adjust=self.auto_adjust,
            prepost=False,
            progress=False,
            group_by="column",
            threads=True,
        )
        if df is None or df.empty:
            return pd.DataFrame()

        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [c[0] for c in df.columns]

        rename = {c: c.title() for c in df.columns}
        return df.rename(columns=rename)
