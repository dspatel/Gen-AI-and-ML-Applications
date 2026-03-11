from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from .apply import run_guided_apply
from .config import (
    CONFIG_DIR,
    DATA_DIR,
    STATE_DIR,
    init_user_config,
    load_yaml,
    require_config,
)
from .discovery import discover_jobs
from .matching import rank_jobs
from .models import Job
from .session import resolve_state_path, save_login_state
from .storage import read_json, write_json

RAW_JOBS_PATH = DATA_DIR / "jobs_raw.json"
SCORED_JOBS_PATH = DATA_DIR / "jobs_scored.json"


def _automation_defaults() -> dict[str, Any]:
    return {
        "min_score": 20.0,
        "limit": 5,
        "auto_submit": True,
        "auto_fill": True,
        "allow_auto_sites": ["greenhouse", "lever"],
        "max_submissions": 3,
        "interactive": True,
        "dry_run": False,
        "headless": False,
        "bootstrap_accounts": True,
        "retry_after_bootstrap": True,
        "learn_from_manual": True,
        "learn_sensitive": True,
        "dedupe_applied": True,
    }


def _load_automation_cfg() -> dict[str, Any]:
    path = CONFIG_DIR / "automation.yaml"
    cfg = load_yaml(path)
    merged = _automation_defaults()
    if cfg:
        merged.update(cfg)
    return merged


def cmd_init(args: argparse.Namespace) -> None:
    created = init_user_config(force=args.force)
    if created:
        print("Created:")
        for item in created:
            print(f"  - {item}")
    else:
        print("No config files created (already present). Use --force to overwrite.")
    print("")
    print("Next: edit your config files in ./config and then run:")
    print("  jobapp-agent discover")
    print("  jobapp-agent match")
    print("  jobapp-agent top")
    print("  jobapp-agent autopilot")


def cmd_discover(_: argparse.Namespace) -> None:
    sources = require_config("sources")
    jobs = discover_jobs(sources)
    write_json(RAW_JOBS_PATH, [job.to_dict() for job in jobs])
    print(f"Discovered {len(jobs)} jobs.")
    print(f"Saved raw jobs to: {RAW_JOBS_PATH}")


def _load_jobs(path: Path) -> list[Job]:
    raw = read_json(path, default=[])
    return [Job.from_dict(item) for item in raw]


def cmd_match(_: argparse.Namespace) -> None:
    profile = require_config("profile")
    preferences = require_config("preferences")
    jobs = _load_jobs(RAW_JOBS_PATH)
    if not jobs:
        raise FileNotFoundError(
            f"No raw jobs found at {RAW_JOBS_PATH}. Run `jobapp-agent discover` first."
        )
    ranked = rank_jobs(jobs, profile, preferences)
    write_json(SCORED_JOBS_PATH, [j.to_dict() for j in ranked])
    print(f"Scored {len(ranked)} jobs.")
    print(f"Saved ranked jobs to: {SCORED_JOBS_PATH}")


def cmd_top(args: argparse.Namespace) -> None:
    ranked = _load_jobs(SCORED_JOBS_PATH)
    if not ranked:
        raise FileNotFoundError(
            f"No scored jobs found at {SCORED_JOBS_PATH}. Run `jobapp-agent match` first."
        )
    filtered = [j for j in ranked if j.score >= args.min_score]
    print(f"Top {args.limit} jobs (min score: {args.min_score}):")
    print("")
    for idx, job in enumerate(filtered[: args.limit], start=1):
        print(f"{idx}. [{job.score:>5}] {job.title} @ {job.company}")
        print(f"   Location: {job.location}")
        print(f"   Apply: {job.apply_url or job.url}")


def cmd_login(args: argparse.Namespace) -> None:
    state_file = resolve_state_path(STATE_DIR, args.site)
    save_login_state(
        site=args.site,
        state_path=state_file,
        login_url=args.login_url or "",
        headless=args.headless,
    )
    print(f"Saved session state: {state_file}")


