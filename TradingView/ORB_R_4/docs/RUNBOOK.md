# Runbook

## Run
```bash
python -m src.run
```

## Verify quickly
```python
import sqlite3
conn = sqlite3.connect("market_data.sqlite")
print(conn.execute("select count(*) from candles").fetchone())
print(conn.execute("select distinct cst_date from candles order by cst_date desc").fetchall())
```

## Sample output (shape)

```
[OK] SPY 15m: raw_fetched=... filtered=... inserted=... skipped_existing=...
```

## Expected output includes Step 2
You should see a line like:
```
[OK] opening_ranges: computed=... upserted=... incomplete=...
```

## Step 3 output
You should see:
```
[OK] daily_reference_metrics: upserted=... incomplete_horizons=...
```
