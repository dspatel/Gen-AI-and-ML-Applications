from __future__ import annotations

from typing import Any

import requests

from .models import Job

TIMEOUT = 25


def _safe_bool_remote(text: str) -> bool:
    lowered = text.lower()
    return "remote" in lowered or "work from home" in lowered


def fetch_greenhouse(board_token: str) -> list[Job]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs"
    resp = requests.get(url, timeout=TIMEOUT)
    resp.raise_for_status()
    payload = resp.json()
    jobs_raw = payload.get("jobs", [])
    jobs: list[Job] = []
    for item in jobs_raw:
        location_name = (item.get("location") or {}).get("name", "")
        title = item.get("title", "")
        absolute_url = item.get("absolute_url", "")
        data = Job(
            source="greenhouse",
            board=board_token,
            company=board_token,
            title=title,
            location=location_name,
            url=absolute_url,
            apply_url=absolute_url,
            description="",
            posted_at="",
            remote=_safe_bool_remote(location_name),
            raw=item,
        )
        jobs.append(data)
    return jobs


def _extract_lever_apply_url(item: dict[str, Any]) -> str:
    hosted = item.get("hostedUrl") or ""
    if hosted:
        return hosted
    apply_url = item.get("applyUrl") or ""
    if apply_url:
        return apply_url
    return item.get("url") or ""


def fetch_lever(company_token: str) -> list[Job]:
    url = f"https://api.lever.co/v0/postings/{company_token}?mode=json"
    resp = requests.get(url, timeout=TIMEOUT)
    resp.raise_for_status()
    jobs_raw = resp.json()
    jobs: list[Job] = []
    for item in jobs_raw:
        categories = item.get("categories") or {}
        location_name = categories.get("location", "")
        description = item.get("descriptionPlain") or item.get("description") or ""
        title = item.get("text", "")
        apply_url = _extract_lever_apply_url(item)
        posting_url = item.get("hostedUrl") or apply_url
        data = Job(
            source="lever",
            board=company_token,
            company=company_token,
            title=title,
            location=location_name,
            url=posting_url,
            apply_url=apply_url,
            description=description,
            posted_at=item.get("createdAt", ""),
            remote=_safe_bool_remote(location_name + " " + description),
            raw=item,
        )
        jobs.append(data)
    return jobs


def discover_jobs(sources_cfg: dict[str, Any]) -> list[Job]:
    jobs: list[Job] = []
    boards = sources_cfg.get("greenhouse_boards", [])
    lever_companies = sources_cfg.get("lever_companies", [])

    for token in boards:
        try:
            jobs.extend(fetch_greenhouse(str(token).strip()))
        except Exception as exc:
            print(f"[WARN] Greenhouse board '{token}' failed: {exc}")

    for company in lever_companies:
        try:
            jobs.extend(fetch_lever(str(company).strip()))
        except Exception as exc:
            print(f"[WARN] Lever company '{company}' failed: {exc}")

    return jobs
