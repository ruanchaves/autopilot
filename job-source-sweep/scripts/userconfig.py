"""The user's config.yaml at the repo root: everything personal (candidate, resume, role, pay,
banned companies, data folder) and every skill's tunables. The three skills' scripts import this
module, so nothing personal lives in code or in the subagent references."""

from __future__ import annotations

import os
import re
import sys
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = Path(os.environ.get("AUTOPILOT_CONFIG") or ROOT / "config.yaml")

REQUIRED = ("home", "candidate", "resume", "role", "pay")


def fix_path(value: str) -> Path:
    """Accept C:\\... or C:/... paths when running under WSL."""
    m = re.match(r"^([A-Za-z]):[\\/](.*)$", str(value or ""))
    if os.name != "nt" and m:
        value = f"/mnt/{m[1].lower()}/{m[2].replace(chr(92), '/')}"
    return Path(value).expanduser()


@lru_cache(maxsize=1)
def load() -> dict:
    try:
        import yaml  # PyYAML
    except ImportError:
        sys.exit("PyYAML is needed to read config.yaml: pip install pyyaml")
    if not CONFIG_PATH.exists():
        sys.exit(f"config.yaml not found at {CONFIG_PATH}: copy and edit the one in the repo")
    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    missing = [k for k in REQUIRED if not cfg.get(k)]
    if missing:
        sys.exit(f"config.yaml is missing: {', '.join(missing)}")
    for k in ("candidate", "resume", "role", "pay", "tracker", "sweep", "autopilot", "recruiter_outreach"):
        cfg.setdefault(k, {})
    cfg.setdefault("banned_companies", [])
    cfg.setdefault("setup_done", [])   # skills whose first-run setup has run
    c = cfg["candidate"]
    c.setdefault("region_aliases", [c.get("region")] if c.get("region") else [])
    c.setdefault("excluded_contract_types", [])
    c.setdefault("stories", [])
    return cfg


def setup_done() -> list:
    """Names of the skills whose first-run setup has run (config.yaml `setup_done`)."""
    return list(load().get("setup_done") or [])


def home() -> str:
    return str(fix_path(load()["home"]))


def candidate() -> dict:
    return load()["candidate"]


def pay() -> dict:
    return load()["pay"]


def banned_companies() -> list[str]:
    return list(load().get("banned_companies") or [])


def country() -> str:
    return candidate().get("country", "")


def region() -> str:
    return candidate().get("region", "")


def salary_policy() -> str:
    """The pay rule as a sentence the apply subagent follows."""
    p = pay()
    t, ask = int(p.get("target_usd_month", 0)), int(p.get("ask_usd_month") or p.get("target_usd_month", 0))
    return (f"Target: USD {t:,}+/month. Where free text is allowed, write '{p.get('free_text_answer', 'Open / negotiable')}'. "
            f"Where a number is required: if the posting shows a range whose top is at least {t:,} USD/month, give the top of "
            f"that range; otherwise give {ask:,} USD/month. Never give less than {t:,}. Convert to the unit the form asks for "
            f"(e.g. {t * 12:,} USD/year, or hourly at 160 h/month).")


def profile() -> dict:
    """The applicant profile the answer bank used to hold: candidate facts in form-answer shape."""
    c = candidate()
    return {
        "full_name": c.get("full_name", ""), "first_name": c.get("first_name", ""), "last_name": c.get("last_name", ""),
        "email": c.get("email", ""), "phone": c.get("phone", ""), "phone_country": c.get("phone_country", ""),
        "city": c.get("city", ""), "country": c.get("country", ""), "region": c.get("region", ""),
        "timezone": c.get("timezone", ""), "linkedin": c.get("linkedin", ""), "github": c.get("github", ""),
        "website": c.get("website", ""), "languages": c.get("languages", ""),
        "years_experience_total": f"{c.get('years_experience', '')}+ (professional since {c.get('professional_since', '')})",
        "headline": c.get("headline", ""), "level": c.get("level", ""),
        "salary_policy": salary_policy(), "availability": c.get("availability", ""),
        "work_authorization": c.get("work_authorization", ""), "sponsorship": c.get("sponsorship", ""),
        "employment_types": c.get("employment_types", ""), "excluded_contract_types": c.get("excluded_contract_types", []),
        "working_hours": c.get("working_hours", ""), "relocation": c.get("relocation", ""),
        "location_pitch": c.get("location_pitch", ""), "pitch": c.get("pitch", ""), "stories": c.get("stories", []),
        "cover_letter": "Optional cover-letter fields follow the autopilot cover_letter policy.",
    }


def snapshot() -> dict:
    """The short candidate snapshot scoring and outreach subagents get in their context."""
    c = candidate()
    return {k: c.get(k, "") for k in ("headline", "years_experience", "level", "core_areas", "domains", "stack",
                                       "country", "region", "region_aliases", "timezone", "languages", "native_language",
                                       "pitch", "location_pitch", "stories", "employment_types", "excluded_contract_types")}
