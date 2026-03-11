# JobAppAgent (v1)

Local, personal-use job application copilot:
- Discovers jobs from ATS APIs (Greenhouse + Lever).
- Ranks jobs based on your profile and preferences.
- Opens applications with saved login sessions.
- Attempts basic autofill, then you manually review and submit.

This is intentionally a safe v1: default mode does not silently auto-submit applications.
Note: it does not crawl LinkedIn directly; job pool size depends on company tokens in `config/sources.yaml`.

## 1) Prerequisites (Windows + Miniconda)

1. Install Miniconda.
2. Open PowerShell in `e:\Machine Learning\JobAppAgent`.
3. Create and activate a conda environment:

```powershell
conda create -y -n jobapp-agent python=3.11
conda activate jobapp-agent
```

4. Install the package:

```powershell
pip install -e .
```

5. Install Playwright browser binaries:

```powershell
playwright install chromium
```

Optional (if you do not want to activate first):

```powershell
conda run -n jobapp-agent pip install -e .
conda run -n jobapp-agent playwright install chromium
```

## 2) Initialize Config

```powershell
jobapp-agent init
```

If env is not activated:

```powershell
conda run -n jobapp-agent jobapp-agent init
```

This creates:
- `config/profile.yaml`
- `config/preferences.yaml`
- `config/sources.yaml`
- `config/automation.yaml`

Edit these files with your real data.

Important fields in `config/preferences.yaml`:
- `weights`: controls scoring strength. Increase `resume_fit`, `must_have`, and `profile_skill` to prioritize JD-to-resume matching.
- `us_only` + `remote_us_only`: enforces US-only jobs and US-eligible remote jobs.
- `local_commute_focus` + `hybrid_in_person_local_only`: keeps hybrid/in-person jobs near your local commute area while allowing US-remote roles.
- `target_titles`: exact role phrases you want.
- `target_seniority_keywords`: catches leadership intent even when title wording differs.
- `target_domain_keywords`: catches analytics/data-science scope from description text.
- `management_focus`: set `true` to strongly prefer manager roles over IC engineer roles.
- `sponsorship_required` and sponsorship keyword lists: boosts likely H1B-friendly postings and penalizes explicit "no sponsorship" postings.

Useful optional fields in `config/profile.yaml`:
- `github_url`: used to autofill GitHub/portfolio/website fields on supported forms.
- `resume_path`: used both for form upload and resume-to-job matching signals in ranking.
- `application_answers`: used to autofill common required questions (work authorization, sponsorship, relocation, etc.).
  - Add one-off form answers in `application_answers.custom` using a question substring key.
  - Example: `"demographic data consent": "yes"`
- `account_bootstrap`: used when a site requires account creation/login before application.
  - Set `JOBAPP_ACCOUNT_PASSWORD` environment variable to avoid storing passwords in config.

## 3) Discover and Rank Jobs

```powershell
jobapp-agent discover
jobapp-agent match
jobapp-agent top --limit 20
```

Output files:
- Raw jobs: `data/jobs_raw.json`
- Scored jobs: `data/jobs_scored.json`

## 4) Save Login Sessions (No Passwords in Code)

For each site you want to apply on, run login once:

```powershell
jobapp-agent login --site linkedin
jobapp-agent login --site greenhouse
jobapp-agent login --site lever
jobapp-agent login --site workday
```

A browser opens. You log in manually.  
After login/MFA, return to terminal and press Enter to save the session.

Saved files:
- `state/linkedin.json`
- `state/greenhouse.json`
- `state/lever.json`
- `state/workday.json`

## 5) Guided Apply Run

```powershell
jobapp-agent apply --min-score 25 --limit 10
```

Behavior:
1. Shows each candidate job.
2. Asks whether to open it.
3. Opens job apply page in browser.
4. Attempts basic autofill (name/email/phone/linkedin/city/resume upload/cover letter upload).
5. You review and submit manually.

Optional flags:
- `--no-autofill` to disable autofill
- `--headless` for debugging runs without visible browser

## 5b) Controlled Autopilot (Optional)

Simplest usage (one command, settings from `config/automation.yaml`):

```powershell
jobapp-agent autopilot
```

Quick overrides:

```powershell
jobapp-agent autopilot --dry-run
jobapp-agent autopilot --live
jobapp-agent autopilot --min-score 25 --limit 8
```

Start with simulation first (no submit clicks):

```powershell
jobapp-agent apply --min-score 20 --limit 5 --auto-submit --confirm-autopilot --dry-run
```

Interactive auto-submit (asks before each submit):

```powershell
jobapp-agent apply --min-score 20 --limit 5 --auto-submit --confirm-autopilot --allow-auto-sites greenhouse,lever --max-submissions 2
```

When blocked by unknown required fields, choose learning prompt in terminal:
- Fill the missing fields manually once in the browser.
- Press Enter to let the tool capture and store those answers for future runs.
- Stored in `data/learning/field_memory.json` (override path with `--learn-store-path`).

Non-interactive batch autopilot (use with care):

```powershell
jobapp-agent apply --min-score 25 --limit 10 --auto-submit --confirm-autopilot --allow-auto-sites greenhouse,lever --max-submissions 3 --no-interactive
```

If account creation/login is required before applying:

```powershell
$env:JOBAPP_ACCOUNT_PASSWORD = "your-password-here"
jobapp-agent apply --min-score 20 --limit 5 --auto-submit --confirm-autopilot --bootstrap-accounts
```

Behavior for account-required pages:
- Detects auth/account gate.
- Attempts to autofill account fields (email/name/password).
- Pauses for CAPTCHA/OTP/MFA completion.
- Saves refreshed session state.
- Retries opening the application form automatically (can disable with `--no-retry-after-bootstrap`).

Autopilot safeguards:
- Auto-submit is blocked unless `--confirm-autopilot` is provided.
- Submit is restricted to allowed sites (`--allow-auto-sites`).
- Run stops after `--max-submissions`.
- Jobs with required fields still empty are blocked from auto-submit.
- Every run is logged to `data/apply_runs/`.
- Blocked events now include `missing_required_fields` with question labels and field types so you can add answers in `profile.yaml`.
- Learning mode (default in interactive runs): when blocked, you can fill fields manually once and save answers for future runs in `data/learning/field_memory.json`.
- Sensitive/EEO-style field learning is ON by default; use `--no-learn-sensitive` to disable it.
- Use `--no-learn-from-manual` to disable learning entirely.
- Previously submitted jobs are skipped by default using `data/applied_jobs.json`.
- Use `--no-dedupe-applied` to allow re-applying and `--applied-store-path` to override registry location.

## 6) Daily Workflow

```powershell
jobapp-agent discover
jobapp-agent match
jobapp-agent top --limit 20
jobapp-agent apply --min-score 30 --limit 10
```

Non-activated equivalent:

```powershell
conda run -n jobapp-agent jobapp-agent discover
conda run -n jobapp-agent jobapp-agent match
conda run -n jobapp-agent jobapp-agent top --limit 20
conda run -n jobapp-agent jobapp-agent apply --min-score 30 --limit 10
```

## Notes and Limits

- Session files can expire; rerun `jobapp-agent login --site <site>` when needed.
- Form structures vary by employer; autofill is best-effort.
- Keep this local and private. Do not store raw passwords in scripts.
- Use responsibly and in line with each website's terms.
- Resume is usually required. Cover letter is optional on many roles but recommended for targeted applications.

## Next improvements you can add

1. Add Workday/SmartRecruiters-specific field mappers.
2. Add rejection/interview tracking to improve ranking.
3. Add cover-letter generation per job.
4. Add a dashboard (Streamlit) for one-click review.
