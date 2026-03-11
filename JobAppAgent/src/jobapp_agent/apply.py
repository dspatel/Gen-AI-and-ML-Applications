from __future__ import annotations

from datetime import datetime
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from playwright.sync_api import Page, sync_playwright

from .models import Job
from .storage import ensure_dir, read_json, write_json

SUBMIT_SELECTORS = [
    "button:has-text('Submit application')",
    "button:has-text('Submit Application')",
    "button:has-text('Submit')",
    "input[type='submit']",
    "[aria-label*='Submit']",
]

CONFIRMATION_KEYWORDS = [
    "thank you for applying",
    "application submitted",
    "we received your application",
    "your application has been submitted",
]

AUTH_GATE_KEYWORDS = [
    "sign in",
    "log in",
    "login",
    "create account",
    "sign up",
    "register",
    "continue with email",
]

SENSITIVE_QUESTION_KEYWORDS = [
    "gender",
    "race",
    "ethnicity",
    "veteran",
    "disability",
    "sexual orientation",
]


def _infer_site(job: Job) -> str:
    if job.source in {"greenhouse", "lever", "linkedin", "workday"}:
        return job.source
    url = (job.apply_url or job.url).lower()
    if "linkedin.com" in url:
        return "linkedin"
    if "greenhouse" in url:
        return "greenhouse"
    if "lever.co" in url:
        return "lever"
    if "workday" in url:
        return "workday"
    return "generic"


def _try_fill_first(locator, value: str) -> None:
    if not value:
        return
    try:
        if locator.count() > 0:
            locator.first.fill(value, timeout=1200)
    except Exception:
        pass


def _autofill_basic(page: Page, profile: dict[str, Any]) -> None:
    first_name = str(profile.get("first_name", ""))
    last_name = str(profile.get("last_name", ""))
    email = str(profile.get("email", ""))
    phone = str(profile.get("phone", ""))
    linkedin = str(profile.get("linkedin_url", ""))
    github = str(profile.get("github_url", ""))
    city = str(profile.get("city", ""))

    _try_fill_first(page.locator("input[name*='first'], input[id*='first']"), first_name)
    _try_fill_first(page.locator("input[name*='last'], input[id*='last']"), last_name)
    _try_fill_first(page.locator("input[type='email'], input[name*='email']"), email)
    _try_fill_first(page.locator("input[type='tel'], input[name*='phone']"), phone)
    _try_fill_first(
        page.locator("input[name*='linkedin'], input[id*='linkedin']"),
        linkedin,
    )
    _try_fill_first(
        page.locator(
            "input[name*='github'], input[id*='github'], "
            "input[name*='portfolio'], input[id*='portfolio'], "
            "input[name*='website'], input[id*='website']"
        ),
        github,
    )
    _try_fill_first(
        page.locator("input[name*='city'], input[id*='city'], input[name*='location']"),
        city,
    )

    resume_path = str(profile.get("resume_path", "")).strip()
    cover_letter_path = str(profile.get("cover_letter_path", "")).strip()
    upload = page.locator("input[type='file']")

    if resume_path:
        try:
            resume_file = Path(resume_path)
            if resume_file.exists():
                _try_fill_file_inputs(
                    page=page,
                    generic_upload=upload,
                    path=resume_file,
                    kind="resume",
                )
        except Exception:
            pass

    if cover_letter_path:
        try:
            cover_file = Path(cover_letter_path)
            if cover_file.exists():
                _try_fill_file_inputs(
                    page=page,
                    generic_upload=upload,
                    path=cover_file,
                    kind="cover",
                )
        except Exception:
            pass


def _try_fill_file_inputs(page: Page, generic_upload, path: Path, kind: str) -> None:
    kind = kind.lower().strip()
    if kind == "resume":
        targeted = page.locator(
            "input[type='file'][name*='resume'], input[type='file'][id*='resume'], "
            "input[type='file'][name*='cv'], input[type='file'][id*='cv']"
        )
        fallback_index = 0
    else:
        targeted = page.locator(
            "input[type='file'][name*='cover'], input[type='file'][id*='cover'], "
            "input[type='file'][name*='letter'], input[type='file'][id*='letter']"
        )
        fallback_index = 1

    if targeted.count() > 0:
        targeted.first.set_input_files(str(path), timeout=2200)
        return

    if generic_upload.count() > fallback_index:
        generic_upload.nth(fallback_index).set_input_files(str(path), timeout=2200)


