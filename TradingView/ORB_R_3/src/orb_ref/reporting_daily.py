
from __future__ import annotations

from pathlib import Path
from datetime import date
from typing import List, Dict, Any

import pandas as pd


def write_daily_report(rows: List[Dict[str, Any]], asof_date: date, out_dir: str = "reports/daily") -> str:
    """Write the day-centric metrics report.

    Note:
    - Many pipelines include `asof_date` in each row already.
    - We only add the column if it is not present to avoid duplicate insert errors.
    """
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)

    if "asof_date" not in df.columns:
        df.insert(0, "asof_date", asof_date)

    path = Path(out_dir) / f"{asof_date}_metrics.csv"
    df.to_csv(path, index=False)
    return str(path)
