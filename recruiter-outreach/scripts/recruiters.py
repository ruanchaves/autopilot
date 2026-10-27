#!/usr/bin/env python3
"""Recruiter outreach: a ranked database of LinkedIn recruiters who place people in the target role from
the candidate region or worldwide, consumed by connection requests (20 a day) and InMails (while credits last).
Separate from job-autopilot: nothing here depends on an application.

    recruiters.py discover-plan [--max-leads 30]          # queries + result file for the discovery subagent
    recruiters.py ingest        --result FILE               # discovered leads with their signals -> leads.json
    recruiters.py score                                     # recompute every lead's score (after a settings change)
    recruiters.py plan          [--n 30]                    # today's ranked pile, quotas, task file for the outreach subagent
    recruiters.py check         --kind connect|inmail|message --profile URL
    recruiters.py record        --result FILE               # outreach result: contacts, counters, credits
    recruiters.py mark          --profile URL --status replied|skipped|contacted [--note TEXT]
    recruiters.py outcome       --profile URL | --name NAME --event accepted|replied|positive|call|declined [--note TEXT]
    recruiters.py outcome       --profile URL | --name NAME --event quoted --usd N [--company C]   # a pay quote
    recruiters.py queries       [--top 25]                  # which searches found recruiters quoting the target pay
    recruiters.py ceilings      [--set COMPANY --usd N]     # highest pay each company ever quoted; under the floor = gated
    recruiters.py import-quotes --file FILE                 # backfill pay quotes collected from the inbox
    recruiters.py network-rank  [--file network_queue.json] # 1st-degree queue: funded-client recruiters first
    recruiters.py targets       [--top 20]                  # company lane: funded companies hiring the role, best first
    recruiters.py funding-plan                              # context for the funding-research subagent
    recruiters.py ingest-funded --result FILE               # its result -> funded_companies.json
    recruiters.py refresh-boards [--max-age-hours 20]       # read registry companies' ATS boards for matching roles
    recruiters.py import-contacts --file FILE               # seed contacts from job-autopilot's contacts.json
A run is done only when `status` says done: connection requests and InMails for the day are all used, and every
contactable 1st-degree lead has had its plain message. Messages to 1st-degree connections never count toward the
connection-request budget.
    recruiters.py dashboard                                 # dashboard.json for the role explorer's Outreach view
    recruiters.py status
    recruiters.py companies                                 # people contacted per company/agency vs max_per_company

Settings come from config.yaml at the repo root (recruiter_outreach, role, pay, banned_companies).
Everything else lives in <home>/recruiters/:
    leads.json      every recruiter found: profile, signals, evidence, score, status, the search that found
                    them (source, sources) and what they did after contact (outcome)
    contacts.json   everyone ever contacted (seeded from autopilot), with the exact message sent
    pay_ceilings.json  the highest monthly USD pay each company or agency ever quoted (the pay gate)
    funded_companies.json  companies found from funding news, portfolios and batches: rounds, ATS board, matching roles
    pacing.json     today's counters, last-send times, InMail credits last seen, last acceptance check
    queries.json    the last `queries` report (also embedded in dashboard.json)
    work/<date>/    discovery and outreach result files
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import unicodedata
import zlib
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

SWEEP = Path(__file__).resolve().parents[2] / "job-source-sweep" / "scripts"
sys.path.insert(0, str(SWEEP))
import jobsweep as js  # noqa: E402
import enrich  # noqa: E402
import userconfig  # noqa: E402  (config.yaml at the repo root)
# shared with job-autopilot: employer-type patterns, posting regions, ATS board readers, people checks
from companies import (VENDOR as _VENDOR, PLATFORM as _PLATFORM, STAFF_AUG as _STAFF_AUG,  # noqa: E402
                       PREMIUM as _PREMIUM, NOT_EMPLOYER, ENG_TITLE as _ENG_TITLE, NOT_ENG as _NOT_ENG,
                       REGIONS, TARGET_REGIONS, NEIGHBOURS, BOARD_KINDS, region_of, usd_month, board_roles)
import companies as shared_companies  # noqa: E402  (recruiters.py has its own companies() for contact counts)

NOW = datetime.now(timezone.utc)
TODAY = NOW.strftime("%Y-%m-%d")


# ------------------------------------------------------------------ files

def rc_dir(home: str) -> Path:
    path = js.fix_path(home) / "recruiters"
    path.mkdir(parents=True, exist_ok=True)
    return path


def work_dir(home: str) -> Path:
    path = rc_dir(home) / "work" / TODAY
    path.mkdir(parents=True, exist_ok=True)
    return path


def load(home: str, name: str, default):
    return enrich.read_json(rc_dir(home) / name, default)


def save(home: str, name: str, data) -> None:
    enrich.write_json(rc_dir(home) / name, data, indent=1)


def emit(obj) -> int:
    print(json.dumps(obj, indent=1, ensure_ascii=False))
    return 0


DEFAULT_SETTINGS = {
    "connect_per_day": 20,     # the user's hard limit on connection requests per UTC day
    "inmail_per_day": 15,      # a second guard; the real bound is the credits LinkedIn shows
    "message_per_day": None,   # plain messages to 1st-degree connections: None = no daily cap, 0 = none at all;
                               # they never count toward connect_per_day
    "gap_seconds": {"connect": 90, "inmail": 120, "message": 60},
    "min_score": 55,           # leads below this are not contacted
    "max_per_company": 3,      # people ever contacted at one company or agency (companies.json keeps the count)
    "stale_days": 60,          # a lead's signals are re-probed after this many days
    "people_queries": [],      # from config.yaml recruiter_outreach.people_queries
    "post_queries": [],
    "required_terms": [],      # words every discovery pass must search with (e.g. "nearshore"); ingest warns when missing
    "cta_styles": ["open", "cv"],  # how first-touch messages close; rotated per lead, compared by `recruiters queries`
    # the company lane: in-house recruiters and hiring managers at funded companies hiring for the target role,
    # found company-first from job-source-sweep's roles (references/pay-tier-research.md, "Company lane")
    "company_lane_share": 0.25,       # share of the day's connection requests + InMails reserved for the lane
    "company_lane_batches": 14,       # sweep batches (run dates) the company targets are read from
    "company_lane_per_round": 8,      # companies handed to discovery per round
    "company_lane_cooldown_days": 30,  # a company searched in the lane is not searched again for this long
    "company_lane_title_terms": [],   # posting-title words that count as the target role (default: role.title's words)
    "company_lane_min_score": 55,     # lane leads are scored on their own rubric (LANE_PARTS_MAX), with their own line
    # the funded-company registry (funded_companies.json): companies found from funding news, portfolio pages and
    # accelerator batches, independent of the job sweep (references/funding.md)
    "company_lane_research_days": 7,      # funding research is due again after this long
    "company_lane_research_per_round": 30,  # companies one research pass checks
    "company_lane_funding_days": 180,     # a round newer than this counts as recent (ranks first, and research window)
    "company_lane_min_round_usd": 3000000,  # smaller announced rounds are not worth a research slot
    "company_lane_board_refresh_hours": 20,  # refresh-boards skips a company whose board was read more recently
    # filled in from config.yaml by settings(): target_role, pay_target_usd_month, banned_companies
}


def settings(home: str) -> dict:
    """DEFAULT_SETTINGS under config.yaml's recruiter_outreach section, plus the shared role, pay
    and banned-company values."""
    cfg = userconfig.load()
    s = dict(DEFAULT_SETTINGS)
    s.update(cfg.get("recruiter_outreach") or {})
    pay = userconfig.pay()
    s.update(target_role=(cfg.get("role") or {}).get("title", ""), pay_target_usd_month=pay.get("target_usd_month", 0),
             pay_floor_usd_month=pay.get("skip_below_usd_month", 0), banned_companies=userconfig.banned_companies())
    return s


def squash(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


_CO_SUFFIX = re.compile(r"\b(inc|llc|ltd|ltda|limited|corp|corporation|co|company|group|gmbh|sa|s\.a\.|srl|plc|lp)\b\.?")
_CO_TRAIL = {"systems", "system", "technologies", "technology", "tech", "solutions", "labs", "consulting", "software",
             "digital", "services", "staffing", "recruitment", "recruiting", "talent", "global", "international",
             "latam", "brasil", "brazil", "usa", "us"}
_CO_NOISE = {"", "unknown", "na", "n/a", "none", "self", "freelance", "independent", "selfemployed",
             "notstated", "notspecified", "notlisted", "undisclosed", "confidential", "unclear"}


def company_key(name: str) -> str:
    """One key per employer or agency: 'Strider (freelance)', 'Strider Inc.' and 'strider' all map to 'strider'.
    Empty for unknown or self-employed, which the cap ignores."""
    n = (name or "").lower()
    n = re.sub(r"\(.*?\)|\[.*?\]", " ", n)            # parentheticals: "(CloudX)", "(freelance)"
    n = re.split(r"\s+[/|,@]\s+|\s+[-\u2013]\s+|\s+via\s+", n)[0]   # "Epidata / Applica Corp" -> epidata
    n = _CO_SUFFIX.sub(" ", n)
    words = n.split()
    while len(words) > 1 and words[-1] in _CO_TRAIL:   # "EPAM Systems" / "10Pearls LATAM" -> epam / 10pearls
        words.pop()
    k = squash(" ".join(words))
    return "" if k in _CO_NOISE else k


def companies_from(cts: dict) -> dict:
    """The per-company memory, derived from every contact ever sent (any purpose, any channel)."""
    out: dict = {}
    for who, c in cts.items():
        if c.get("status") != "sent":
            continue
        k = company_key(c.get("company", ""))
        if not k:
            continue
        e = out.setdefault(k, {"name": (c.get("company") or "").strip(), "contacted": 0, "profiles": [], "last": ""})
        e["contacted"] += 1
        e["profiles"].append(who)
        e["last"] = max(e["last"], c.get("at", "") or "")
    return out


def companies(home: str, cts: dict | None = None) -> dict:
    """companies.json, rebuilt from contacts.json when missing or when `cts` is given."""
    if cts is None:
        data = load(home, "companies.json", None)
        if data is not None:
            return data
        cts = contacted(home)
    data = companies_from(cts)
    save(home, "companies.json", data)
    return data


def company_room(home: str, st: dict, company: str, cos: dict | None = None) -> tuple[int, str]:
    """How many more people may be contacted at `company` under max_per_company, and the key."""
    k = company_key(company)
    if not k:
        return 10 ** 6, ""
    cos = companies(home) if cos is None else cos
    return max(0, int(st["max_per_company"]) - cos.get(k, {}).get("contacted", 0)), k


def is_banned(company: str, st: dict) -> bool:
    c = squash(company)
    return len(c) >= 3 and any(c.startswith(squash(b)) for b in st["banned_companies"] if squash(b))


def norm_profile(url: str) -> str:
    m = re.search(r"linkedin\.com/(in|company)/([^/?#]+)", url or "", re.I)
    return f"linkedin.com/{m[1].lower()}/{m[2].lower()}" if m else (url or "").strip().lower()


# ------------------------------------------------------------- attribution
# Every lead remembers the search that first surfaced it (`source`) and every later one (`sources`), and every
# contacted lead collects outcome events (accepted, replied, positive, call, declined) from the sender's
# acceptance check and from linkedin-inbox. `recruiters queries` joins the two, so the searches whose recruiters
# accept and answer can be told apart from the ones that only fill the pile.

_PAGE_NOTE = re.compile(r"\s*\((?:page|p\.?)\s*\d+\)\s*$", re.I)


def source_key(found_via: str) -> str:
    """The canonical key of the search that found a lead, in the same namespace as discovery-runs.json:
    'people:<phrasing>', 'posts:<phrasing>' (hashtags included), 'snowball:<company>', 'also_viewed:<lead>',
    'jobs:<keywords> @ <location>', 'jobs:posting', 'engagement:post'. Page notes like '(page 3)' are dropped,
    so every page of one phrasing counts toward that phrasing. Empty when unknown."""
    s = (found_via or "").strip()
    if ":" not in s:
        return ""
    kind, rest = (x.strip() for x in s.split(":", 1))
    kind = kind.lower()
    if kind == "jobs":
        m = re.search(r"https?://\S+", rest)
        url = m[0] if m else rest
        if "/jobs/search" in url:
            qs = parse_qs(urlparse(url).query)
            kw, loc = (qs.get("keywords") or [""])[0], (qs.get("location") or [""])[0]
            return f"jobs:{kw}" + (f" @ {loc}" if loc else "")
        return "jobs:posting" if "/jobs/view" in url else "jobs:" + _PAGE_NOTE.sub("", rest)
    rest = _PAGE_NOTE.sub("", rest)
    if kind == "snowball":
        m = re.match(r"(.+?)\s+people also viewed\b", rest, re.I)
        if m:
            return "also_viewed:" + m[1].strip()
        rest = re.sub(r"\s*\(.*\)\s*$", "", rest)
        return "snowball:" + re.sub(r"\s+(recruiters?|talent)$", "", rest, flags=re.I)
    if kind == "engagement":
        return "engagement:post"
    return f"{kind}:{rest}"


def avenue(key: str) -> str:
    kind, _, rest = (key or "").partition(":")
    if not kind or kind == "unattributed":
        return "unattributed"
    return "hashtag" if kind == "posts" and "#" in rest else kind


def lead_source(v: dict | None) -> str:
    v = v or {}
    return v.get("source") or source_key(v.get("found_via", ""))


def cta_for(key: str, styles: list) -> str:
    """The closing style for a lead's first message: stable per lead, spread evenly across `styles`."""
    return styles[zlib.crc32(key.encode()) % len(styles)] if styles else ""