def _normalize_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def _build_application_answers(
    profile: dict[str, Any], learned_custom: dict[str, str] | None = None
) -> dict[str, Any]:
    answers = profile.get("application_answers", {})
    if not isinstance(answers, dict):
        answers = {}
    payload: dict[str, Any] = {
        "skip_sensitive_questions": bool(answers.get("skip_sensitive_questions", True)),
        "work_authorized_us": str(answers.get("work_authorized_us", "")).strip(),
        "requires_sponsorship": str(answers.get("requires_sponsorship", "")).strip(),
        "future_sponsorship_required": str(
            answers.get("future_sponsorship_required", "")
        ).strip(),
        "willing_to_relocate": str(answers.get("willing_to_relocate", "")).strip(),
        "salary_expectation": str(answers.get("salary_expectation", "")).strip(),
        "country": str(answers.get("country", "")).strip(),
        "state": str(answers.get("state", "")).strip(),
        "city": str(answers.get("city", profile.get("city", ""))).strip(),
        "linkedin_url": str(answers.get("linkedin_url", profile.get("linkedin_url", ""))).strip(),
        "github_url": str(answers.get("github_url", profile.get("github_url", ""))).strip(),
        "portfolio_url": str(answers.get("portfolio_url", "")).strip(),
        "website_url": str(answers.get("website_url", "")).strip(),
        "custom": {},
    }
    merged_custom: dict[str, str] = {}
    if learned_custom:
        for key, value in learned_custom.items():
            k = _normalize_text(str(key))
            if not k:
                continue
            merged_custom[k] = str(value).strip()
    custom = answers.get("custom", {})
    if isinstance(custom, dict):
        for key, value in custom.items():
            k = _normalize_text(str(key))
            if not k:
                continue
            merged_custom[k] = str(value).strip()
    payload["custom"] = merged_custom
    return payload


def _build_account_bootstrap_settings(profile: dict[str, Any]) -> dict[str, Any]:
    cfg = profile.get("account_bootstrap", {})
    if not isinstance(cfg, dict):
        cfg = {}
    email = str(cfg.get("email", profile.get("email", ""))).strip()
    first_name = str(cfg.get("first_name", profile.get("first_name", ""))).strip()
    last_name = str(cfg.get("last_name", profile.get("last_name", ""))).strip()
    password_env_var = str(cfg.get("password_env_var", "JOBAPP_ACCOUNT_PASSWORD")).strip()
    password_from_cfg = str(cfg.get("create_account_password", "")).strip()
    password = str(os.getenv(password_env_var, "")).strip() or password_from_cfg
    return {
        "enabled": bool(cfg.get("enabled", True)),
        "email": email,
        "first_name": first_name,
        "last_name": last_name,
        "password": password,
        "password_env_var": password_env_var,
        "prompt_for_verification": bool(cfg.get("prompt_for_verification", True)),
    }


def _is_sensitive_question(text: str) -> bool:
    n = _normalize_text(text)
    if not n:
        return False
    return any(key in n for key in SENSITIVE_QUESTION_KEYWORDS)


