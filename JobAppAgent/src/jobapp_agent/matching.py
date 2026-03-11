from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .models import Job


DEFAULT_RESUME_TERMS = [
    "python",
    "sql",
    "snowflake",
    "tableau",
    "power bi",
    "power apps",
    "machine learning",
    "data science",
    "analytics",
    "ab testing",
    "predictive modeling",
    "airflow",
    "etl",
    "stakeholder",
    "leadership",
    "kpi",
    "governance",
    "experimentation",
    "risk",
    "finance",
]


def _contains_any(text: str, needles: list[str]) -> int:
    lowered = text.lower()
    hits = 0
    for n in needles:
        n = n.strip().lower()
        if n and n in lowered:
            hits += 1
    return hits


def _extract_resume_terms(profile: dict[str, Any]) -> list[str]:
    terms: set[str] = set()
    skills = profile.get("skills", [])
    for s in skills:
        if s and isinstance(s, str):
            terms.add(s.strip().lower())

    resume_path = str(profile.get("resume_path", "")).strip()
    text = ""
    if resume_path:
        p = Path(resume_path)
        if p.exists():
            try:
                if p.suffix.lower() == ".pdf":
                    from pypdf import PdfReader  # type: ignore

                    text = "\n".join((pg.extract_text() or "") for pg in PdfReader(str(p)).pages)
                elif p.suffix.lower() in {".txt", ".md"}:
                    text = p.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                text = ""

    lowered = text.lower()
    for term in DEFAULT_RESUME_TERMS:
        if term in lowered:
            terms.add(term)

    years_hits = re.findall(r"\b(\d{1,2})\+?\s*years?\b", lowered)
    if years_hits:
        terms.add("experience")

    return [t for t in terms if t]