STAGES = ("accepted", "replied", "positive", "call")  # each stage implies the ones before it
# `quoted`: the recruiter stated the pay of a role (monthly USD in `quoted_usd_month`, the highest quote kept).
# It is the outcome this skill optimises for: recruiters quoting the target or more, not recruiters who reply.
EVENTS = STAGES + ("declined", "quoted")


def add_event(lead: dict, event: str, at: str, note: str = "", usd: int = 0) -> bool:
    """Record an outcome event on a lead; the earliest time wins. A later stage implies the earlier ones (a reply
    means the request was accepted), except that InMails and messages to 1st-degree leads have no request to accept.
    Returns True when anything changed."""
    out = lead.setdefault("outcome", {})
    implied = list(STAGES[:STAGES.index(event) + 1]) if event in STAGES else ["accepted", "replied", event]
    changed = False
    if event == "quoted" and usd and usd > (out.get("quoted_usd_month") or 0):
        out["quoted_usd_month"] = int(usd)
        changed = True
    is_connect = (lead.get("contact") or {}).get("channel") == "connect"
    for e in implied:
        if e == "accepted" and e != event and not is_connect:
            continue
        k = e + "_at"
        if not out.get(k) or at < out[k]:
            out[k] = at
            changed = True
    notes = out.setdefault("notes", [])
    if note and not any(n.endswith(f"{event}: {note}") for n in notes):
        notes.append(f"{at[:10]} {event}: {note}")
        changed = True
    if not notes:
        out.pop("notes")
    if event != "accepted" and lead.get("status") in ("new", "contacted"):
        lead["status"] = "replied"
    if out.get("replied_at"):
        lead["replied_at"] = out["replied_at"]  # the field the dashboard has always shown
    return changed


def _plain(s: str) -> str:
    return squash(unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode())


def find_lead(leads: dict, profile: str = "", name: str = "") -> str:
    """A lead's key from its profile URL, or else from its name among contacted leads (messaging often links
    profiles by member id, not the public slug). Empty when there is no unique match."""
    who = norm_profile(profile) if profile else ""
    if who in leads:
        return who
    n = _plain(name)
    hits = [k for k, v in leads.items() if n and v.get("contact") and _plain(v.get("name", "")) == n]
    return hits[0] if len(hits) == 1 else ""


# ---------------------------------------------------------------- scoring
# Five questions: is the recruiter explicitly hiring in the candidate's region and/or worldwide? active on
# LinkedIn? posting roles aligned with the target role? posting roles aligned with the target pay? recruiting for
# clients that pay at that level (funded product companies, boutique search) rather than staff augmentation?
# The fifth question, the pay-ceiling gate and the reweighting come from a study of 354 inbox threads in which
# 120 recruiters quoted pay: region predicted nothing about pay, the client behind the recruiter did
# (references/pay-tier-research.md).

SCOPE = {"latam": 30, "worldwide": 30, "nearshore": 15, "remote_unclear": 12, "unknown": 4, "local_only": 0}
PAY = {"at_or_above": 20, "near": 8, "unknown": 5, "below": 0}
TIER = {"funded_product": 20, "boutique_search": 20, "unclear": 8, "staff_aug": 0}
PARTS_MAX = {"scope": 30, "active": 10, "role": 20, "pay": 20, "tier": 20}

# Fallback for leads probed before client_tier and the vendor/platform flags existed: read the headline, company
# and evidence (the patterns live in job-source-sweep/scripts/companies.py). An explicit signal from discovery always
# wins over these patterns.


def lead_text(lead: dict) -> str:
    return " ".join([lead.get("headline") or "", lead.get("company") or ""] + list(lead.get("evidence") or []))


def infer_tier(lead: dict) -> str:
    """client_tier from text, for leads the discovery subagent probed before the signal existed."""
    t = lead_text(lead)
    if _VENDOR.search(t) or _STAFF_AUG.search(t):
        return "staff_aug"
    return "boutique_search" if _PREMIUM.search(t) else "unclear"


def score_parts(sig: dict, lead: dict | None = None) -> dict:
    lead = lead or {}
    scope = SCOPE.get(sig.get("hiring_scope") or "unknown", 4)
    days = sig.get("last_activity_days")
    active = 2 if days is None else 10 if days <= 7 else 7 if days <= 30 else 4 if days <= 90 else 0
    role_n = int(sig.get("role_posts_90d") or 0)
    adj_n = int(sig.get("adjacent_posts_90d") or 0)
    role = 20 if role_n >= 2 else 13 if role_n == 1 else 5 if adj_n else 0
    pay_sig = sig.get("pay_signal") or "unknown"
    if pay_sig == "near" and sig.get("pay_basis") == "hourly":
        pay_sig = "below"  # every hourly quote seen sat at or under the 8k band; an hourly 'near' is not near
    pay = PAY.get(pay_sig, 5)
    tier = TIER.get(sig.get("client_tier") or infer_tier(lead), 8)
    text = lead_text(lead)
    vendor = sig["volume_vendor"] if "volume_vendor" in sig else bool(_VENDOR.search(text))
    platform = sig["task_platform"] if "task_platform" in sig else bool(_PLATFORM.search(text))
    penalty = (-15 if sig.get("local_rate_shop") else 0) + (-20 if vendor else 0) + (-20 if platform else 0)
    return {"scope": scope, "active": active, "role": role, "pay": pay, "tier": tier, "penalty": penalty}


def total(parts: dict) -> int:
    return max(0, min(100, sum(parts.values())))


# The company lane's own rubric. A lane lead's company was chosen because its posting matched, so scoring the
# posting again (region, role, pay, client) gave every lane lead 64-92 whoever the person was. Here the company is
# worth 40 and the person 60: does this person own the posted role, are they active, do they post about hiring.
# Lane scores are compared only with other lane scores (plan fills each lane from its own pile).
LANE_REGION = {"latam": 15, "worldwide": 15, "remote_unclear": 6, "nearshore": 6, "unknown": 3, "local_only": 0}
OWNER = {"direct": 30, "adjacent": 15, "distant": 0, "unknown": 10}
VOICE = {"role": 15, "general": 8, "none": 0, "unknown": 4}
LANE_PARTS_MAX = {"region": 15, "pay": 15, "role": 10, "owner": 30, "active": 15, "voice": 15}

# Fallbacks for lane leads probed before role_owner and hiring_voice existed
_OWNER_HM = re.compile(r"\b(head of (\w+ )?(ai|ml|machine learning|data|engineering|applied)|director,? (of )?(ai|ml|engineering|"
                       r"machine learning|applied)|(engineering|ml|ai|data science) manager|manager,? (ml|ai|machine learning)|"
                       r"cto|chief (technology|ai|scientist)|co-?founder|founder|vp,? (of )?engineering|building\b)", re.I)
_OWNER_REC = re.compile(r"\b(technical|tech|engineering|ai|ml|r&d|gtm and engineering)[ /&-]*(recruit\w*|talent|sourc\w*)|"
                        r"(recruit\w*|talent)[^|@]{0,30}\b(ai|ml|engineering|technical)\b", re.I)
_HIRING_TALK = re.compile(r"\b(we'?re hiring|hiring|join (our|my) team|open roles?|looking for)\b", re.I)


def infer_owner(lead: dict) -> str:
    head = lead.get("headline") or ""
    if lead_type(lead) == "hiring_manager":
        return "direct" if _OWNER_HM.search(head) else "unknown"
    return "direct" if _OWNER_REC.search(head) else "adjacent"


def lane_parts(sig: dict, lead: dict | None = None) -> dict:
    lead = lead or {}
    region = LANE_REGION.get(sig.get("hiring_scope") or "unknown", 3)
    pay_sig = sig.get("pay_signal") or "unknown"
    if pay_sig == "near" and sig.get("pay_basis") == "hourly":
        pay_sig = "below"
    funded = sig.get("client_tier") == "funded_product" or bool(sig.get("funding"))
    pay = {"at_or_above": 15, "near": 8, "below": 0}.get(pay_sig, 8 if funded else 3)
    role = 10 if int(sig.get("role_posts_90d") or 0) >= 1 else 4 if int(sig.get("adjacent_posts_90d") or 0) else 0
    owner = OWNER.get(sig.get("role_owner") or infer_owner(lead), 10)
    days = sig.get("last_activity_days")
    active = 2 if days is None else 15 if days <= 7 else 10 if days <= 30 else 5 if days <= 90 else 0
    voice_sig = sig.get("hiring_voice") or ("general" if _HIRING_TALK.search(" ".join(lead.get("evidence") or []))
                                             and days is not None and days <= 90 else "unknown")
    voice = VOICE.get(voice_sig, 4)
    penalty = (-20 if sig.get("volume_vendor") else 0) + (-20 if sig.get("task_platform") else 0)
    return {"region": region, "pay": pay, "role": role, "owner": owner, "active": active, "voice": voice, "penalty": penalty}


def rubric(lead: dict) -> str:
    return "lane" if in_lane(lead) else "recruiter"


def min_for(lead: dict, st: dict) -> int:
    """The contact line for this lead's rubric."""
    return int(st.get("company_lane_min_score") or st["min_score"]) if in_lane(lead) else int(st["min_score"])


def qualifies(lead: dict, st: dict) -> bool:
    return (lead.get("score") or 0) >= min_for(lead, st)


def ceilings(home: str) -> dict:
    """pay_ceilings.json: the highest monthly USD pay each company or agency has ever quoted the candidate."""
    return load(home, "pay_ceilings.json", {})


def score_ctx(home: str, st: dict | None = None) -> dict:
    st = st or settings(home)
    return {"ceilings": ceilings(home), "min_score": st["min_score"], "floor": st["pay_floor_usd_month"],
            "target": st["pay_target_usd_month"], "st": st}