def cmd_apply(args: argparse.Namespace) -> None:
    profile = require_config("profile")
    ranked = _load_jobs(SCORED_JOBS_PATH)
    if not ranked:
        raise FileNotFoundError(
            f"No scored jobs found at {SCORED_JOBS_PATH}. Run `jobapp-agent match` first."
        )
    if args.no_interactive and not args.auto_submit:
        raise ValueError("--no-interactive requires --auto-submit.")
    if args.auto_submit and not args.confirm_autopilot:
        raise ValueError(
            "Auto-submit is gated. Re-run with --confirm-autopilot after reviewing top matches."
        )
    allowed_sites = {
        s.strip().lower() for s in args.allow_auto_sites.split(",") if s.strip()
    } if args.allow_auto_sites else set()
    run_guided_apply(
        jobs=ranked,
        profile=profile,
        state_dir=STATE_DIR,
        min_score=args.min_score,
        limit=args.limit,
        auto_fill=(not args.no_autofill),
        headless=args.headless,
        interactive=(not args.no_interactive),
        auto_submit=args.auto_submit,
        allowed_auto_submit_sites=allowed_sites,
        max_submissions=args.max_submissions,
        dry_run=args.dry_run,
        log_dir=(Path(args.log_dir) if args.log_dir else None),
        bootstrap_accounts=args.bootstrap_accounts,
        retry_after_bootstrap=(not args.no_retry_after_bootstrap),
        learn_from_manual=(not args.no_learn_from_manual),
        learn_store_path=(Path(args.learn_store_path) if args.learn_store_path else None),
        learn_sensitive=(not args.no_learn_sensitive),
        dedupe_applied=(not args.no_dedupe_applied),
        applied_store_path=(Path(args.applied_store_path) if args.applied_store_path else None),
    )


