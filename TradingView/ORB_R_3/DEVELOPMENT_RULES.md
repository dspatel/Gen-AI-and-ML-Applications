
# Development Rules
- Use trading sessions (market days), not calendar days.
- Keep logic symbol-agnostic; symbol management belongs to a universe loader.
- All user-facing outputs are day-centric CSVs with a `symbol` column.
- Cache is an internal implementation detail and must be gitignored.
- Add small tests for each module (pytest). Tests should be offline-safe where possible.