def rescore(lead: dict, ctx: dict | None = None) -> dict:
    """Score a lead on its rubric (recruiters: score_parts; company-lane leads: lane_parts). With `ctx` (score_ctx),
    a company whose known pay ceiling is under the pay floor caps the lead just below its contact line, and one whose
    ceiling reaches the target counts as posting target pay."""
    ctx = ctx or {}
    sig = dict(lead.get("signals") or {})
    ceil = (ctx.get("ceilings") or {}).get(company_key(lead.get("company", "")))
    best = (lead.get("outcome") or {}).get("quoted_usd_month") or 0
    known = max(best, (ceil or {}).get("max_usd_month") or 0) if (ceil or best) else None
    lead.pop("gate", None)
    if known is not None and ctx.get("target") and known >= ctx["target"]:
        sig["pay_signal"] = "at_or_above"
    elif known is not None and ctx.get("floor") and known < ctx["floor"]:
        sig["pay_signal"] = "below"
    lane = in_lane(lead)
    parts = lane_parts(sig, lead) if lane else score_parts(sig, lead)
    lead["parts"] = parts
    lead["rubric"] = "lane" if lane else "recruiter"
    score = 0 if lead.get("irrelevant") else total(parts)
    line = min_for(lead, ctx["st"]) if ctx.get("st") else ctx.get("min_score", 0)
    if known is not None and ctx.get("floor") and known < ctx["floor"] and score >= line:
        score = max(0, line - 1)
        lead["gate"] = f"pay ceiling ${known:,}/month < floor ${ctx['floor']:,}"
    elif lane and not parts["role"] and score >= line:
        # the lane is defined by an open role at the company; a well-placed person at a company with none is not a lead
        score = max(0, line - 1)
        lead["gate"] = "no open role like the target at this company"
    lead["score"] = score
    return lead


# --------------------------------------------------------------- discovery

MAX_PAGES = 6  # how deep one search phrasing is worth reading before it is spent


def need(home: str, st: dict) -> tuple[int, dict]:
    """How many more leads today's remaining quotas can take: connection requests left plus InMails
    left (bounded by the credits LinkedIn last showed, when known)."""
    p = pacing(home)
    q = quotas(p, st)
    inmail_left = q["inmail"]["left"]
    if p.get("inmail_credits") is not None:
        inmail_left = min(inmail_left, int(p["inmail_credits"]))
    return q["connect"]["left"] + inmail_left, q


def contactable(leads: dict, st: dict, home: str | None = None) -> list:
    """Uncontacted leads at or above their rubric's contact line whose company still has room under max_per_company."""
    cos = companies(home) if home else {}
    return sorted((v for v in leads.values() if v["status"] == "new" and qualifies(v, st)
                   and (not home or company_room(home, st, v.get("company", ""), cos)[0] > 0)),
                  key=lambda v: -v["score"])


def first_degree(v: dict) -> bool:
    """Already a connection: gets a plain message, which spends no connection request."""
    return str(v.get("degree") or "").strip().lower().startswith("1")


# ----------------------------------------------------------- company lane
# Recruiters were a proxy: every $10k+ quote traced back to a funded product company. The company lane goes to
# those companies directly. Targets come from job-source-sweep's roles (VC/YC portfolio boards, startup boards,
# posted pay at the target) and from the funded-company registry (funding news, portfolio pages, batches; see
# below); discovery finds each target's in-house recruiter and the hiring manager (founder, CTO, head of AI). They
# share the pile and the caps with recruiters but are scored on their own rubric (lane_parts), and a fixed share of
# the daily budget is reserved for them so the two lanes can be compared on `quoted_target`.

LEAD_TYPES = ("recruiter", "in_house", "hiring_manager")


def title_re(st: dict) -> re.Pattern:
    """Posting titles that count as the target role: company_lane_title_terms from config.yaml, or else the words
    of role.title."""
    terms = [t for t in (st.get("company_lane_title_terms") or []) if t] or \
        [w for w in re.split(r"\W+", st["target_role"]) if len(w) > 1 and w.lower() not in ("engineer", "senior", "staff")]
    return re.compile(r"\b(" + "|".join(re.escape(t) for t in terms) + r")\b", re.I) if terms else re.compile(".")
# a raw ATS board lists every job at the company, so a board title needs an engineering word as well as a target
# term (ENG_TITLE / NOT_ENG in companies.py)


def board_match(title: str, st: dict) -> bool:
    return bool(title_re(st).search(title or "") and _ENG_TITLE.search(title or "") and not _NOT_ENG.search(title or ""))


FUNDED_SRC = re.compile(r"portfolio|work at a startup|y ?combinator", re.I)
STARTUP_SRC = re.compile(r"wellfound|who is hiring", re.I)
# talent marketplaces and staffing names that show up as the "company" of a posting (NOT_EMPLOYER, companies.py)


def lead_type(v: dict) -> str:
    t = (v or {}).get("lead_type") or "recruiter"
    return t if t in LEAD_TYPES else "recruiter"


def in_lane(v: dict) -> bool:
    return lead_type(v) != "recruiter"


def lane_quota(needed: int, st: dict) -> int:
    """Connection requests + InMails reserved for the company lane today (backfilled by recruiters if unused)."""
    share = float(st.get("company_lane_share") or 0)
    return min(needed, math.ceil(needed * share)) if share > 0 else 0


def applied_companies(home: str) -> dict:
    """company key -> what job-autopilot did there: an application (applied) or a filled form waiting (filled)."""
    out: dict = {}
    for e in enrich.read_json(js.fix_path(home) / "autopilot" / "ledger.json", {}).values():
        if e.get("status") not in ("applied", "filled"):
            continue
        k = company_key(e.get("company", ""))
        if k and (k not in out or e.get("status") == "applied"):
            out[k] = {"status": e["status"], "title": e.get("title", ""), "at": (e.get("applied_at") or e.get("updated") or "")[:10]}
    return out


def company_targets(home: str, st: dict, runs: dict | None = None, for_plan: bool = False) -> list:
    """Companies worth reaching directly, best first. Two sources: job-source-sweep's roles (posted the target role
    recently, open to the candidate's region or silent on it, and either posted pay at the target or a VC/YC
    portfolio or startup board with no pay under the floor), and the funded-company registry (a recent round or a
    portfolio listing, plus a matching role whose ATS posting is remote and open to the region or silent on it).
    Staffing names, marketplaces, banned companies, known low ceilings and companies searched within
    company_lane_cooldown_days are left out. `for_plan` keeps companies already searched or at the company cap, for
    looking up a planned lead's roles."""
    hp = js.fix_path(home)
    try:
        roles = enrich.collect_roles(home, None, enrich.run_dates(home, int(st["company_lane_batches"])))
    except SystemExit:
        roles = []
    cls = enrich.read_json(hp / "classifications.json", {})
    scores = enrich.read_json(hp / "autopilot" / "scores.json", {})
    ceil, applied = ceilings(home), applied_companies(home)
    floor, target = st["pay_floor_usd_month"], st["pay_target_usd_month"]
    runs = runs if runs is not None else load(home, "discovery-runs.json", {})
    cool = (NOW - timedelta(days=int(st["company_lane_cooldown_days"]))).isoformat()
    worked = set() if for_plan else \
        {company_key(k.split(":", 1)[1]) for k, at in runs.items() if k.startswith("company:") and at >= cool}
    cos = companies(home)
    role_title = title_re(st)
    recent_since = (NOW - timedelta(days=int(st["company_lane_funding_days"]))).strftime("%Y-%m-%d")

    def excluded(name: str, k: str) -> bool:
        return not k or k in worked or is_banned(name, st) or bool(NOT_EMPLOYER.search(name) or _STAFF_AUG.search(name)
                                                                   or _VENDOR.search(name)) \
            or (ceil.get(k) or {}).get("max_usd_month", floor) < floor

    out: dict = {}

    def entry_for(k: str, name: str) -> dict:
        return out.setdefault(k, {"company": name, "roles": [], "pay_max_usd_month": 0, "funded_source": False,
                                  "startup_source": False, "open_to_region": False, "nearby_region": False, "best_fit": 0, "latest": "",
                                  "sources": [], "origin": [], "funding": None, "recent_round": False,
                                  "applied": applied.get(k), "room": company_room(home, st, name, cos)[0]})

    for r in roles:
        name = (r.get("company") or "").strip()
        k = company_key(name)
        if excluded(name, k):
            continue
        sc = scores.get(r["key"]) or {}
        elig = ((cls.get(r["key"]) or {}).get("elig") or {}).get("category")
        hi = sc.get("pay_max") or sc.get("pay_min")
        if sc.get("apply") is False or elig == 3 or (hi and hi < floor):
            continue
        if not role_title.search(r.get("title") or "") and int(sc.get("core") or 0) < 20:
            continue  # the sweep's profile also catches general engineering roles; the lane is for the target role
        funded = any(FUNDED_SRC.search(s) for s in r.get("sources") or [])
        startup = any(STARTUP_SRC.search(s) for s in r.get("sources") or [])
        if not (hi and hi >= target) and not funded and not startup:
            continue
        t = entry_for(k, name)
        label = enrich.ELIG_LABEL.get(elig, "unclassified")
        dup = next((x for x in t["roles"] if x["title"].strip().lower() == (r["title"] or "").strip().lower()), None)
        if dup:  # one role posted on several boards: keep one entry, with the most informative eligibility and pay
            if dup["eligibility"] in ("unclassified", "none") and label == "open":
                dup.update(eligibility=label, url=r["url"])
            dup["pay_usd_month"] = dup["pay_usd_month"] or (round(hi) if hi else None)
        else:
            t["roles"].append({"title": r["title"], "url": r["url"], "posted": (r.get("posted_iso") or "")[:10],
                               "pay_usd_month": round(hi) if hi else None, "eligibility": label})
        t["pay_max_usd_month"] = max(t["pay_max_usd_month"], round(hi or 0))
        t["funded_source"] |= funded
        t["startup_source"] |= startup
        t["open_to_region"] |= elig == 1
        t["best_fit"] = max(t["best_fit"], int(sc.get("core") or 0))
        t["latest"] = max(t["latest"], r.get("posted_iso") or "")
        t["sources"] = sorted(set(t["sources"]) | set(r.get("sources") or []))
        if "sweep" not in t["origin"]:
            t["origin"].append("sweep")

    # the funded-company registry: companies found from the money side, whatever the sweep saw
    for k, e in registry(home).items():
        name = e.get("name") or k
        if excluded(name, k):
            continue
        ok = [x for x in e.get("roles") or [] if board_match(x.get("title"), st)
              and x.get("region", "unclear") in TARGET_REGIONS
              and not (x.get("pay_usd_month") and x["pay_usd_month"] < floor)]
        if not ok and k not in out:
            continue  # funded, but no matching role open to the region yet: refresh-boards keeps watching
        t = entry_for(k, name)
        # one title posted per country: keep the copy closest to the candidate (open, then nearby, then unclear)
        ok.sort(key=lambda x: TARGET_REGIONS.index(x.get("region", "unclear")))
        for x in ok:
            if not any(y["title"].strip().lower() == (x.get("title") or "").strip().lower() for y in t["roles"]):
                t["roles"].append({"title": x.get("title", ""), "url": x.get("url", ""), "posted": (x.get("posted") or "")[:10],
                                   "pay_usd_month": x.get("pay_usd_month"),
                                   "eligibility": x.get("region") or "unclassified",
                                   "location": x.get("location", "")})
            t["pay_max_usd_month"] = max(t["pay_max_usd_month"], int(x.get("pay_usd_month") or 0))
            t["open_to_region"] |= x.get("region") == "open"
            t["nearby_region"] |= x.get("region") == "nearby"
            t["latest"] = max(t["latest"], x.get("posted") or "")
            t["best_fit"] = max(t["best_fit"], 20)  # the title matched the target role; there is no autopilot score
        r = latest_round(e)
        t["funding"] = funding_label(e)
        t["funded_source"] |= bool(e.get("funding"))
        t["recent_round"] |= (r.get("date") or "") >= recent_since
        t["sources"] = sorted(set(t["sources"]) | {"funding:" + s for s in e.get("sources") or []})
        if ok and "registry" not in t["origin"]:
            t["origin"].append("registry")
    # job-autopilot's well-scored roles (75+) whose form couldn't be filled (account walls, load failures;
    # autopilot/target_roles.json): reaching the company directly is the next best thing to the application
    for x in enrich.read_json(hp / "autopilot" / "target_roles.json", {}).values():
        name = (x.get("company") or "").strip()
        k = company_key(name)
        if excluded(name, k):
            continue
        t = entry_for(k, name)
        if not any(y["title"].strip().lower() == (x.get("title") or "").strip().lower() for y in t["roles"]):
            t["roles"].append({"title": x.get("title", ""), "url": x.get("url", ""), "posted": (x.get("at") or "")[:10],
                               "pay_usd_month": None, "eligibility": "open"})
        t["open_to_region"] = True
        t["autopilot_blocked"] = x.get("outcome", "")
        t["latest"] = max(t["latest"], x.get("at") or "")
        if "autopilot" not in t["origin"]:
            t["origin"].append("autopilot")
    # people checks (companies.py): engineers in the candidate's country or region are evidence the company hires
    # there, whatever its postings say
    people = shared_companies.people_checks(home)
    for t in out.values():
        lvl = shared_companies.people_level(people, t["company"])
        t["people"] = lvl
        if lvl in ("brazil", "latam"):
            t["open_to_region"] = True
    ranked = sorted((t for t in out.values() if for_plan or t["room"] > 0), key=lambda t: t["latest"], reverse=True)  # newest first...
    # ...within each band (sort is stable). A role that says it is open to the candidate's region comes first: a US
    # salary on a posting that states no region is often US-only in practice, so posted pay ranks after region (a role
    # posted in another country of the region comes right after one open to the candidate's own). A round in
    # the last company_lane_funding_days ranks above an older or undated portfolio listing.
    ranked.sort(key=lambda t: (not t["open_to_region"], not t["nearby_region"], t["pay_max_usd_month"] < target, not t["funded_source"],
                               not t["recent_round"], -min(t["best_fit"], 20)))
    region = userconfig.snapshot().get("region") or "your region"
    for t in ranked:
        t["roles"] = sorted(sorted(t["roles"], key=lambda x: x["posted"], reverse=True),  # best region, then newest
                            key=lambda x: {"open": 0, "nearby": 1}.get(x["eligibility"], 2))[:3]
        t["why"] = ", ".join(x for x in [f"posted pay ${t['pay_max_usd_month']:,}/mo" if t["pay_max_usd_month"] >= target else "",
                                         t["funding"] or "",
                                         "VC/YC portfolio board" if t["funded_source"] and "sweep" in t["origin"]
                                         and any(FUNDED_SRC.search(s) for s in t["sources"]) else "",
                                         "startup board" if t["startup_source"] and not t["funded_source"] else "",
                                         "autopilot couldn't fill its form" if t.get("autopilot_blocked") else "",
                                         f"engineers in {region}" if t.get("people") in ("brazil", "latam") else "",
                                         f"open to {region}" if t["open_to_region"] and t.get("people") not in ("brazil", "latam") else
                                         f"posts roles elsewhere in {region}" if t["nearby_region"] else ""] if x)
        t.pop("room", None)
    return ranked