def cmd_autopilot(args: argparse.Namespace) -> None:
    profile = require_config("profile")
    ranked = _load_jobs(SCORED_JOBS_PATH)
    if not ranked:
        raise FileNotFoundError(
            f"No scored jobs found at {SCORED_JOBS_PATH}. Run `jobapp-agent match` first."
        )

    cfg = _load_automation_cfg()
    min_score = float(args.min_score if args.min_score is not None else cfg.get("min_score", 20))
    limit = int(args.limit if args.limit is not None else cfg.get("limit", 5))
    allow_sites_cfg = cfg.get("allow_auto_sites", ["greenhouse", "lever"])
    allow_sites = (
        [s.strip().lower() for s in allow_sites_cfg if str(s).strip()]
        if isinstance(allow_sites_cfg, list)
        else [s.strip().lower() for s in str(allow_sites_cfg).split(",") if s.strip()]
    )

    dry_run = bool(cfg.get("dry_run", False))
    if args.dry_run:
        dry_run = True
    if args.live:
        dry_run = False

    print("Autopilot using config/automation.yaml")
    print(
        f"min_score={min_score} limit={limit} "
        f"dry_run={dry_run} interactive={bool(cfg.get('interactive', True))}"
    )

    run_guided_apply(
        jobs=ranked,
        profile=profile,
        state_dir=STATE_DIR,
        min_score=min_score,
        limit=limit,
        auto_fill=bool(cfg.get("auto_fill", True)),
        headless=bool(cfg.get("headless", False)),
        interactive=bool(cfg.get("interactive", True)),
        auto_submit=bool(cfg.get("auto_submit", True)),
        allowed_auto_submit_sites=set(allow_sites),
        max_submissions=int(cfg.get("max_submissions", 3)),
        dry_run=dry_run,
        log_dir=(Path(args.log_dir) if args.log_dir else None),
        bootstrap_accounts=bool(cfg.get("bootstrap_accounts", True)),
        retry_after_bootstrap=bool(cfg.get("retry_after_bootstrap", True)),
        learn_from_manual=bool(cfg.get("learn_from_manual", True)),
        learn_store_path=(Path(args.learn_store_path) if args.learn_store_path else None),
        learn_sensitive=bool(cfg.get("learn_sensitive", True)),
        dedupe_applied=bool(cfg.get("dedupe_applied", True)),
        applied_store_path=(Path(args.applied_store_path) if args.applied_store_path else None),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jobapp-agent",
        description="Local job discovery and guided application copilot.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Create config files from templates.")
    p_init.add_argument("--force", action="store_true", help="Overwrite existing configs.")
    p_init.set_defaults(func=cmd_init)

    p_discover = sub.add_parser("discover", help="Fetch jobs from configured sources.")
    p_discover.set_defaults(func=cmd_discover)

    p_match = sub.add_parser("match", help="Score jobs using your profile/preferences.")
    p_match.set_defaults(func=cmd_match)

    p_top = sub.add_parser("top", help="Show top scored jobs.")
    p_top.add_argument("--limit", type=int, default=20)
    p_top.add_argument("--min-score", type=float, default=1.0)
    p_top.set_defaults(func=cmd_top)

    p_login = sub.add_parser("login", help="Open browser and save a logged-in session.")
    p_login.add_argument("--site", required=True, help="linkedin|greenhouse|lever|workday|...")
    p_login.add_argument(
        "--login-url",
        default="",
        help="Optional login URL override for the selected site.",
    )
    p_login.add_argument("--headless", action="store_true")
    p_login.set_defaults(func=cmd_login)

    p_apply = sub.add_parser("apply", help="Run guided apply flow with manual review.")
    p_apply.add_argument("--min-score", type=float, default=25.0)
    p_apply.add_argument("--limit", type=int, default=10)
    p_apply.add_argument("--no-autofill", action="store_true")
    p_apply.add_argument(
        "--auto-submit",
        action="store_true",
        help="Attempt submit clicks automatically after autofill (gated by allowlist).",
    )
    p_apply.add_argument(
        "--confirm-autopilot",
        action="store_true",
        help="Required safety confirmation when --auto-submit is used.",
    )
    p_apply.add_argument(
        "--allow-auto-sites",
        default="greenhouse,lever",
        help="Comma-separated site allowlist for auto-submit (example: greenhouse,lever).",
    )
    p_apply.add_argument(
        "--max-submissions",
        type=int,
        default=3,
        help="Maximum number of auto-submissions in one run.",
    )
    p_apply.add_argument(
        "--no-interactive",
        action="store_true",
        help="Run without per-job prompts (requires --auto-submit).",
    )
    p_apply.add_argument(
        "--dry-run",
        action="store_true",
        help="Autopilot simulation; no submit click will be executed.",
    )
    p_apply.add_argument(
        "--log-dir",
        default=str(DATA_DIR / "apply_runs"),
        help="Directory for apply run logs.",
    )
    p_apply.add_argument(
        "--bootstrap-accounts",
        action="store_true",
        help="When auth/account gates are detected, attempt account form autofill and pause for verification.",
    )
    p_apply.add_argument(
        "--no-retry-after-bootstrap",
        action="store_true",
        help="Disable automatic retry of the application page after account verification.",
    )
    p_apply.add_argument(
        "--no-learn-from-manual",
        action="store_true",
        help="Disable learning new answers from manual field completion.",
    )
    p_apply.add_argument(
        "--learn-store-path",
        default=str(DATA_DIR / "learning" / "field_memory.json"),
        help="Path to persistent learned-answer store.",
    )
    p_apply.add_argument(
        "--no-learn-sensitive",
        action="store_true",
        help="Disable learning sensitive/EEO-style fields during manual learning.",
    )
    p_apply.add_argument(
        "--no-dedupe-applied",
        action="store_true",
        help="Disable skip logic for previously submitted jobs.",
    )
    p_apply.add_argument(
        "--applied-store-path",
        default=str(DATA_DIR / "applied_jobs.json"),
        help="Path to persistent submitted-job registry.",
    )
    p_apply.add_argument("--headless", action="store_true")
    p_apply.set_defaults(func=cmd_apply)

    p_auto = sub.add_parser(
        "autopilot",
        help="Run apply flow using config/automation.yaml with minimal command flags.",
    )
    p_auto.add_argument("--min-score", type=float, default=None)
    p_auto.add_argument("--limit", type=int, default=None)
    p_auto.add_argument("--dry-run", action="store_true")
    p_auto.add_argument("--live", action="store_true")
    p_auto.add_argument(
        "--log-dir",
        default=str(DATA_DIR / "apply_runs"),
        help="Directory for apply run logs.",
    )
    p_auto.add_argument(
        "--learn-store-path",
        default=str(DATA_DIR / "learning" / "field_memory.json"),
        help="Path to persistent learned-answer store.",
    )
    p_auto.add_argument(
        "--applied-store-path",
        default=str(DATA_DIR / "applied_jobs.json"),
        help="Path to persistent submitted-job registry.",
    )
    p_auto.set_defaults(func=cmd_autopilot)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