def score_job(
    job: Job,
    profile: dict[str, Any],
    preferences: dict[str, Any],
    resume_terms: list[str],
) -> float:
    weights = preferences.get("weights", {})
    w_title = float(weights.get("title", 12))
    w_seniority_title = float(weights.get("seniority_title", 8))
    w_seniority_text = float(weights.get("seniority_text", 3))
    w_domain = float(weights.get("domain", 6))
    w_must = float(weights.get("must_have", 12))
    w_nice = float(weights.get("nice_to_have", 2))
    w_profile = float(weights.get("profile_skill", 5))
    w_resume = float(weights.get("resume_fit", 10))
    w_local_non_remote_boost = float(weights.get("local_non_remote_boost", 10))
    w_non_local_non_remote_penalty = float(weights.get("non_local_non_remote_penalty", 25))

    title_keywords = preferences.get("target_titles", [])
    seniority_keywords = preferences.get("target_seniority_keywords", [])
    domain_keywords = preferences.get("target_domain_keywords", [])
    management_focus = bool(preferences.get("management_focus", False))
    management_title_keywords = preferences.get(
        "management_title_keywords",
        ["manager", "director", "head", "vp", "vice president"],
    )
    must_have_skills = preferences.get("must_have_skills", [])
    nice_to_have_skills = preferences.get("nice_to_have_skills", [])
    locations = preferences.get("preferred_locations", [])
    remote_only = bool(preferences.get("remote_only", False))
    sponsorship_required = bool(preferences.get("sponsorship_required", False))
    sponsorship_positive = preferences.get("sponsorship_positive_keywords", [])
    sponsorship_negative = preferences.get("sponsorship_negative_keywords", [])
    exclude_title_keywords = preferences.get("exclude_title_keywords", [])
    no_senior_manager = bool(preferences.get("no_senior_manager", False))
    us_only = bool(preferences.get("us_only", False))
    remote_us_only = bool(preferences.get("remote_us_only", False))
    local_commute_focus = bool(preferences.get("local_commute_focus", False))
    hybrid_in_person_local_only = bool(preferences.get("hybrid_in_person_local_only", False))
    local_commute_keywords = preferences.get("local_commute_keywords", [])
    hybrid_keywords = preferences.get(
        "hybrid_keywords",
        ["hybrid", "in office 2 days", "in office 3 days", "flexible onsite"],
    )
    onsite_keywords = preferences.get(
        "onsite_keywords",
        ["onsite", "on-site", "in-office", "in office", "office-based"],
    )
    us_positive_keywords = preferences.get(
        "us_positive_keywords",
        [
            "united states",
            "us-",
            "usa",
            "u.s.",
            "remote us",
            "remote - us",
            "remote - united states",
        ],
    )
    non_us_keywords = preferences.get(
        "non_us_keywords",
        [
            "canada",
            "ontario",
            "mexico",
            "london",
            "dublin",
            "berlin",
            "amsterdam",
            "tokyo",
            "singapore",
            "india",
            "ireland",
            "germany",
            "france",
            "united kingdom",
            "uk",
            "europe",
            "australia",
        ],
    )

    searchable_text = f"{job.title}\n{job.location}\n{job.description}"
    location_text = f"{job.location}\n{job.description}".lower()
    has_us_signal = _contains_any(location_text, us_positive_keywords) > 0
    has_non_us_signal = _contains_any(location_text, non_us_keywords) > 0
    is_remote = job.remote or ("remote" in location_text)
    is_hybrid = _contains_any(location_text, hybrid_keywords) > 0
    is_onsite = _contains_any(location_text, onsite_keywords) > 0 and not is_remote
    is_non_remote = is_hybrid or is_onsite or (not is_remote)
    local_hits = _contains_any(location_text, local_commute_keywords)

    if us_only and has_non_us_signal and not has_us_signal:
        return 0.0
    if us_only and remote_us_only and is_remote and has_non_us_signal and not has_us_signal:
        return 0.0

    score = 0.0

    title_hits = _contains_any(job.title, title_keywords)
    score += title_hits * w_title

    title_lower = job.title.lower()
    if no_senior_manager and ("senior" in title_lower and "manager" in title_lower):
        return 0.0

    seniority_hits_title = _contains_any(job.title, seniority_keywords)
    seniority_hits_all = _contains_any(searchable_text, seniority_keywords)
    score += seniority_hits_title * w_seniority_title
    score += (seniority_hits_all - seniority_hits_title) * w_seniority_text

    domain_hits = _contains_any(searchable_text, domain_keywords)
    score += domain_hits * w_domain

    must_hits = _contains_any(searchable_text, must_have_skills)
    score += must_hits * w_must

    nice_hits = _contains_any(searchable_text, nice_to_have_skills)
    score += nice_hits * w_nice

    profile_skills = profile.get("skills", [])
    profile_skill_hits = _contains_any(searchable_text, profile_skills)
    score += profile_skill_hits * w_profile
    resume_fit_hits = _contains_any(searchable_text, resume_terms)
    score += min(resume_fit_hits, 12) * w_resume

    excluded_title_hits = _contains_any(job.title, exclude_title_keywords)
    if excluded_title_hits > 0:
        score -= 35

    if management_focus:
        management_hits = _contains_any(job.title, management_title_keywords)
        if management_hits > 0:
            score += management_hits * 12
        else:
            score -= 30
        if title_hits == 0 and domain_hits < 2:
            score -= 25

    relevance_signals = (
        title_hits + seniority_hits_title + domain_hits + must_hits + profile_skill_hits
    )
    if relevance_signals == 0:
        score -= 25
    if domain_hits == 0 and title_hits == 0:
        score -= 20

    if _contains_any(job.location, locations) > 0:
        score += 8

    if local_commute_focus and is_non_remote:
        if local_hits > 0:
            score += local_hits * w_local_non_remote_boost
        else:
            if hybrid_in_person_local_only:
                return 0.0
            score -= w_non_local_non_remote_penalty

    if job.remote:
        score += 6
    elif remote_only:
        score -= 30

    if sponsorship_required:
        sponsor_pos_hits = _contains_any(searchable_text, sponsorship_positive)
        sponsor_neg_hits = _contains_any(searchable_text, sponsorship_negative)
        score += sponsor_pos_hits * 7
        if sponsor_neg_hits > 0:
            score -= 40

    if score < 0:
        return 0.0
    return round(score, 2)


def rank_jobs(
    jobs: list[Job], profile: dict[str, Any], preferences: dict[str, Any]
) -> list[Job]:
    resume_terms = _extract_resume_terms(profile)
    scored: list[Job] = []
    for job in jobs:
        job.score = score_job(job, profile, preferences, resume_terms)
        scored.append(job)
    scored.sort(key=lambda j: j.score, reverse=True)
    return scored