def vc_funds() -> list:
    """The VC and accelerator portfolios job-source-sweep already reads (Getro, Consider, YC). Their talent partners
    recruit only for funded portfolio companies."""
    cat = enrich.read_json(SWEEP.parent / "sources.json", {})
    out = []
    for s in cat.get("http") or []:
        if str(s.get("id", "")).startswith(("getro_", "consider_", "yc")):
            name = re.sub(r"\s+portfolio$", "", s.get("label", ""), flags=re.I).replace("YC Work at a Startup", "Y Combinator")
            if name and name not in out:
                out.append(name)
    return out


# ------------------------------------------------------- funded companies
# The lane used to see only companies the job sweep had already found posting the role, so it ran dry when the
# sweep's LATAM-open startups were used up. funded_companies.json adds companies found from the money side: funding
# news, VC portfolio pages, accelerator batches and AI company lists (references/funding.md), researched on the web
# by a subagent that never touches LinkedIn. Each entry keeps its rounds and, when the careers page runs on Ashby,
# Greenhouse, Lever or Workable, the board. refresh-boards reads those boards through the ATS's public API, so the
# posting's own location and workplace fields decide whether a role is open to the candidate's region: the check
# the 2026-10-13 run lacked when three US-pay targets turned out to be onsite.

# REGIONS, TARGET_REGIONS, NEIGHBOURS, BOARD_KINDS, region_of, usd_month and board_roles live in
# job-source-sweep/scripts/companies.py, shared with the sweep's company_boards source and job-autopilot.


def registry(home: str) -> dict:
    """funded_companies.json: company key -> name, rounds, website, careers/ATS board, matching roles."""
    return load(home, "funded_companies.json", {})


def latest_round(e: dict) -> dict:
    rounds = [r for r in e.get("funding") or [] if r.get("date")]
    return max(rounds, key=lambda r: r["date"]) if rounds else ((e.get("funding") or [{}])[-1] or {})


def funding_label(e: dict) -> str:
    """'Series A $30M 2026-09-12' / 'portfolio: Sequoia' - the latest round in a few words."""
    r = latest_round(e)
    if not r:
        return ""
    amt = r.get("amount_usd") or 0
    money = f"${amt / 1e9:.1f}B" if amt >= 1e9 else f"${round(amt / 1e6)}M" if amt >= 1e6 else ""
    if not r.get("date") and not money:
        inv = ", ".join((r.get("investors") or [])[:2])
        return f"{r.get('round') or 'funded'}" + (f": {inv}" if inv else "")
    return " ".join(x for x in [r.get("round") or "round", money, (r.get("date") or "")[:10]] if x)


def cmd_refresh_boards(args) -> int:
    """Read every registry company's ATS board; keep the postings matching the target role, each with its region."""
    st = settings(args.home)
    reg = registry(args.home)
    cand = userconfig.snapshot()
    hours = args.max_age_hours if args.max_age_hours is not None else int(st["company_lane_board_refresh_hours"])
    cutoff = (NOW - timedelta(hours=hours)).isoformat()
    due = [k for k, e in reg.items() if ((e.get("ats") or {}).get("kind") or "").lower() in BOARD_KINDS
           and (e.get("board_checked_at") or "") < cutoff]
    f = js.Fetcher(enrich.UA)

    def one(k: str):
        try:
            return k, board_roles(f, reg[k]["ats"]), ""
        except Exception as err:  # one bad board (e.g. a malformed slug) must not stop the refresh
            return k, None, str(err)[:160]

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(6) as ex:
        results = list(ex.map(one, due))
    errors, new_roles = [], []
    for k, roles, err in results:
        e = reg[k]
        e["board_checked_at"] = NOW.isoformat()
        if roles is None:
            e["board_error"] = err
            errors.append(f'{e.get("name", k)}: {err}')
            continue
        e.pop("board_error", None)
        before = {x.get("url") for x in e.get("roles") or []}
        e["open_roles_total"] = len(roles)
        e["roles"] = [{**x, "region": region_of(x["location"], x["remote"], cand)} for x in roles if board_match(x["title"], st)]
        new_roles += [f'{e.get("name", k)}: {x["title"]} ({x["region"]}, {x["location"][:40]})'
                      for x in e["roles"] if x["url"] not in before]
    save(args.home, "funded_companies.json", reg)
    watching = [e for e in reg.values() if e.get("ats")]
    usable = [e for e in reg.values() if any(x.get("region") in TARGET_REGIONS for x in e.get("roles") or [])]
    return emit({"boards_read": len(due) - len(errors), "errors": errors[:20], "skipped_fresh": len(watching) - len(due),
                 "registry": len(reg), "with_board": len(watching), "with_matching_role_open_or_unclear": len(usable),
                 "new_matching_roles": new_roles[:40]})


def research_state(home: str, st: dict, runs: dict | None = None) -> dict:
    runs = runs if runs is not None else load(home, "discovery-runs.json", {})
    last = max((at for k, at in runs.items() if k.startswith("funding:")), default="")
    return {"last": last[:10], "due_by_age": last < (NOW - timedelta(days=int(st["company_lane_research_days"]))).isoformat()}


def cmd_funding_plan(args) -> int:
    """Context for the funding-research subagent (references/funding.md)."""
    st = settings(args.home)
    reg = registry(args.home)
    runs = load(args.home, "discovery-runs.json", {})
    tried_since = (NOW - timedelta(days=21)).isoformat()
    stale = (NOW - timedelta(days=14)).isoformat()
    since = NOW - timedelta(days=int(st["company_lane_funding_days"]))
    recent = since.strftime("%Y-%m-%d")
    # registry companies whose careers page has no public board: only a fresh look can tell whether the role opened
    recheck = [{"name": e["name"], "website": e.get("website", ""), "careers_url": e.get("careers_url", "")}
               for e in reg.values() if not e.get("ats") and (e.get("checked_at") or "") < stale
               and (latest_round(e).get("date") or "9999") >= recent][:10]
    months, d = [], NOW.replace(day=1)
    while d >= since.replace(day=1) and len(months) < 4:
        months.append(d.strftime("%B %Y"))
        d = (d - timedelta(days=1)).replace(day=1)
    cand = userconfig.snapshot()
    ceil = ceilings(args.home)
    out = work_dir(args.home)
    return emit({"per_round": int(st["company_lane_research_per_round"]), "funded_since": recent,
                 "min_round_usd": int(st["company_lane_min_round_usd"]),
                 "known_companies": sorted(e.get("name", k) for k, e in reg.items()),
                 "already_targets": sorted({t["company"] for t in company_targets(args.home, st, runs, for_plan=True)}),
                 "queries_tried": sorted(k.split(":", 1)[1] for k, at in runs.items() if k.startswith("funding:") and at >= tried_since),
                 "recheck": recheck, "vc_funds": vc_funds(),
                 # employers of engineers in the candidate's country, from job-source-sweep's weekly harvest
                 # (people-harvest): candidates for avenue 6 of references/funding.md, not yet in the registry
                 "region_employers": sorted((e.get("name") for k, e in enrich.read_json(js.fix_path(args.home) / "region_employers.json", {}).items()
                                             if k not in reg and not NOT_EMPLOYER.search(e.get("name") or "")), key=str.lower)[:60],
                 "seeds": {"months": months, "role_terms": [st["target_role"]] + list(cand.get("core_areas") or []),
                           "title_terms": st["company_lane_title_terms"] or [st["target_role"]],
                           "region_terms": list(dict.fromkeys(x for x in [cand.get("country"), cand.get("region")] + list(cand.get("region_aliases") or []) if x))},
                 "target_role": st["target_role"], "pay_target_usd_month": st["pay_target_usd_month"],
                 "pay_floor_usd_month": st["pay_floor_usd_month"], "candidate": cand,
                 "banned_companies": st["banned_companies"],
                 "known_low_ceiling": sorted(e["name"] for e in ceil.values() if e["max_usd_month"] < st["pay_floor_usd_month"]),
                 "result": enrich.host_path(out / f"funding_result_{NOW.strftime('%H%M')}.json")})


