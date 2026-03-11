# ORB Reference Range vNext (Clean)

Run from repo root (no env vars):

```bash
pip install -r requirements.txt
python -m tools.demo_step_or_rr
```

Outputs:
- SQLite DB in `db/`
- Audit CSVs in `reports_test/` (TEST) or `reports/` (PROD)



## Replay a Day (bar-by-bar)
This simulates a historical session day as it progressed and emits alerts when the reference range breaks.

```bash
python -m tools.replay_day --date 2026-02-05 --send-discord
```

- Primary horizon selection: **smallest horizon first** (e.g., 3D before 5D before 9D).
- The alert will also list other horizons that break on the same bar (same direction).

### Discord
Discord webhook is configured in `config/config.yaml` under `notifications.discord_webhook_url`.
Replace it with your own webhook before any real use.


## Notifications (Discord)

Messages are rendered via `config/notification_templates.yml` and labels via `config/labels.yml`.
Update these files to change wording/icons without touching code.
