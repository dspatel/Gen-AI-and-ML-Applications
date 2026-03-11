
# ORB Trading Engine — Core Data Layer (v0.2.0)

## Structure

project/
  src/
  data/
    db/          → SQLite databases (core storage)
    raw/         → raw external pulls (optional future use)
    exports/     → derived outputs / reports

Primary DB:
  ./data/db/orb_core.sqlite

This DB will hold multiple tables in the future:
- candles (current)
- opening_ranges
- reference_ranges
- trade_metrics
- decision_logs
- model_features

## Run

pip install -r requirements.txt
python -m src.run