def cmd_ingest_funded(args) -> int:
    """Merge a funding-research result into funded_companies.json."""
    res = load_result(args.result)
    st = settings(args.home)
    reg = registry(args.home)
    runs = load(args.home, "discovery-runs.json", {})
    cand = userconfig.snapshot()
    for q in res.get("queries_run") or []:
        runs["funding:" + q] = NOW.isoformat()
    added = updated = 0
    skipped = []
    for c in res.get("companies") or []:
        name = (c.get("name") or "").strip()
        k = company_key(name)
        if not k:
            continue
        if is_banned(name, st) or NOT_EMPLOYER.search(name) or _STAFF_AUG.search(name):
            skipped.append(name)
            continue
        cur = reg.get(k)
        e = cur or {"name": name, "found_at": NOW.isoformat(), "found_via": c.get("found_via", "")}
        for fld in ("website", "linkedin_url", "headcount", "hq", "remote_policy", "careers_url", "ai_native",
                    "description", "notes"):
            if c.get(fld) not in (None, ""):
                e[fld] = c[fld]
        ats = c.get("ats") or {}
        if (ats.get("kind") or "").lower() in BOARD_KINDS and ats.get("slug"):
            if (e.get("ats") or {}) != {"kind": ats["kind"].lower(), "slug": ats["slug"]}:
                e.pop("board_checked_at", None)  # a new board: read it on the next refresh
            e["ats"] = {"kind": ats["kind"].lower(), "slug": ats["slug"]}
        rounds = e.setdefault("funding", [])
        for r in c.get("funding") or []:
            if not any((x.get("round"), x.get("date")) == (r.get("round"), r.get("date")) for x in rounds):
                rounds.append({kk: r.get(kk) for kk in ("round", "amount_usd", "date", "investors", "source_url") if r.get(kk)})
        e["sources"] = sorted(set(e.get("sources") or []) | ({c["found_via"]} if c.get("found_via") else set()))
        if c.get("roles") is not None:  # the subagent's reading; refresh-boards replaces it for companies with a board
            e["roles"] = [{"title": x.get("title", ""), "url": x.get("url", ""), "location": x.get("location", ""),
                           "remote": x.get("remote"), "posted": (x.get("posted") or "")[:10],
                           "pay_usd_month": x.get("pay_usd_month"),
                           "region": x["region"] if x.get("region") in REGIONS
                           else region_of(x.get("location", ""), x.get("remote"), cand)} for x in c["roles"]]
        e["checked_at"] = NOW.isoformat()
        reg[k] = e
        added += cur is None
        updated += cur is not None
    save(args.home, "funded_companies.json", reg)
    save(args.home, "discovery-runs.json", runs)
    return emit({"added": added, "updated": updated, "skipped": skipped, "registry": len(reg),
                 "with_board": sum(bool(e.get("ats")) for e in reg.values()),
                 "next": "run `recruiters refresh-boards`, then `recruiters targets`"})


def cmd_targets(args) -> int:
    """The company lane's target list, for a look before a run."""
    st = settings(args.home)
    t = company_targets(args.home, st)
    return emit({"targets": len(t), "with_target_pay": sum(x["pay_max_usd_month"] >= st["pay_target_usd_month"] for x in t),
                 "funded": sum(x["funded_source"] for x in t), "recent_round": sum(x["recent_round"] for x in t),
                 "from_registry": sum("registry" in x["origin"] for x in t), "from_sweep": sum("sweep" in x["origin"] for x in t),
                 "open_to_region": sum(x["open_to_region"] for x in t),
                 "applied_by_autopilot": sum(bool(x["applied"]) for x in t), "top": t[:args.top]})


def cmd_discover_plan(args) -> int:
    st = settings(args.home)
    leads = load(args.home, "leads.json", {})
    runs = load(args.home, "discovery-runs.json", {})
    depth = load(args.home, "discovery-depth.json", {})
    cutoff = (NOW - timedelta(days=7)).isoformat()
    people = [q for q in st["people_queries"] if (runs.get("people:" + q) or "") < cutoff]
    posts = [q for q in st["post_queries"] if (runs.get("posts:" + q) or "") < cutoff]
    stale = (NOW - timedelta(days=st["stale_days"])).isoformat()
    reprobe = [{"profile_url": v["profile_url"], "name": v.get("name"), "probed_at": v.get("probed_at")}
               for v in leads.values() if (v.get("probed_at") or "") < stale and v.get("status") == "new"][:10]
    needed, q = need(args.home, st)
    pile = contactable(leads, st, args.home)
    have = sum(not first_degree(v) for v in pile)
    lane_q = lane_quota(needed, st)
    lane_have = sum(in_lane(v) and not first_degree(v) for v in pile)
    targets = company_targets(args.home, st, runs) if lane_q > lane_have else []
    research = research_state(args.home, st, runs)
    reg = registry(args.home)
    # phrasings already run this week whose deeper result pages haven't been read yet
    deeper = [{"kind": k.split(":", 1)[0], "query": k.split(":", 1)[1], "start_page": depth.get(k, 2) + 1}
              for k, at in runs.items() if at >= cutoff and depth.get(k, 2) < MAX_PAGES]
    # employers and agencies of leads proven to place at the target pay (a quote, a ceiling, or posted pay at or above
    # it): their colleagues recruit for the same clients. Snowballing from any high scorer used to pull in whole
    # staff-augmentation agencies whose ceiling sits under the floor.
    ceil = ceilings(args.home)
    target = st["pay_target_usd_month"]

    def proven(v: dict) -> bool:
        c = ceil.get(company_key(v.get("company", ""))) or {}
        return ((v.get("outcome") or {}).get("quoted_usd_month") or 0) >= target or c.get("max_usd_month", 0) >= target \
            or (v.get("signals") or {}).get("pay_signal") == "at_or_above"

    snowball = []
    for v in sorted(leads.values(), key=lambda v: -v["score"]):
        c = (v.get("company") or "").strip()
        if c and company_key(c) and not is_banned(c, st) and company_key(c) not in {company_key(x) for x in snowball} \
                and not v.get("gate") \
                and not v.get("irrelevant") and (v.get("parts") or {}).get("tier", 8) > 0 and proven(v):
            snowball.append(c)
    # agencies that quoted the target pay to the candidate directly (inbound recruiters are often not leads at all)
    for e in sorted(ceil.values(), key=lambda e: -e["max_usd_month"]):
        if e["max_usd_month"] >= target and not is_banned(e["name"], st) \
                and company_key(e["name"]) not in {company_key(c) for c in snowball}:
            snowball.insert(0, e["name"])
    cand = userconfig.snapshot()
    year = target * 12
    seeds = {"role_terms": [st["target_role"]] + list(cand.get("core_areas") or []),
             "region_terms": [cand.get("country"), cand.get("region")] + list(cand.get("region_aliases") or [])
                             + [t for t in st["required_terms"] if t not in (cand.get("region_aliases") or [])],
             "recruiter_nouns": ["recruiter", "technical recruiter", "talent partner", "talent acquisition",
                                 "sourcer", "headhunter", "hiring", "we're hiring", "executive search", "AI talent search",
                                 "talent partner (VC fund)", "founding engineer recruiter"],
             # who the role is for: recruiters naming funded product companies quoted the target pay; the ones naming
             # "a US client" or a consultancy did not (references/pay-tier-research.md)
             "client_terms": ["YC", "Y Combinator", "Series A", "Series B", "seed", "VC-backed", "founding", "startup",
                              "AI-native", "applied research"],
             "languages": [x for x in ["English", cand.get("native_language")] if x],
             "language_rule": "English first; at most one native-language phrasing per pass (native-language recruiters "
                              "quoted local-currency pay well under the floor)",
             "pay_terms": ["USD", f"${year // 1000}k", f"${round(year * 1.25 / 1000)}k", f"${year // 1000}k-${round(year * 1.5 / 1000)}k",
                           f"${target // 1000}k/month", "equity", "full-time"],
             "hashtags": ["#hiring", f"#{(cand.get('region') or '').lower()}", "#remotework", "#" + squash(st["target_role"]),
                          "#foundingengineer", "#startupjobs"]}
    max_leads = args.max_leads or max(30, needed * 2)
    week = [k for k, at in runs.items() if at >= cutoff]
    required = [{"term": t, "run_this_week": sum(t.lower() in k.lower() for k in week),
                 "rule": "every discovery subagent runs at least one people and one post phrasing containing it"}
                for t in st["required_terms"]]
    out = work_dir(args.home)
    return emit({"people_queries": people, "post_queries": posts, "exhausted": not people and not posts,
                 "required_terms": required,
                 "needed": needed, "contactable_now": have, "quotas": q, "max_leads": max_leads,
                 "queries_tried": sorted(runs), "deeper": deeper[:12], "snowball_companies": snowball[:15],
                 "vc_funds": vc_funds(), "pay_floor_usd_month": st["pay_floor_usd_month"],
                 "company_lane": {"quota": lane_q, "contactable_now": lane_have, "short_by": max(0, lane_q - lane_have),
                                  "targets_available": len(targets),
                                  "rule": "when short_by > 0, one discovery subagent works company_targets "
                                          "(references/discover.md, Company lane) alongside the recruiter avenues"},
                 "company_targets": targets[:int(st["company_lane_per_round"])],
                 # the registry's own source of targets: funding news, portfolios, batches (references/funding.md)
                 "funding_research": {"due": float(st["company_lane_share"] or 0) > 0 and (research["due_by_age"] or (
                                          lane_q > lane_have and len(targets) < 2 * int(st["company_lane_per_round"]))),
                                      "last": research["last"], "registry": len(reg),
                                      "watching_without_role": sum(1 for e in reg.values() if not e.get("roles")),
                                      "rule": "when due, launch the funding-research subagent (recruiters funding-plan, "
                                              "references/funding.md) alongside the Chrome subagents; then ingest-funded, "
                                              "refresh-boards, and give the new targets to a lane subagent"},
                 "known_low_ceiling": sorted(e["name"] for e in ceil.values() if e["max_usd_month"] < st["pay_floor_usd_month"]),
                 "seeds": seeds, "known_profiles": sorted(leads), "reprobe": reprobe,
                 "target_role": st["target_role"], "pay_target_usd_month": st["pay_target_usd_month"],
                 "banned_companies": st["banned_companies"], "candidate": cand,
                 "result": enrich.host_path(out / f"discover_result_{NOW.strftime('%H%M')}.json")})


