ema20_scanner/
  README.md
  config.py                  # all toggles in one place (no duplicates)
  run_step1_download_tv.py
  run_step2_fetch_yf_to_sqlite.py
  run_step3_scan.py          # daily scan entrypoint (live + single-day test)
  run_backtest.py            # multi-day replay entrypoint

  core/
    engine.py                # SINGLE source of truth for scan logic
    events.py                # event definitions (LONG/SHORT/reentry)
    windowing.py             # window anchor computation (frozen window)
    cross.py                 # crossover detection rules
    metrics.py               # alert metrics computation
    state_machine.py         # arming/rearm/disarm logic (pure, testable)

  data/
    cache/
      marketdata.sqlite      # only DB file (daily bars + state + alerts optional)
    symbols/
      symbols_YYYY-MM-DD.csv
    outputs/
      scan_all_YYYY-MM-DD.csv
      scan_alerts_YYYY-MM-DD.csv
      backtest_alerts_START_to_END.csv
      backtest_summary_START_to_END.csv
    tv_exports/
      (temp files; can be deleted)

  store/
    sqlite_store.py          # DB schema + CRUD (daily_bars, symbol_state, optional alerts table)
    models.py                # dataclasses for typed records if desired

  notifiers/
    discord.py               # later
    console.py               # dashboard formatting

  utils/
    io_utils.py              # read/write CSV, date helpers
    time_utils.py            # trading days helpers (optional)
    logging_utils.py         # log setup

  tests/
    test_engine.py           # unit tests for cross/window/state logic (fast)
    fixtures/
