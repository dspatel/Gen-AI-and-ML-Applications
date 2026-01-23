from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class Paths:
    data_dir: Path = Path("data")

    def bars_path(self, sym: str) -> Path:
        return self.data_dir / f"{sym}_30d_5m_yahoo.csv"

    def features_path(self, sym: str) -> Path:
        return self.data_dir / f"{sym}_30d_5m_yahoo_ema20_orb.csv"

    def trades_conservative_path(self, sym: str) -> Path:
        return self.data_dir / f"{sym}_trades_conservative.csv"

    def dvr_conservative_path(self, sym: str) -> Path:
        return self.data_dir / f"{sym}_decision_vs_reality_conservative.csv"

    def equity_curve_conservative_path(self, sym: str) -> Path:
        return self.data_dir / f"{sym}_equity_curve_conservative.csv"

    def equity_summary_conservative_path(self, sym: str) -> Path:
        return self.data_dir / f"{sym}_equity_summary_conservative.csv"