def _load_learned_answers(path: Path) -> dict[str, str]:
    payload = read_json(path, default={})
    if not isinstance(payload, dict):
        return {}
    custom = payload.get("custom", {})
    if not isinstance(custom, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in custom.items():
        k = _normalize_text(str(key))
        if not k:
            continue
        out[k] = str(value).strip()
    return out


def _save_learned_answers(path: Path, custom: dict[str, str]) -> None:
    clean: dict[str, str] = {}
    for key, value in custom.items():
        k = _normalize_text(str(key))
        if not k:
            continue
        clean[k] = str(value).strip()
    write_json(
        path,
        {
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "custom": clean,
        },
    )


def _capture_field_values_for_learning(
    page: Page, missing_fields: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    script = """
    (missingFields) => {
      const norm = (s) => (s || "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
      const getLabel = (el) => {
        let label = "";
        if (el?.id) {
          const byFor = document.querySelector(`label[for="${el.id.replace(/"/g, '\\"')}"]`);
          if (byFor) label = byFor.innerText || "";
        }
        if (!label && el) {
          const close = el.closest("label");
          if (close) label = close.innerText || "";
        }
        if (!label) label = el?.getAttribute?.("aria-label") || "";
        if (!label) label = el?.getAttribute?.("placeholder") || "";
        if (!label) label = el?.name || el?.id || "";
        return (label || "").trim();
      };
      const esc = (v) => String(v || "").replace(/["\\\\]/g, "\\\\$&");
      const resolveEl = (spec) => {
        const id = String(spec?.id || "");
        const name = String(spec?.name || "");
        if (id) {
          const byId = document.getElementById(id);
          if (byId) return byId;
        }
        if (name) {
          const byName = document.querySelector(`[name="${esc(name)}"]`);
          if (byName) return byName;
        }
        return null;
      };
      const result = [];
      for (const spec of (missingFields || [])) {
        const el = resolveEl(spec);
        if (!el) continue;
        const tag = (el.tagName || "").toLowerCase();
        const type = (el.type || "").toLowerCase();
        let value = "";
        if (type === "checkbox") {
          value = el.checked ? "yes" : "";
        } else if (type === "radio") {
          const name = el.name || "";
          const group = Array.from(document.querySelectorAll('input[type="radio"]')).filter((r) => r.name === name);
          const checked = group.find((r) => r.checked);
          if (checked) value = (checked.value || getLabel(checked) || "").trim();
        } else if (tag === "select") {
          const idx = el.selectedIndex ?? -1;
          if (idx >= 0 && el.options && el.options[idx]) {
            value = (el.options[idx].textContent || el.value || "").trim();
          } else {
            value = (el.value || "").trim();
          }
        } else {
          value = (el.value || "").trim();
        }
        if (!value) continue;
        const question = String(spec?.question || getLabel(el) || "").trim();
        const name = String(spec?.name || el.name || "").trim();
        const id = String(spec?.id || el.id || "").trim();
        const keys = [];
        if (question) keys.push(norm(question));
        if (name) keys.push(norm(name));
        if (id) keys.push(norm(id));
        const key = keys.find(Boolean) || "";
        if (!key) continue;
        result.push({
          key,
          value,
          question,
          name,
          id,
          field_type: type || tag
        });
      }
      return result;
    }
    """
    try:
        data = page.evaluate(script, missing_fields)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _detect_auth_gate(page: Page) -> dict[str, Any]:
    script = """
    (gateKeywords) => {
      const body = (document.body?.innerText || "").toLowerCase();
      const href = (window.location.href || "").toLowerCase();
      const hasPassword = document.querySelectorAll("input[type='password']").length > 0;
      const hasAuthText = gateKeywords.some((k) => body.includes((k || "").toLowerCase()));
      const hasApplySignals =
        document.querySelectorAll("input[type='file']").length > 0 ||
        body.includes("resume") ||
        body.includes("cover letter") ||
        body.includes("submit application");
      const urlAuth = /(signin|sign-in|login|log-in|signup|sign-up|register|create-account|auth)/.test(href);
      const detected = Boolean((hasPassword || urlAuth) && hasAuthText && !hasApplySignals);
      return { detected, hasPassword, hasAuthText, hasApplySignals, urlAuth };
    }
    """
    try:
        data = page.evaluate(script, AUTH_GATE_KEYWORDS)
        if not isinstance(data, dict):
            return {"detected": False}
        return data
    except Exception:
        return {"detected": False}


def _autofill_account_creation(page: Page, profile: dict[str, Any]) -> list[dict[str, Any]]:
    settings = _build_account_bootstrap_settings(profile)
    if not settings["enabled"]:
        return []

    filled: list[dict[str, Any]] = []
    email = str(settings.get("email", ""))
    first_name = str(settings.get("first_name", ""))
    last_name = str(settings.get("last_name", ""))
    password = str(settings.get("password", ""))

    if email:
        before = page.locator("input[type='email'], input[name*='email'], input[id*='email']").count()
        _try_fill_first(
            page.locator("input[type='email'], input[name*='email'], input[id*='email']"),
            email,
        )
        if before > 0:
            filled.append({"field": "email", "value": email})

    if first_name:
        before = page.locator("input[name*='first'], input[id*='first']").count()
        _try_fill_first(page.locator("input[name*='first'], input[id*='first']"), first_name)
        if before > 0:
            filled.append({"field": "first_name", "value": first_name})

    if last_name:
        before = page.locator("input[name*='last'], input[id*='last']").count()
        _try_fill_first(page.locator("input[name*='last'], input[id*='last']"), last_name)
        if before > 0:
            filled.append({"field": "last_name", "value": last_name})

    if password:
        try:
            pw_fields = page.locator("input[type='password']")
            if pw_fields.count() > 0:
                pw_fields.first.fill(password, timeout=1500)
                filled.append({"field": "password", "value": "***"})
            if pw_fields.count() > 1:
                pw_fields.nth(1).fill(password, timeout=1500)
                filled.append({"field": "confirm_password", "value": "***"})
        except Exception:
            pass

    return filled


def _autofill_application_questions(
    page: Page,
    profile: dict[str, Any],
    learned_custom: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    payload = _build_application_answers(profile, learned_custom=learned_custom)
    script = """
    (payload) => {
      const isVisible = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
      const norm = (s) => (s || "").toLowerCase().replace(/[^a-z0-9]+/g, " ").trim();
      const yesSet = new Set(["yes","y","true","1"]);
      const noSet = new Set(["no","n","false","0"]);
      const boolVal = (v) => {
        const n = norm(v);
        if (yesSet.has(n)) return true;
        if (noSet.has(n)) return false;
        return null;
      };
      const getLabel = (el) => {
        let label = "";
        if (el.id) {
          const byFor = document.querySelector(`label[for="${el.id.replace(/"/g, '\\"')}"]`);
          if (byFor) label = byFor.innerText || "";
        }
        if (!label) {
          const close = el.closest("label");
          if (close) label = close.innerText || "";
        }
        if (!label) label = el.getAttribute("aria-label") || "";
        if (!label) label = el.getAttribute("placeholder") || "";
        if (!label) label = el.name || el.id || "";
        return label.trim();
      };
      const isSensitive = (q) => {
        const n = norm(q);
        const keys = ["gender","race","ethnicity","veteran","disability","sexual orientation"];
        return keys.some((k) => n.includes(k));
      };
      const resolveAnswer = (questionText, attrText) => {
        const q = norm(`${questionText} ${attrText}`);
        if (!q) return "";
        const custom = payload.custom || {};
        for (const [k, v] of Object.entries(custom)) {
          if (k && q.includes(k)) return String(v || "");
        }
        if (payload.skip_sensitive_questions && isSensitive(q)) return "";
        if (q.includes("legally authorized") || q.includes("authorized to work") || q.includes("work authorization")) {
          return payload.work_authorized_us || "";
        }
        if (q.includes("future sponsorship")) {
          return payload.future_sponsorship_required || payload.requires_sponsorship || "";
        }
        if (q.includes("require sponsorship") || q.includes("need sponsorship") || q.includes("visa sponsorship") || q.includes("h1b") || q.includes("h-1b")) {
          return payload.requires_sponsorship || "";
        }
        if (q.includes("relocat")) return payload.willing_to_relocate || "";
        if (q.includes("salary")) return payload.salary_expectation || "";
        if (q.includes("linkedin")) return payload.linkedin_url || "";
        if (q.includes("github")) return payload.github_url || "";
        if (q.includes("portfolio")) return payload.portfolio_url || payload.github_url || "";
        if (q.includes("website")) return payload.website_url || payload.github_url || "";
        if (q.includes("country")) return payload.country || "";
        if (q.includes("state")) return payload.state || "";
        if (q.includes("city") || q.includes("location")) return payload.city || "";
        return "";
      };
      const pickInSelect = (el, answer) => {
        const val = String(answer || "");
        const n = norm(val);
        if (!n) return false;
        for (const opt of Array.from(el.options || [])) {
          const text = norm(opt.textContent || "");
          const ov = norm(opt.value || "");
          if (text === n || ov === n || text.includes(n) || (n.includes("yes") && text.includes("yes")) || (n.includes("no") && text.includes("no"))) {
            el.value = opt.value;
            el.dispatchEvent(new Event("input", { bubbles: true }));
            el.dispatchEvent(new Event("change", { bubbles: true }));
            return true;
          }
        }
        return false;
      };
      const pickRadio = (name, answer) => {
        const radios = Array.from(document.querySelectorAll('input[type="radio"]')).filter((r) => r.name === name);
        if (!radios.length) return false;
        const n = norm(answer);
        const b = boolVal(answer);
        for (const r of radios) {
          const label = getLabel(r);
          const text = norm(`${label} ${r.value || ""}`);
          if (
            (b === true && (text.includes("yes") || text.includes("authorized") || text.includes("i am"))) ||
            (b === false && text.includes("no")) ||
            text === n ||
            text.includes(n)
          ) {
            r.click();
            return true;
          }
        }
        return false;
      };

      const fields = Array.from(document.querySelectorAll("input[required], textarea[required], select[required]"));
      const filled = [];
      const visitedRadio = new Set();
      for (const el of fields) {
        if (!isVisible(el)) continue;
        const tag = (el.tagName || "").toLowerCase();
        const type = (el.type || "").toLowerCase();
        const label = getLabel(el);
        const attrText = `${el.name || ""} ${el.id || ""} ${el.placeholder || ""} ${el.getAttribute("aria-label") || ""}`;
        const hasIdentity = Boolean((label || "").trim() || (el.name || "").trim() || (el.id || "").trim() || (el.getAttribute("aria-label") || "").trim() || (el.placeholder || "").trim());
        if (!hasIdentity) continue;
        let isFilled = false;
        if (type === "checkbox") {
          isFilled = !!el.checked;
        } else if (type === "radio") {
          if (visitedRadio.has(el.name || el.id || "")) continue;
          visitedRadio.add(el.name || el.id || "");
          const group = Array.from(document.querySelectorAll('input[type="radio"]')).filter((r) => r.name === el.name);
          isFilled = group.some((r) => r.checked);
        } else if (tag === "select") {
          isFilled = !!el.value;
        } else {
          isFilled = !!String(el.value || "").trim();
        }
        if (isFilled) continue;
        const answer = resolveAnswer(label, attrText);
        if (!answer) continue;
        let didFill = false;
        if (type === "checkbox") {
          const b = boolVal(answer);
          if (b === true && !el.checked) {
            el.click();
            didFill = true;
          }
        } else if (type === "radio") {
          if (el.name) didFill = pickRadio(el.name, answer);
        } else if (tag === "select") {
          didFill = pickInSelect(el, answer);
        } else {
          el.value = String(answer);
          el.dispatchEvent(new Event("input", { bubbles: true }));
          el.dispatchEvent(new Event("change", { bubbles: true }));
          didFill = true;
        }
        if (didFill) {
          filled.push({
            question: label,
            answer_used: String(answer),
            field_type: type || tag,
            name: el.name || "",
            id: el.id || ""
          });
        }
      }
      return filled;
    }
    """
    try:
        data = page.evaluate(script, payload)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _collect_unfilled_required_fields(page: Page) -> list[dict[str, Any]]:
    script = """
    () => {
      const isVisible = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
      const getLabel = (el) => {
        let label = "";
        if (el.id) {
          const byFor = document.querySelector(`label[for="${el.id.replace(/"/g, '\\"')}"]`);
          if (byFor) label = byFor.innerText || "";
        }
        if (!label) {
          const close = el.closest("label");
          if (close) label = close.innerText || "";
        }
        if (!label) label = el.getAttribute("aria-label") || "";
        if (!label) label = el.getAttribute("placeholder") || "";
        if (!label) label = el.name || el.id || "";
        return (label || "").trim();
      };
      const getOptionPreview = (el) => {
        const tag = (el.tagName || "").toLowerCase();
        if (tag === "select") {
          return Array.from(el.options || []).slice(0, 8).map((o) => (o.textContent || "").trim()).filter(Boolean);
        }
        return [];
      };
      const fields = Array.from(document.querySelectorAll("input[required], textarea[required], select[required]"));
      const missing = [];
      const visitedRadio = new Set();
      for (const el of fields) {
        if (!isVisible(el)) continue;
        const tag = (el.tagName || "").toLowerCase();
        const type = (el.type || "").toLowerCase();
        const qLabel = getLabel(el);
        const hasIdentity = Boolean((qLabel || "").trim() || (el.name || "").trim() || (el.id || "").trim() || (el.getAttribute("aria-label") || "").trim() || (el.placeholder || "").trim());
        if (!hasIdentity) continue;
        const key = `${type}:${el.name || el.id || ""}`;
        if (type === "radio" && visitedRadio.has(key)) continue;
        if (type === "radio") visitedRadio.add(key);
        let filled = false;
        if (type === "checkbox" || type === "radio") {
          if (type === "radio") {
            const name = el.name || "";
            const group = Array.from(document.querySelectorAll('input[type="radio"]')).filter((r) => r.name === name);
            filled = group.some((r) => r.checked);
          } else {
            filled = !!el.checked;
          }
        } else if (tag === "select") {
          filled = !!el.value;
        } else {
          filled = !!(el.value || "").trim();
        }
        if (filled) continue;
        missing.push({
          question: qLabel,
          field_type: type || tag,
          name: el.name || "",
          id: el.id || "",
          options_preview: getOptionPreview(el),
        });
      }
      return missing;
    }
    """
    try:
        data = page.evaluate(script)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _has_unfilled_required_fields(page: Page) -> bool:
    return len(_collect_unfilled_required_fields(page)) > 0


def _attempt_submit(page: Page) -> tuple[bool, str]:
    for selector in SUBMIT_SELECTORS:
        try:
            target = page.locator(selector)
            if target.count() == 0:
                continue
            target.first.click(timeout=2000)
            page.wait_for_timeout(1500)
            return True, selector
        except Exception:
            continue
    return False, ""


def _is_likely_submitted(page: Page) -> bool:
    try:
        body = (page.locator("body").inner_text(timeout=2500) or "").lower()
    except Exception:
        return False
    for text in CONFIRMATION_KEYWORDS:
        if text in body:
            return True
    return False


def _canonicalize_apply_url(url: str) -> str:
    if not url:
        return ""
    try:
        parts = urlsplit(url.strip())
        keep_params: list[tuple[str, str]] = []
        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            k = key.lower().strip()
            if k.startswith("utm_"):
                continue
            if k in {"gh_src", "source", "ref", "trk"}:
                continue
            keep_params.append((key, value))
        normalized_query = urlencode(sorted(keep_params))
        return urlunsplit(
            (
                parts.scheme.lower(),
                parts.netloc.lower(),
                parts.path.rstrip("/"),
                normalized_query,
                "",
            )
        )
    except Exception:
        return url.strip().lower()


def _job_registry_key(job: Job, target_url: str) -> str:
    raw_id = ""
    if isinstance(job.raw, dict):
        raw_id = str(job.raw.get("id", "") or job.raw.get("internal_job_id", "")).strip()
    if raw_id:
        return f"{job.source}:{job.company}:{raw_id}"
    canonical = _canonicalize_apply_url(target_url)
    return f"{job.source}:{job.company}:{canonical}"


def _load_applied_registry(path: Path) -> dict[str, Any]:
    payload = read_json(path, default={})
    if not isinstance(payload, dict):
        return {"applied": {}}
    applied = payload.get("applied", {})
    if not isinstance(applied, dict):
        applied = {}
    return {"applied": applied}


def _save_applied_registry(path: Path, registry: dict[str, Any]) -> None:
    write_json(
        path,
        {
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "applied": registry.get("applied", {}),
        },
    )


def _record_applied_job(
    registry: dict[str, Any],
    key: str,
    job: Job,
    site: str,
    target_url: str,
    run_id: str,
    status: str,
) -> None:
    applied = registry.setdefault("applied", {})
    now = datetime.now().isoformat(timespec="seconds")
    existing = applied.get(key, {})
    record = {
        "source": job.source,
        "site": site,
        "company": job.company,
        "title": job.title,
        "url": _canonicalize_apply_url(target_url),
        "first_applied_at": existing.get("first_applied_at", now),
        "last_applied_at": now,
        "last_status": status,
        "run_id": run_id,
        "score": job.score,
    }
    applied[key] = record


def run_guided_apply(
    jobs: list[Job],
    profile: dict[str, Any],
    state_dir: Path,
    min_score: float,
    limit: int,
    auto_fill: bool = True,
    headless: bool = False,
    interactive: bool = True,
    auto_submit: bool = False,
    allowed_auto_submit_sites: set[str] | None = None,
    max_submissions: int = 3,
    dry_run: bool = False,
    log_dir: Path | None = None,
    bootstrap_accounts: bool = False,
    retry_after_bootstrap: bool = True,
    learn_from_manual: bool = True,
    learn_store_path: Path | None = None,
    learn_sensitive: bool = True,
    dedupe_applied: bool = True,
    applied_store_path: Path | None = None,
) -> None:
    selected = [j for j in jobs if j.score >= min_score][:limit]
    if not selected:
        print("No jobs found for apply run. Try lowering --min-score or increasing discovery.")
        return

    if max_submissions < 1:
        raise ValueError("--max-submissions must be >= 1")

    allowed_sites = {s.strip().lower() for s in (allowed_auto_submit_sites or set()) if s.strip()}
    if auto_submit and not allowed_sites:
        allowed_sites = {"greenhouse", "lever"}
    if log_dir is None:
        log_dir = Path.cwd() / "data" / "apply_runs"
    ensure_dir(log_dir)
    if learn_store_path is None:
        learn_store_path = Path.cwd() / "data" / "learning" / "field_memory.json"
    ensure_dir(learn_store_path.parent)
    learned_custom = _load_learned_answers(learn_store_path)
    if applied_store_path is None:
        applied_store_path = Path.cwd() / "data" / "applied_jobs.json"
    ensure_dir(applied_store_path.parent)
    applied_registry = _load_applied_registry(applied_store_path)
    applied_count = len(applied_registry.get("applied", {}))
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"apply_run_{run_id}.json"
    events: list[dict[str, Any]] = []
    submitted_count = 0

    mode = "autopilot" if auto_submit else "guided"
    print(f"Starting {mode} apply for {len(selected)} jobs.")
    if auto_submit:
        print(f"Auto-submit allowed sites: {sorted(allowed_sites)} | max submissions: {max_submissions}")
        if dry_run:
            print("Dry run enabled: no submit clicks will be executed.")
    bootstrap_settings = _build_account_bootstrap_settings(profile)
    if bootstrap_accounts:
        print("Account bootstrap enabled for auth-gated sites.")
        if not bootstrap_settings.get("enabled", True):
            print("Warning: profile.account_bootstrap.enabled is false; bootstrap will be skipped.")
    if learn_from_manual:
        print(
            f"Learning enabled. Learned answer keys loaded: {len(learned_custom)} "
            f"from {learn_store_path}"
        )
    if dedupe_applied:
        print(
            f"Applied-job dedupe enabled. Known applied jobs: {applied_count} "
            f"from {applied_store_path}"
        )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)

        for idx, job in enumerate(selected, start=1):
            if auto_submit and submitted_count >= max_submissions:
                print(f"Reached max submissions ({max_submissions}); ending run.")
                break

            site = _infer_site(job)
            state_file = state_dir / f"{site}.json"
            if state_file.exists():
                context = browser.new_context(storage_state=str(state_file))
            else:
                context = browser.new_context()

            page = context.new_page()
            target_url = job.apply_url or job.url
            registry_key = _job_registry_key(job, target_url)
            event: dict[str, Any] = {
                "idx": idx,
                "site": site,
                "source": job.source,
                "company": job.company,
                "title": job.title,
                "score": job.score,
                "url": target_url,
                "registry_key": registry_key,
                "status": "started",
            }
            print("")
            print(f"[{idx}/{len(selected)}] {job.title} @ {job.company} | score={job.score}")
            print(f"URL: {target_url}")
            if dedupe_applied and registry_key in applied_registry.get("applied", {}):
                print("Skipping: already submitted in previous run.")
                event["status"] = "skipped_already_applied"
                events.append(event)
                context.close()
                continue
            if interactive:
                ans = input("Open this application? [Y/n/q]: ").strip().lower()
                if ans == "q":
                    event["status"] = "stopped_by_user"
                    events.append(event)
                    context.close()
                    break
                if ans == "n":
                    event["status"] = "skipped_by_user"
                    events.append(event)
                    context.close()
                    continue

            question_fills: list[dict[str, Any]] = []
            try:
                page.goto(target_url, wait_until="domcontentloaded")
                time.sleep(1.2)
                if auto_fill:
                    _autofill_basic(page, profile)
                    question_fills = _autofill_application_questions(
                        page, profile, learned_custom=learned_custom
                    )
                    print("Autofill attempt complete (best effort).")
                    if question_fills:
                        print(f"Filled {len(question_fills)} required question fields from config answers.")
                event["autofilled_questions"] = question_fills
            except Exception as exc:
                event["status"] = "failed_open"
                event["reason"] = str(exc)
                events.append(event)
                context.close()
                continue

            if not auto_submit:
                if interactive:
                    print("Review the page and submit manually in the browser.")
                    input("Press Enter when finished to continue to next job...")
                    event["status"] = "manual_review_completed"
                else:
                    print("Skipping: non-interactive mode requires --auto-submit.")
                    event["status"] = "skipped_non_interactive_without_auto_submit"
                events.append(event)
                context.close()
                continue

            if site not in allowed_sites:
                print(f"Skipping auto-submit: site '{site}' not in allowlist.")
                event["status"] = "skipped_site_not_allowed"
                events.append(event)
                context.close()
                continue

            gate_info = _detect_auth_gate(page)
            event["auth_gate"] = gate_info
            if bool(gate_info.get("detected", False)):
                print("Account/login gate detected before application form.")
                if not bootstrap_accounts or not bootstrap_settings.get("enabled", True):
                    event["status"] = "requires_account_setup"
                    events.append(event)
                    context.close()
                    continue

                bootstrap_fills = _autofill_account_creation(page, profile)
                event["account_bootstrap_fills"] = bootstrap_fills
                if bootstrap_fills:
                    print(f"Bootstrap autofilled {len(bootstrap_fills)} account fields.")

                if not interactive:
                    event["status"] = "requires_interactive_verification"
                    event["reason"] = "Auth gate requires CAPTCHA/OTP/manual verification."
                    events.append(event)
                    context.close()
                    continue

                if bootstrap_settings.get("prompt_for_verification", True):
                    print("Complete account creation/login/MFA in the browser, then return here.")
                    input("Press Enter when verification is complete...")
                    try:
                        context.storage_state(path=str(state_file))
                        event["session_saved_after_bootstrap"] = str(state_file)
                    except Exception:
                        event["session_saved_after_bootstrap"] = ""

                if retry_after_bootstrap:
                    try:
                        page.goto(target_url, wait_until="domcontentloaded")
                        time.sleep(1.0)
                        if auto_fill:
                            _autofill_basic(page, profile)
                            after_retry_fills = _autofill_application_questions(
                                page, profile, learned_custom=learned_custom
                            )
                            if after_retry_fills:
                                question_fills.extend(after_retry_fills)
                                event["autofilled_questions"] = question_fills
                    except Exception as exc:
                        event["status"] = "failed_retry_after_bootstrap"
                        event["reason"] = str(exc)
                        events.append(event)
                        context.close()
                        continue

                gate_after = _detect_auth_gate(page)
                event["auth_gate_after_bootstrap"] = gate_after
                if bool(gate_after.get("detected", False)):
                    event["status"] = "blocked_auth_gate_persisted"
                    events.append(event)
                    context.close()
                    continue

            missing_fields = _collect_unfilled_required_fields(page)
            learned_items: list[dict[str, Any]] = []
            if missing_fields and interactive and learn_from_manual:
                ans = input(
                    "Required fields missing. Learn answers from manual fill now? [y/N]: "
                ).strip().lower()
                if ans == "y":
                    print("Please fill the missing fields manually in the browser.")
                    input("Press Enter here once you are done...")
                    learned_items = _capture_field_values_for_learning(page, missing_fields)
                    learned_added = 0
                    learned_keys: list[str] = []
                    learned_skipped_sensitive: list[str] = []
                    for item in learned_items:
                        key = _normalize_text(str(item.get("key", "")))
                        value = str(item.get("value", "")).strip()
                        question = str(item.get("question", ""))
                        if not key or not value:
                            continue
                        if (not learn_sensitive) and _is_sensitive_question(question):
                            learned_skipped_sensitive.append(question or key)
                            continue
                        learned_custom[key] = value
                        learned_keys.append(key)
                        learned_added += 1
                    if learned_added > 0:
                        _save_learned_answers(learn_store_path, learned_custom)
                        print(f"Learned {learned_added} answers for future runs.")
                    if learned_skipped_sensitive:
                        print(
                            "Skipped sensitive fields during learning "
                            "(enable with --learn-sensitive if you want them learned)."
                        )
                    event["learned_items"] = learned_items
                    event["learned_keys_added"] = learned_keys
                    event["learned_skipped_sensitive"] = learned_skipped_sensitive

                    if auto_fill:
                        question_fills_after_learn = _autofill_application_questions(
                            page, profile, learned_custom=learned_custom
                        )
                        if question_fills_after_learn:
                            question_fills.extend(question_fills_after_learn)
                            event["autofilled_questions"] = question_fills
                    missing_fields = _collect_unfilled_required_fields(page)

            if missing_fields:
                print("Blocked auto-submit: required fields still missing.")
                event["status"] = "blocked_required_fields"
                event["missing_required_fields"] = missing_fields
                if missing_fields:
                    preview = ", ".join(
                        [f.get("question") or f.get("name") or "unknown" for f in missing_fields[:3]]
                    )
                    print(f"Missing required examples: {preview}")
                events.append(event)
                context.close()
                continue

            if interactive:
                ans = input("Attempt auto-submit now? [y/N]: ").strip().lower()
                if ans != "y":
                    event["status"] = "skipped_autosubmit_declined"
                    events.append(event)
                    context.close()
                    continue

            if dry_run:
                print("Dry-run: would click submit here.")
                event["status"] = "dry_run_would_submit"
                events.append(event)
                context.close()
                continue

            clicked, selector = _attempt_submit(page)
            if not clicked:
                print("Auto-submit failed: submit button not found/clickable.")
                event["status"] = "failed_submit_button_not_found"
                events.append(event)
                context.close()
                continue

            submitted_count += 1
            confirmed = _is_likely_submitted(page)
            event["status"] = "submitted_confirmed" if confirmed else "submitted_unconfirmed"
            event["submit_selector"] = selector
            _record_applied_job(
                registry=applied_registry,
                key=registry_key,
                job=job,
                site=site,
                target_url=target_url,
                run_id=run_id,
                status=event["status"],
            )
            events.append(event)
            print(f"Auto-submit click sent ({submitted_count}/{max_submissions}).")
            context.close()

        browser.close()

    write_json(
        log_path,
        {
            "run_id": run_id,
            "min_score": min_score,
            "limit": limit,
            "auto_fill": auto_fill,
            "auto_submit": auto_submit,
            "interactive": interactive,
            "dry_run": dry_run,
            "bootstrap_accounts": bootstrap_accounts,
            "retry_after_bootstrap": retry_after_bootstrap,
            "learn_from_manual": learn_from_manual,
            "learn_store_path": str(learn_store_path),
            "learn_sensitive": learn_sensitive,
            "learned_key_count": len(learned_custom),
            "dedupe_applied": dedupe_applied,
            "applied_store_path": str(applied_store_path),
            "known_applied_count": len(applied_registry.get("applied", {})),
            "allowed_auto_submit_sites": sorted(list(allowed_sites)),
            "max_submissions": max_submissions,
            "submitted_count": submitted_count,
            "events": events,
        },
    )
    _save_applied_registry(applied_store_path, applied_registry)
    print(f"Saved apply run log: {log_path}")