def cmd_ingest(args) -> int:
    res = load_result(args.result)
    st = settings(args.home)
    leads = load(args.home, "leads.json", {})
    runs = load(args.home, "discovery-runs.json", {})
    depth = load(args.home, "discovery-depth.json", {})
    pages = res.get("pages_read") or {}
    for q in res.get("people_queries_run") or []:
        runs["people:" + q] = NOW.isoformat()
    for q in res.get("post_queries_run") or []:
        runs["posts:" + q] = NOW.isoformat()
    for c in res.get("companies_run") or []:  # the company lane: a target is searched once per cooldown
        runs["company:" + c] = NOW.isoformat()
    for k in list(runs):
        if (pages.get(k) or 0) > depth.get(k, 0):
            depth[k] = int(pages[k])
    added = updated = banned = 0
    ctx = score_ctx(args.home, st)
    origins = None
    for L in res.get("leads") or []:
        key = norm_profile(L.get("profile_url", ""))
        if not key.startswith("linkedin.com/in/"):
            continue
        if is_banned(L.get("company", ""), st):
            banned += 1
            continue
        cur = leads.get(key)
        src = source_key(L.get("found_via", ""))
        first = lead_source(cur) if cur else ""
        seen = list((cur or {}).get("sources") or ([first] if first else []))
        for s in [src] + [source_key(x) for x in (L.get("also_found_via") or [])]:
            if s and s not in seen:
                seen.append(s)
        entry = {**(cur or {"found_at": NOW.isoformat(), "status": "new", "contact": None}),
                 "profile_url": L.get("profile_url"), "name": L.get("name", ""), "headline": L.get("headline", ""),
                 "company": L.get("company", ""), "location": L.get("location", ""), "degree": L.get("degree", ""),
                 "language": L.get("language", "en"), "signals": L.get("signals") or {},
                 "evidence": L.get("evidence") or [], "recent_roles": L.get("recent_roles") or [],
                 "vibe": L.get("vibe", ""), "irrelevant": bool(L.get("irrelevant")),
                 "lead_type": L.get("lead_type") if L.get("lead_type") in LEAD_TYPES else lead_type(cur or {}),
                 "found_via": (cur or {}).get("found_via") or L.get("found_via", ""),
                 "source": first or src, "sources": seen,
                 "probed_at": NOW.isoformat()}
        if entry["irrelevant"] and entry["status"] == "new":
            entry["status"] = "skipped"
            entry["skip_reason"] = L.get("irrelevant_reason", "not a relevant recruiter")
        if in_lane(entry) and not entry.get("lane_origin"):
            # where the lead's company came from (the job sweep, the funded-company registry, or both), so `queries`
            # can compare the two sources of company targets
            if origins is None:
                origins = {company_key(t["company"]): t["origin"] for t in company_targets(args.home, st, for_plan=True)}
            entry["lane_origin"] = "+".join(origins.get(company_key(entry["company"])) or ["sweep"])
        rescore(entry, ctx)
        leads[key] = entry
        added += cur is None
        updated += cur is not None
    save(args.home, "leads.json", leads)
    save(args.home, "discovery-runs.json", runs)
    save(args.home, "discovery-depth.json", depth)
    ranked = contactable(leads, st, args.home)
    needed, _ = need(args.home, st)
    ran = {"people": res.get("people_queries_run") or [], "posts": res.get("post_queries_run") or []}
    used = {t: {k: sum(t.lower() in q.lower() for q in qs) for k, qs in ran.items()} for t in st["required_terms"]}
    missing = [f"{t}: no {k} phrasing used it" for t, u in used.items() for k, n in u.items() if not n]
    unattributed = sum(1 for L in res.get("leads") or [] if not source_key(L.get("found_via", "")))
    return emit({"added": added, "updated": updated, "skipped_banned": banned, "leads_total": len(leads),
                 "contactable_now": sum(not first_degree(v) for v in ranked),
                 "company_lane_contactable": sum(in_lane(v) and not first_degree(v) for v in ranked),
                 "company_lane_quota": lane_quota(needed, st),
                 "first_degree_pending": sum(first_degree(v) for v in ranked) if st["message_per_day"] != 0 else 0,
                 "needed": needed, "required_terms_used": used,
                 **({"required_terms_missing": missing} if missing else {}),
                 **({"leads_without_found_via": unattributed} if unattributed else {}),
                 # two rubrics, two lists: a lane score and a recruiter score are not on the same scale
                 "top": [f'{v["score"]} {v["name"]} - {v["headline"][:60]}' for v in ranked if not in_lane(v)][:8],
                 "top_lane": [f'{v["score"]} {v["name"]} - {v["headline"][:60]}' for v in ranked if in_lane(v)][:6]})


def cmd_score(args) -> int:
    st = settings(args.home)
    leads = load(args.home, "leads.json", {})
    ctx = score_ctx(args.home, st)
    before = {k: qualifies(v, st) for k, v in leads.items() if v.get("status") == "new"}
    for v in leads.values():
        rescore(v, ctx)
        if not v.get("source") and lead_source(v):
            v["source"] = lead_source(v)
            v.setdefault("sources", [v["source"]])
        if in_lane(v) and not v.get("lane_origin"):
            v["lane_origin"] = "sweep"  # every lane lead before the registry existed came from the sweep's roles
    save(args.home, "leads.json", leads)
    after = {k: qualifies(leads[k], st) for k in before}
    return emit({"rescored": len(leads), "by_status": dict(Counter(v["status"] for v in leads.values())),
                 "pile_before": sum(before.values()), "pile_after": sum(after.values()),
                 "dropped_from_pile": sorted(leads[k]["name"] for k in before if before[k] and not after[k])[:40],
                 "joined_pile": sorted(leads[k]["name"] for k in before if after[k] and not before[k])[:40],
                 "gated": sum(1 for v in leads.values() if v.get("gate"))})


# ---------------------------------------------------------------- outreach

def pacing(home: str) -> dict:
    p = load(home, "pacing.json", {})
    if p.get("day") != TODAY:
        p.update(day=TODAY, connect=0, inmail=0, message=0)
    p.setdefault("last", {})
    return p


def quotas(p: dict, st: dict) -> dict:
    caps = {"connect": st["connect_per_day"], "inmail": st["inmail_per_day"], "message": st["message_per_day"]}
    return {k: {"used": p.get(k, 0), "cap": v, "left": None if v is None else max(0, v - p.get(k, 0))}
            for k, v in caps.items()} | \
        {"inmail_credits_hint": p.get("inmail_credits")}


def contacted(home: str) -> dict:
    return load(home, "contacts.json", {})


def cmd_plan(args) -> int:
    st = settings(args.home)
    leads = load(args.home, "leads.json", {})
    cts = contacted(args.home)
    p = pacing(args.home)
    save(args.home, "pacing.json", p)
    q = quotas(p, st)
    cos = companies(args.home, cts)
    pile, capped = [], []
    for key, v in leads.items():
        if v.get("status") != "new" or not qualifies(v, st):
            continue
        if cts.get(key, {}).get("status") == "sent":
            v["status"] = "contacted"  # contacted elsewhere (e.g. by the old autopilot follow-ups)
            continue
        if is_banned(v.get("company", ""), st):
            continue
        pile.append(v)
    # best first; 1st-degree connections cost no quota, so they break ties upward. Lane and recruiter scores sit on
    # different rubrics, but the order only matters within a lane: the first pass fills each lane's slots from its
    # own leads, and the backfill pass only runs once one lane has nothing left to offer.
    pile.sort(key=lambda v: (-v["score"], not first_degree(v), v.get("found_at", "")))
    save(args.home, "leads.json", leads)
    needed, _ = need(args.home, st)
    # two separate budgets: connection requests + InMails (`needed`) for everyone else, and plain messages for
    # 1st-degree connections, which spend no connection request (message_per_day None = no cap)
    msg_left = q["message"]["left"]
    # the company lane gets lane_q of the connection + InMail slots first; whatever either lane can't fill is
    # backfilled from the other, so the day's budget is never left unspent for the sake of the split
    lane_q = lane_quota(needed, st)
    # at most max_per_company people per employer/agency, counting everyone contacted before today
    room: dict = {}
    chosen, seen = [], set()
    n = {"first": 0, "lane": 0, "rec": 0}

    def take(v: dict) -> bool:
        left, k = company_room(args.home, st, v.get("company", ""), cos)
        if k:
            left = room.setdefault(k, left)
        if left <= 0:
            capped.append(f'{v["name"]} ({v.get("company", "")})')
            return False
        if k:
            room[k] -= 1
        chosen.append(v)
        return True

    for backfill in (False, True):
        for v in pile:
            key = v.get("profile_url")
            if key in seen or len(chosen) >= args.n:
                continue
            if first_degree(v):
                if backfill or (msg_left is not None and n["first"] >= msg_left):
                    continue
                slot = "first"
            elif n["lane"] + n["rec"] >= needed:
                continue
            elif backfill:
                slot = "lane" if in_lane(v) else "rec"
            elif in_lane(v):
                if n["lane"] >= lane_q:
                    continue
                slot = "lane"
            else:
                if n["rec"] >= needed - lane_q:
                    continue
                slot = "rec"
            seen.add(key)
            if take(v):
                n[slot] += 1
    capped = sorted(set(capped))
    n_first, n_other = n["first"], n["lane"] + n["rec"]
    # what the company lane tasks need to write a role-specific note: the company's open roles, and whether
    # job-autopilot applied there
    lane_on = any(in_lane(v) for v in chosen)
    info = {company_key(t["company"]): t for t in company_targets(args.home, st, for_plan=True)} if lane_on else {}
    applied = applied_companies(args.home) if lane_on else {}
    styles = list(st["cta_styles"] or [])
    tasks = []
    for v in chosen:
        t = {**{k: v.get(k) for k in ("profile_url", "name", "headline", "company", "location", "degree", "language",
                                      "score", "parts", "signals", "evidence", "recent_roles", "vibe")},
             "lead_type": lead_type(v), "cta": cta_for(norm_profile(v.get("profile_url", "")), styles)}
        if in_lane(v):
            c = info.get(company_key(v.get("company", ""))) or {}
            t.update(company_roles=c.get("roles") or v.get("recent_roles") or [],
                     applied=c.get("applied") or applied.get(company_key(v.get("company", ""))),
                     funding=c.get("funding") or "")
        tasks.append(t)
    # connection requests not yet seen accepted: the sender reads "Recently added" connections back to `since`
    awaiting = [v for v in leads.values() if (v.get("contact") or {}).get("channel") == "connect"
                and not (v.get("outcome") or {}).get("accepted_at")]
    since = p.get("connections_checked_at") or min((v["contact"].get("at", "") for v in awaiting), default="")
    accept_check = {"awaiting": len(awaiting), "since": (since or TODAY)[:10]} if awaiting else None
    out = work_dir(args.home)
    return emit({"quotas": q, "needed": needed, "pile": len(pile) - len(capped), "short_by": max(0, needed - n_other),
                 "first_degree_tasks": n_first,
                 "company_lane": {"quota": lane_q, "planned": n["lane"], "recruiters_planned": n["rec"],
                                  "short_by": max(0, lane_q - n["lane"])},
                 "cta_styles": styles, "accept_check": accept_check,
                 "max_per_company": st["max_per_company"], "held_by_company_cap": capped, "tasks": tasks,
                 "order": "1st-degree leads get a plain message whatever the connection budget says (it never counts toward it); "
                          "everyone else: connection requests first, then InMail while credits last",
                 "banned_companies": st["banned_companies"], "target_role": st["target_role"],
                 "candidate": userconfig.snapshot(),
                 "contacts_file": enrich.host_path(rc_dir(args.home) / "contacts.json"),
                 "result": enrich.host_path(out / f"outreach_result_{NOW.strftime('%H%M')}.json")})


def cmd_check(args) -> int:
    st = settings(args.home)
    p = pacing(args.home)
    cts = contacted(args.home)
    who = norm_profile(args.profile)
    res = {"kind": args.kind, "who": who}
    prev = cts.get(who)
    q = quotas(p, st)
    company = args.company or load(args.home, "leads.json", {}).get(who, {}).get("company", "")
    room, ckey = company_room(args.home, st, company)
    if prev and prev.get("status") == "sent":
        res.update(allowed=False, reason=f"already contacted on {prev.get('at', '')[:10]} via {prev.get('channel')}")
    elif room <= 0:
        res.update(allowed=False, reason=f"company cap: {st['max_per_company']} people already contacted at {company}")
    elif q[args.kind]["left"] is not None and q[args.kind]["left"] <= 0:
        res.update(allowed=False, reason=f"daily {args.kind} cap of {q[args.kind]['cap']} reached")
    else:
        last = p["last"].get(args.kind)
        gap = st["gap_seconds"].get(args.kind, 60)
        wait = gap - (NOW - datetime.fromisoformat(last)).total_seconds() if last else 0
        res.update(allowed=wait <= 0, left_today=q[args.kind]["left"], company=company, company_room=room,
                   **({"wait_seconds": math.ceil(wait), "reason": "minimum gap"} if wait > 0 else {}))
    save(args.home, "pacing.json", p)
    return emit(res)


# a first message never proposes a call, chat or meeting (references/message.md); record flags any that slipped through
CALL_ASK = re.compile(r"\b(worth an? (short |quick )?(call|chat)|(quick|short) (call|chat)|hop on a call|call this week|"
                      r"set up \d+ ?min|\d+ minutes\b|calendly|vale uma conversa|bate-papo|marcar uma (call|conversa|reuni)|"
                      r"llamada|charla|agendamos|tienes \d+ minutos)", re.I)


_BAD_ESCAPE = re.compile(r'\\(?!["\\/bfnrtu])')


def load_result(path) -> dict:
    raw = js.fix_path(path).read_text(encoding="utf-8-sig")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return json.loads(_BAD_ESCAPE.sub(r"\\\\", raw))


# a lead the sender passed over only because today's budget ran out stays in the pile for the next day
_BUDGET = re.compile(r"quota|budget|no (connection request )?slots? left|daily \w+ cap|requests? (are )?(all )?used|"
                     r"cap of \d+ reached", re.I)


