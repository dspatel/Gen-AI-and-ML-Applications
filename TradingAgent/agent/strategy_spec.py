from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import time


STRATEGY_RE = re.compile(
    r"^TF(?P<tf>\d+)_(?P<exit>.+)_(?P<limit>UNLIMITED|LIMIT1)_LONG_CUTOFF_(?P<cutoff>NONE|\d{4})$"
)


@dataclass(frozen=True)
class StrategySpec:
    strategy_id: str
    timeframe_min: int
    exit_variant: str
    trade_limit_1d: int
    long_cutoff: time | None


def parse_strategy_id(strategy_id: str) -> StrategySpec:
    m = STRATEGY_RE.match(strategy_id)
    if m is None:
        raise ValueError(f"Invalid strategy id: {strategy_id}")

    tf = int(m.group("tf"))
    exit_variant = m.group("exit")
    limit = 1 if m.group("limit") == "LIMIT1" else 0
    cutoff_raw = m.group("cutoff")
    cutoff = None
    if cutoff_raw != "NONE":
        hh = int(cutoff_raw[:2])
        mm = int(cutoff_raw[2:])
        cutoff = time(hh, mm)

    return StrategySpec(
        strategy_id=strategy_id,
        timeframe_min=tf,
        exit_variant=exit_variant,
        trade_limit_1d=limit,
        long_cutoff=cutoff,
    )
