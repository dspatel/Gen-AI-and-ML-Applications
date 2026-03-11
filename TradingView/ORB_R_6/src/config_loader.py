from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml


@dataclass(frozen=True)
class SymbolsConfig:
    mode: str
    single: Optional[str]
    list: List[str]
    csv_path: str


@dataclass(frozen=True)
class SessionConfig:
    start: str
    end: str
    calendar: str


@dataclass(frozen=True)
class MarketDataConfig:
    opening_range_minutes: int
    interval: str
    lookback_days: List[int]


@dataclass(frozen=True)
class AppConfig:
    version: str
    db_path: str
    timezone: str
    asof_date_cst: Optional[str]   # canonical anchor date (YYYY-MM-DD) or None
    session: SessionConfig
    symbols: SymbolsConfig
    market_data: MarketDataConfig
    replay: Dict[str, Any]
    discord: Dict[str, Any]


def load_config(path: str = "config.yaml") -> AppConfig:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Config file not found: {p.resolve()}")

    raw: Dict[str, Any] = yaml.safe_load(p.read_text(encoding="utf-8")) or {}

    version = str(raw.get("version", "0.0.0"))
    db_path = str(raw.get("db", {}).get("path", "./market_data.sqlite"))
    timezone = str(raw.get("timezone", "America/Chicago"))

    asof_date_cst = raw.get("asof_date_cst", None)
    if asof_date_cst in ("", "null", "None"):
        asof_date_cst = None
    if asof_date_cst is not None:
        asof_date_cst = str(asof_date_cst)

    sess = raw.get("session", {}) or {}
    session = SessionConfig(
        start=str(sess.get("start", "08:30")),
        end=str(sess.get("end", "15:00")),
        calendar=str(sess.get("calendar", "NYSE")),
    )

    sym = raw.get("symbols", {}) or {}
    symbols = SymbolsConfig(
        mode=str(sym.get("mode", "list")).lower(),
        single=(sym.get("single") or None),
        list=[str(x).upper() for x in (sym.get("list") or [])],
        csv_path=str(sym.get("csv_path", "./data/symbols.csv")),
    )

    md = raw.get("market_data", {}) or {}
    market_data = MarketDataConfig(
        opening_range_minutes=int(md.get("opening_range_minutes", 30)),
        interval=str(md.get("interval", "15m")),
        lookback_days=[int(x) for x in (md.get("lookback_days") or [3, 5, 9])],
    )

    replay = raw.get("replay", {}) or {}
    discord = raw.get("discord", {}) or {}

    return AppConfig(
        version=version,
        db_path=db_path,
        timezone=timezone,
        asof_date_cst=asof_date_cst,
        session=session,
        symbols=symbols,
        market_data=market_data,
        replay=replay,
        discord=discord,
    )