def deferred(c: dict) -> bool:
    detail = c.get("detail") or ""
    return c.get("status") == "deferred" or (c.get("status") == "skipped" and bool(_BUDGET.search(detail))
                                              and "company cap" not in detail.lower())


def cmd_record(args) -> int:
    res = load_result(args.result)
    st = settings(args.home)
    leads = load(args.home, "leads.json", {})
    cts = contacted(args.home)
    p = pacing(args.home)
    sent = held = 0
    call_asks = []
    for c in res.get("contacts") or []:
        who = norm_profile(c.get("profile_url", ""))
        if not who:
            continue
        if deferred(c):
            held += 1
            continue
        lead = leads.get(who)
        entry = {**{k: c.get(k, "") for k in ("name", "headline", "company", "profile_url", "channel", "subject",
                                              "message", "status", "detail", "cta")},
                 "source": lead_source(lead), "purpose": lead_type(lead or c), "at": NOW.isoformat()}
        if cts.get(who, {}).get("status") == "sent" and entry["status"] != "sent":
            continue
        cts[who] = entry
        if entry["status"] == "sent":
            sent += 1
            if CALL_ASK.search(entry["message"] or ""):
                call_asks.append(entry["name"] or who)
            kind = entry["channel"] if entry["channel"] in ("connect", "inmail", "message") else "message"
            p[kind] = p.get(kind, 0) + 1
            p["last"][kind] = NOW.isoformat()
            if lead:
                lead.update(status="contacted", contact={"at": entry["at"], "channel": kind, "subject": entry["subject"],
                                                         "message": entry["message"], "cta": entry["cta"]})
        elif lead and entry["status"] == "skipped" and lead.get("status") == "new":
            lead.update(status="skipped", skip_reason=entry.get("detail", ""))
    if res.get("inmail_credits") is not None:
        p["inmail_credits"] = res["inmail_credits"]
    accepted = []
    for c in res.get("new_connections") or []:
        url, name = (c, "") if isinstance(c, str) else (c.get("profile_url", ""), c.get("name", ""))
        key = find_lead(leads, url, name)
        lead = leads.get(key)
        if lead and (lead.get("contact") or {}).get("channel") == "connect" and add_event(lead, "accepted", NOW.isoformat()):
            accepted.append(lead.get("name", key))
    if res.get("connections_checked"):
        p["connections_checked_at"] = NOW.isoformat()
    save(args.home, "contacts.json", cts)
    cos = companies(args.home, cts)
    at_cap = sorted(e["name"] for e in cos.values() if e["contacted"] >= st["max_per_company"])
    save(args.home, "leads.json", leads)
    save(args.home, "pacing.json", p)
    return emit({"recorded": len(res.get("contacts") or []), "sent": sent, "quotas": quotas(p, st),
                 "deferred_to_next_day": held, "accepted_now": accepted, "companies_at_cap": at_cap,
                 **({"call_asks": call_asks, "warning": "these sent messages propose a call or chat, which the "
                     "first message must never do; correct the sender's instructions"} if call_asks else {})})


def cmd_mark(args) -> int:
    leads = load(args.home, "leads.json", {})
    who = norm_profile(args.profile)
    if who not in leads:
        return emit({"error": f"unknown lead {who}"})
    leads[who]["status"] = args.status
    if args.note:
        leads[who]["note"] = args.note
    if args.status == "replied":
        add_event(leads[who], "replied", NOW.isoformat(), args.note or "")
    save(args.home, "leads.json", leads)
    return emit({"marked": who, "status": args.status})


def raise_ceiling(home: str, company: str, usd: int, at: str = "", note: str = "") -> dict | None:
    """Add a pay quote to pay_ceilings.json. A company's ceiling is the highest monthly USD it ever quoted, so one
    $12k role lifts an agency out of the gate however many $5k roles it also offered."""
    k = company_key(company)
    if not k or not usd:
        return None
    data = ceilings(home)
    e = data.setdefault(k, {"name": company.strip(), "max_usd_month": 0, "quotes": []})
    q = {"usd_month": int(usd), "at": (at or NOW.isoformat())[:10], "note": note}
    if q not in e["quotes"]:
        e["quotes"].append(q)
    e["max_usd_month"] = max(x["usd_month"] for x in e["quotes"])
    save(home, "pay_ceilings.json", data)
    return {"company": e["name"], "key": k, "max_usd_month": e["max_usd_month"], "quotes": len(e["quotes"])}


def any_lead(leads: dict, profile: str = "", name: str = "") -> str:
    """find_lead, widened to uncontacted leads (an inbound recruiter may already be in the pile)."""
    key = find_lead(leads, profile, name)
    if key:
        return key
    n = _plain(name)
    hits = [k for k, v in leads.items() if n and _plain(v.get("name", "")) == n]
    return hits[0] if len(hits) == 1 else ""


def cmd_outcome(args) -> int:
    """What a contacted recruiter did next, from the sender's acceptance check, linkedin-inbox, or the user.
    `--event quoted --usd N [--company C]` also raises C's pay ceiling, even for a recruiter this skill never
    contacted, so inbound quotes teach the gate too."""
    if args.event == "quoted" and not args.usd:
        return emit({"error": "--event quoted needs --usd <monthly USD>"})
    leads = load(args.home, "leads.json", {})
    at = args.at or NOW.isoformat()
    key = (any_lead if args.event == "quoted" else find_lead)(leads, args.profile or "", args.name or "")
    lead = leads.get(key)
    company = args.company or (lead or {}).get("company", "")
    ceiling = raise_ceiling(args.home, company, args.usd, at, f'{args.name or key}: {args.note or ""}'.strip(": ")) \
        if args.event == "quoted" else None
    if not lead:
        return emit({"lead": False, "reason": "not a recruiter-outreach lead; no lead outcome recorded",
                     **({"ceiling": ceiling} if ceiling else {})})
    changed = add_event(lead, args.event, at, args.note or "", args.usd or 0)
    if args.event == "quoted":
        rescore(lead, score_ctx(args.home))
    save(args.home, "leads.json", leads)
    return emit({"lead": True, "who": key, "name": lead.get("name", ""), "source": lead_source(lead),
                 "event": args.event, "changed": changed, "outcome": lead.get("outcome"),
                 **({"ceiling": ceiling} if ceiling else {})})


TIER_ORDER = {"funded_product": 0, "boutique_search": 0, "unclear": 1, "staff_aug": 2}


def cmd_network_rank(args) -> int:
    """Reorder the pending part of a 1st-degree messaging queue (<home>/recruiters/<file>, {"queue": [...],
    "done": [...]}) so recruiters for funded product companies and boutique search firms go first, unclear ones
    next, and staff augmentation, US-IT vendors, task platforms and companies with a known pay ceiling under the
    floor last. LinkedIn rate-limits messaging per account, so the order decides who is reached at all."""
    st = settings(args.home)
    path = rc_dir(args.home) / args.file
    data = enrich.read_json(path, None)
    if not data or "queue" not in data:
        return emit({"error": f"no queue in {path}"})
    leads = load(args.home, "leads.json", {})
    ceil = ceilings(args.home)
    done = set(data.get("done") or [])
    pending = [e for e in data["queue"] if e.get("key") not in done]
    for e in pending:
        lead = leads.get(e.get("key"), {})
        probe = {"headline": e.get("headline", ""), "company": lead.get("company", ""), "evidence": lead.get("evidence") or []}
        sig = lead.get("signals") or {}
        tier = sig.get("client_tier") or infer_tier(probe)
        known = (ceil.get(company_key(lead.get("company", ""))) or {}).get("max_usd_month")
        quoted = (lead.get("outcome") or {}).get("quoted_usd_month")
        low = any(x is not None and x < st["pay_floor_usd_month"] for x in (known, quoted)) \
            or bool(_VENDOR.search(lead_text(probe)) or _PLATFORM.search(lead_text(probe)))
        e["tier"] = "low_ceiling" if low else tier
        e["tier_rank"] = 3 if low else TIER_ORDER.get(tier, 1)
    pending.sort(key=lambda e: (e["tier_rank"], -(e.get("prio") or 0)))
    data["queue"] = [e for e in data["queue"] if e.get("key") in done] + pending
    data["ranked_by"] = f"client tier, then prio ({TODAY}; recruiters network-rank)"
    enrich.write_json(path, data, indent=1)
    return emit({"pending": len(pending), "done": len(done), "by_tier": dict(Counter(e["tier"] for e in pending)),
                 "next": [f'{e["tier"]} {e["name"]} - {e.get("headline", "")[:60]}' for e in pending[:10]]})


def cmd_ceilings(args) -> int:
    """List pay ceilings, or add one quote by hand (`--set COMPANY --usd N`)."""
    st = settings(args.home)
    if args.set:
        res = raise_ceiling(args.home, args.set, args.usd, args.at or "", args.note or "")
        if res is None:
            return emit({"error": "need a known company name and --usd"})
    data = ceilings(args.home)
    rows = sorted(data.values(), key=lambda e: -e["max_usd_month"])
    return emit({"floor": st["pay_floor_usd_month"], "target": st["pay_target_usd_month"], "companies": len(rows),
                 "gated": [e["name"] for e in rows if e["max_usd_month"] < st["pay_floor_usd_month"]],
                 "at_target": [e["name"] for e in rows if e["max_usd_month"] >= st["pay_target_usd_month"]],
                 "rows": [{"company": e["name"], "max_usd_month": e["max_usd_month"], "quotes": len(e["quotes"])} for e in rows]})


def cmd_import_quotes(args) -> int:
    """Backfill pay quotes collected from the inbox: a JSON list of {name, profile_url?, company?, usd_month, at?,
    note?}. Raises company ceilings, records `quoted` on matching leads, then rescores every lead."""
    rows = load_result(args.file)
    rows = rows.get("quotes", []) if isinstance(rows, dict) else rows
    leads = load(args.home, "leads.json", {})
    n_ceil = n_lead = 0
    for r in rows:
        usd = int(r.get("usd_month") or 0)
        if not usd:
            continue
        at = r.get("at") or NOW.isoformat()
        key = any_lead(leads, r.get("profile_url", ""), r.get("name", ""))
        company = r.get("company") or (leads.get(key) or {}).get("company", "")
        n_ceil += bool(raise_ceiling(args.home, company, usd, at, f'{r.get("name", "")}: {r.get("note", "")}'.strip(": ")))
        if key:
            n_lead += add_event(leads[key], "quoted", at, r.get("note", ""), usd)
    ctx = score_ctx(args.home)
    for v in leads.values():
        rescore(v, ctx)
    save(args.home, "leads.json", leads)
    return emit({"quotes": len(rows), "ceilings_updated": n_ceil, "leads_updated": n_lead,
                 "companies_known": len(ctx["ceilings"]), "gated_leads": sum(1 for v in leads.values() if v.get("gate"))})


# ----------------------------------------------------------------- learning

LEGACY_CTA = "call (asked for a call, before 2026-10-12)"
MIN_SAMPLE = 5  # rates over fewer contacted leads than this are noise


