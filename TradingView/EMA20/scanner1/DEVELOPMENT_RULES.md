# Development Rules — EMA20 Anchored Breakout Scanner

These rules keep the project production-grade and prevent “logic drift”.

## 1) Single source of truth
- **Strategy logic** must live in one place:
  - Cross logic: `utils/indicators.py`
  - Window anchoring: `run_step3_scan_from_sqlite.py` (and re-used by live)
  - State/ledger: `utils/sqlite_store.py`
- Do **not** copy/paste strategy logic across scripts. Extract functions instead.

## 2) Files that must be updated together
When you change anything that affects behavior, update all of these:
- `config.py`
- `README.md` and `USER_GUIDE.md`
- `PROJECT_CONTEXT.md`
- `CHANGELOG.md`

If you change schema:
- `utils/sqlite_store.py`
- `model.sql`

## 3) No placeholders
- No `TODO` stubs left in production scripts.
- If something is optional, implement it behind a toggle.

## 4) Time and trading days
- Treat the user timezone as **America/Chicago**.
- Live session detection must use **exchange-calendars XNYS**.
- Strategy windows and lookbacks are based on **trading days** (from the daily bars you have, not calendar days).

## 5) Safety guards
- Never let live mode overwrite EOD outputs.
- Keep `PRESERVE_EXISTING_ALERTS_FILE_IF_EMPTY = True` on by default.
- Dedupe alerts via the ledger (`alerts_log`).

## 6) Performance guardrails
- Avoid writing large intermediate files unless explicitly enabled by a toggle.
- Keep SQLite writes batched where possible.

## 7) Release process
- Bump version in `CHANGELOG.md`.
- Add a short “what changed” list.
- Verify:
  - `python -m py_compile` passes
  - Step2 + Step3 run end-to-end
  - Live tracker starts pre-open and waits correctly