def query_report(home: str, st: dict, leads: dict, top: int = 25) -> dict:
    """Funnel per search, per avenue and per closing style: found -> qualified -> contacted -> accepted -> replied
    -> positive. Each lead counts once, under the search that first found it."""
    runs = load(home, "discovery-runs.json", {})
    by_src: dict = {}
    by_av: dict = {}
    by_cta: dict = {}
    by_type: dict = {}
    by_origin: dict = {}
    for v in leads.values():
        src = lead_source(v) or "unattributed"
        o, ct = v.get("outcome") or {}, v.get("contact") or {}
        sent = bool(ct) or v.get("status") in ("contacted", "replied")
        row = Counter(found=1, relevant=int(not v.get("irrelevant")), qualified=int(qualifies(v, st)),
                      score=v.get("score") or 0, contacted=int(sent), connects=int(ct.get("channel") == "connect"),
                      accepted=int(bool(o.get("accepted_at"))), replied=int(bool(o.get("replied_at") or v.get("replied_at"))),
                      positive=int(bool(o.get("positive_at") or o.get("call_at"))), declined=int(bool(o.get("declined_at"))),
                      quoted=int(bool(o.get("quoted_at"))),
                      quoted_target=int((o.get("quoted_usd_month") or 0) >= (st["pay_target_usd_month"] or 1)))
        by_src.setdefault(src, Counter()).update(row)
        by_av.setdefault(avenue(src), Counter()).update(row)
        by_type.setdefault(lead_type(v), Counter()).update(row)
        if in_lane(v):
            by_origin.setdefault(v.get("lane_origin") or "sweep", Counter()).update(row)
        if sent:
            by_cta.setdefault(ct.get("cta") or LEGACY_CTA, Counter()).update(row)

    def pct(a, b):
        return round(100 * a / b) if b else None

    def rows(table):
        out = []
        for k, c in table.items():
            out.append({"key": k, "avenue": avenue(k), "found": c["found"], "qualified": c["qualified"],
                        "avg_score": round(c["score"] / c["found"]) if c["found"] else 0, "contacted": c["contacted"],
                        "accepted": c["accepted"], "replied": c["replied"], "positive": c["positive"], "declined": c["declined"],
                        "quoted": c["quoted"], "quoted_target": c["quoted_target"],
                        "qualified_pct": pct(c["qualified"], c["found"]), "accept_pct": pct(c["accepted"], c["connects"]),
                        "reply_pct": pct(c["replied"], c["contacted"]),
                        "quoted_target_pct": pct(c["quoted_target"], c["quoted"]), "small_sample": c["contacted"] < MIN_SAMPLE,
                        **({"last_run": runs[k][:10]} if k in runs else {})})
        # the goal is recruiters with roles at the target pay, so a search that found one outranks any reply count
        return sorted(out, key=lambda r: (-r["quoted_target"], -r["positive"], -r["replied"], -r["accepted"], -r["qualified"],
                                          -r["found"], r["key"]))

    # a snowball search runs as 'people:<company> recruiter' but its leads are filed under 'snowball:<company>'
    dead = sorted(k for k in runs if k not in by_src
                  and not (k.startswith("people:") and source_key("snowball:" + k[7:]) in by_src))
    return {"built": NOW.strftime("%Y-%m-%d %H:%M UTC"), "min_score": st["min_score"],
            "note": f"Each lead counts once, under the search that first found it. Rows are ranked by quoted_target, the "
                    f"recruiters who quoted ${st['pay_target_usd_month']:,}/month or more (quoted_target_pct is over everyone "
                    f"who quoted any pay). accept_pct is over connection requests, reply_pct over everyone contacted; rows "
                    f"with fewer than {MIN_SAMPLE} contacted are small samples.",
            "by_avenue": rows(by_av), "by_cta": rows(by_cta), "queries": rows(by_src)[:top],
            # the company-lane experiment: recruiters vs in-house recruiters vs hiring managers on the same outcomes.
            # avg_score is not comparable across these rows (two rubrics); the outcome columns are.
            "by_lead_type": rows(by_type),
            # lane leads by where their company came from: the job sweep's roles or the funded-company registry
            "by_lane_origin": rows(by_origin),
            "searches_total": len(by_src), "ran_without_leads": len(dead), "ran_without_leads_sample": dead[:40]}


def cmd_queries(args) -> int:
    st = settings(args.home)
    report = query_report(args.home, st, load(args.home, "leads.json", {}), args.top)
    save(args.home, "queries.json", report)
    return emit(report)


def cmd_import_contacts(args) -> int:
    src = enrich.read_json(js.fix_path(args.file), {})
    cts = contacted(args.home)
    n = 0
    for who, c in (src.items() if isinstance(src, dict) else ((norm_profile(x.get("profile_url", "")), x) for x in src)):
        who = norm_profile(c.get("profile_url") or who)
        if who.startswith("linkedin.com/in/") and who not in cts:
            cts[who] = {**c, "imported_from": str(args.file)}
            n += 1
    save(args.home, "contacts.json", cts)
    cos = companies(args.home, cts)
    return emit({"imported": n, "contacts_total": len(cts), "companies": len(cos)})


# --------------------------------------------------------------- dashboard

def cmd_dashboard(args) -> int:
    st = settings(args.home)
    leads = load(args.home, "leads.json", {})
    p = pacing(args.home)
    rows = sorted(leads.values(), key=lambda v: (-(v.get("score") or 0), v.get("found_at", "")))
    reg = registry(args.home)
    data = {"built": NOW.strftime("%Y-%m-%d %H:%M UTC"), "quotas": quotas(p, st), "min_score": st["min_score"],
            "min_score_lane": min_for({"lead_type": "in_house"}, st),
            "parts_max": PARTS_MAX, "parts_max_lane": LANE_PARTS_MAX,
            "funded": {"registry": len(reg), "with_board": sum(bool(e.get("ats")) for e in reg.values()),
                       "with_role": sum(any(x.get("region") in TARGET_REGIONS for x in e.get("roles") or [])
                                        for e in reg.values())},
            "leads": [{"name": v.get("name", ""), "headline": v.get("headline", ""), "company": v.get("company", ""),
                       "location": v.get("location", ""), "profile_url": v.get("profile_url", ""), "degree": v.get("degree", ""),
                       "score": v.get("score", 0), "parts": v.get("parts") or {}, "signals": v.get("signals") or {},
                       "gate": v.get("gate", ""), "leadType": lead_type(v), "rubric": rubric(v),
                       "laneOrigin": v.get("lane_origin", ""),
                       "evidence": (v.get("evidence") or [])[:4], "recent_roles": (v.get("recent_roles") or [])[:3],
                       "status": v.get("status", "new"), "skipReason": v.get("skip_reason", ""),
                       "contact": v.get("contact"), "foundAt": (v.get("found_at") or "")[:10],
                       "source": lead_source(v), "outcome": {k: (x or "")[:10] for k, x in (v.get("outcome") or {}).items()
                                                             if k.endswith("_at")},
                       "repliedAt": (v.get("replied_at") or "")[:10]} for v in rows],
            "queries": query_report(args.home, st, leads, 15)}
    save(args.home, "dashboard.json", data)
    return emit({"leads": len(rows), "by_status": dict(Counter(v["status"] for v in rows)),
                 "file": enrich.host_path(rc_dir(args.home) / "dashboard.json")})


def cmd_status(args) -> int:
    st = settings(args.home)
    leads = load(args.home, "leads.json", {})
    p = pacing(args.home)
    new = contactable(leads, st, args.home)
    needed, q = need(args.home, st)
    # 1st-degree leads wait on the message channel, not on connection requests: done needs both budgets spent
    first = sum(first_degree(v) for v in new) if q["message"]["cap"] != 0 else 0
    cos = companies(args.home)
    at_cap = sorted(e["name"] for e in cos.values() if e["contacted"] >= st["max_per_company"])
    lane_q = lane_quota(needed, st)
    lane_have = sum(in_lane(v) and not first_degree(v) for v in new)
    return emit({"leads": len(leads), "by_status": dict(Counter(v["status"] for v in leads.values())),
                 "contactable_now": sum(not first_degree(v) for v in new), "first_degree_pending": first,
                 "by_lead_type": dict(Counter(lead_type(v) for v in leads.values())),
                 "company_lane": {"quota": lane_q, "contactable_now": lane_have, "short_by": max(0, lane_q - lane_have)},
                 "needed": needed, "done": needed == 0 and first == 0,
                 "companies_contacted": len(cos), "companies_at_cap": at_cap, "max_per_company": st["max_per_company"],
                 "quotas": q, "inmail_credits": p.get("inmail_credits"), "contacts": len(contacted(args.home)),
                 "funded_registry": len(registry(args.home)),
                 "top": [f'{v["score"]} {v["name"]} - {v["headline"][:50]}' for v in new if not in_lane(v)][:8],
                 "top_lane": [f'{v["score"]} {v["name"]} - {v["headline"][:50]}' for v in new if in_lane(v)][:6]})


def cmd_companies(args) -> int:
    """The per-company memory: who was contacted where, and which companies are at the cap."""
    st = settings(args.home)
    cos = companies(args.home, contacted(args.home))  # rebuild from contacts so it is always current
    rows = sorted(cos.values(), key=lambda e: (-e["contacted"], e["name"].lower()))
    return emit({"max_per_company": st["max_per_company"], "companies": len(rows),
                 "at_cap": [e["name"] for e in rows if e["contacted"] >= st["max_per_company"]],
                 "rows": [{"company": e["name"], "contacted": e["contacted"], "last": e["last"][:10],
                           "profiles": e["profiles"]} for e in rows]})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--home", default=os.environ.get("JOBSWEEP_HOME") or userconfig.home())
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("discover-plan"); p.add_argument("--max-leads", type=int, default=0); p.set_defaults(fn=cmd_discover_plan)
    p = sub.add_parser("ingest"); p.add_argument("--result", required=True); p.set_defaults(fn=cmd_ingest)
    sub.add_parser("score").set_defaults(fn=cmd_score)
    p = sub.add_parser("plan"); p.add_argument("--n", type=int, default=30); p.set_defaults(fn=cmd_plan)
    p = sub.add_parser("check"); p.add_argument("--kind", required=True, choices=["connect", "inmail", "message"])
    p.add_argument("--profile", required=True); p.add_argument("--company", default="")
    p.set_defaults(fn=cmd_check)
    p = sub.add_parser("record"); p.add_argument("--result", required=True); p.set_defaults(fn=cmd_record)
    p = sub.add_parser("mark"); p.add_argument("--profile", required=True)
    p.add_argument("--status", required=True, choices=["replied", "skipped", "contacted", "new"]); p.add_argument("--note")
    p.set_defaults(fn=cmd_mark)
    p = sub.add_parser("outcome"); p.add_argument("--profile", default=""); p.add_argument("--name", default="")
    p.add_argument("--event", required=True, choices=list(EVENTS)); p.add_argument("--note"); p.add_argument("--at", default="")
    p.add_argument("--usd", type=int, default=0, help="with --event quoted: the quoted pay in USD per month")
    p.add_argument("--company", default="", help="with --event quoted: the agency or employer the quote belongs to")
    p.set_defaults(fn=cmd_outcome)
    p = sub.add_parser("ceilings"); p.add_argument("--set", default="", metavar="COMPANY"); p.add_argument("--usd", type=int, default=0)
    p.add_argument("--note", default=""); p.add_argument("--at", default=""); p.set_defaults(fn=cmd_ceilings)
    p = sub.add_parser("import-quotes"); p.add_argument("--file", required=True); p.set_defaults(fn=cmd_import_quotes)
    p = sub.add_parser("network-rank"); p.add_argument("--file", default="network_queue.json"); p.set_defaults(fn=cmd_network_rank)
    p = sub.add_parser("targets"); p.add_argument("--top", type=int, default=20); p.set_defaults(fn=cmd_targets)
    sub.add_parser("funding-plan").set_defaults(fn=cmd_funding_plan)
    p = sub.add_parser("ingest-funded"); p.add_argument("--result", required=True); p.set_defaults(fn=cmd_ingest_funded)
    p = sub.add_parser("refresh-boards"); p.add_argument("--max-age-hours", type=int, default=None)
    p.set_defaults(fn=cmd_refresh_boards)
    p = sub.add_parser("queries"); p.add_argument("--top", type=int, default=25); p.set_defaults(fn=cmd_queries)
    p = sub.add_parser("import-contacts"); p.add_argument("--file", required=True); p.set_defaults(fn=cmd_import_contacts)
    sub.add_parser("dashboard").set_defaults(fn=cmd_dashboard)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("companies").set_defaults(fn=cmd_companies)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
