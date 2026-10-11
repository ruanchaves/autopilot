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
    recruiters.py import-contacts --file FILE               # seed contacts from job-autopilot's contacts.json
A run is done only when `status` says done: connection requests and InMails for the day are all used.
    recruiters.py dashboard                                 # dashboard.json for the role explorer's Outreach view
    recruiters.py status
    recruiters.py companies                                 # people contacted per company/agency vs max_per_company

Settings come from config.yaml at the repo root (recruiter_outreach, role, pay, banned_companies).
Everything else lives in <home>/recruiters/:
    leads.json      every recruiter found: profile, signals, evidence, score, status
    contacts.json   everyone ever contacted (seeded from autopilot), with the exact message sent
    pacing.json     today's counters, last-send times, InMail credits last seen
    work/<date>/    discovery and outreach result files
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

SWEEP = Path(__file__).resolve().parents[2] / "job-source-sweep" / "scripts"
sys.path.insert(0, str(SWEEP))
import jobsweep as js  # noqa: E402
import enrich  # noqa: E402
import userconfig  # noqa: E402  (config.yaml at the repo root)

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
    "message_per_day": 20,
    "gap_seconds": {"connect": 90, "inmail": 120, "message": 60},
    "min_score": 45,           # leads below this are not contacted
    "max_per_company": 3,      # people ever contacted at one company or agency (companies.json keeps the count)
    "stale_days": 60,          # a lead's signals are re-probed after this many days
    "people_queries": [],      # from config.yaml recruiter_outreach.people_queries
    "post_queries": [],
    # filled in from config.yaml by settings(): target_role, pay_target_usd_month, banned_companies
}


def settings(home: str) -> dict:
    """DEFAULT_SETTINGS under config.yaml's recruiter_outreach section, plus the shared role, pay
    and banned-company values."""
    cfg = userconfig.load()
    s = dict(DEFAULT_SETTINGS)
    s.update(cfg.get("recruiter_outreach") or {})
    s.update(target_role=(cfg.get("role") or {}).get("title", ""), pay_target_usd_month=userconfig.pay().get("target_usd_month", 0),
             banned_companies=userconfig.banned_companies())
    return s


def squash(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


_CO_SUFFIX = re.compile(r"\b(inc|llc|ltd|ltda|limited|corp|corporation|co|company|group|gmbh|sa|s\.a\.|srl|plc|lp)\b\.?")
_CO_TRAIL = {"systems", "system", "technologies", "technology", "tech", "solutions", "labs", "consulting", "software",
             "digital", "services", "staffing", "recruitment", "recruiting", "talent", "global", "international",
             "latam", "brasil", "brazil", "usa", "us"}
_CO_NOISE = {"", "unknown", "na", "n/a", "none", "self", "freelance", "independent", "selfemployed"}


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


# ---------------------------------------------------------------- scoring
# Four questions, in the user's words: is the recruiter explicitly hiring in LATAM and/or
# worldwide? active on LinkedIn? posting roles aligned with the target role? posting roles
# aligned with the target pay? A recruiter who answers yes to all four scores near 100.

SCOPE = {"latam": 35, "worldwide": 35, "nearshore": 32, "remote_unclear": 15, "unknown": 5, "local_only": 0}
PAY = {"at_or_above": 20, "near": 8, "unknown": 5, "below": 0}


def score_parts(sig: dict) -> dict:
    scope = SCOPE.get(sig.get("hiring_scope") or "unknown", 5)
    days = sig.get("last_activity_days")
    active = 3 if days is None else 20 if days <= 7 else 14 if days <= 30 else 7 if days <= 90 else 0
    role_n = int(sig.get("role_posts_90d") or 0)
    adj_n = int(sig.get("adjacent_posts_90d") or 0)
    role = 25 if role_n >= 2 else 16 if role_n == 1 else 6 if adj_n else 0
    pay = PAY.get(sig.get("pay_signal") or "unknown", 5)
    penalty = -15 if sig.get("local_rate_shop") else 0
    return {"scope": scope, "active": active, "role": role, "pay": pay, "penalty": penalty}


def total(parts: dict) -> int:
    return max(0, min(100, sum(parts.values())))


def rescore(lead: dict) -> dict:
    parts = score_parts(lead.get("signals") or {})
    lead["parts"] = parts
    lead["score"] = 0 if lead.get("irrelevant") else total(parts)
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
    """Uncontacted leads at or above min_score whose company still has room under max_per_company."""
    cos = companies(home) if home else {}
    return sorted((v for v in leads.values() if v["status"] == "new" and v["score"] >= st["min_score"]
                   and (not home or company_room(home, st, v.get("company", ""), cos)[0] > 0)),
                  key=lambda v: -v["score"])


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
    have = len(contactable(leads, st, args.home))
    # phrasings already run this week whose deeper result pages haven't been read yet
    deeper = [{"kind": k.split(":", 1)[0], "query": k.split(":", 1)[1], "start_page": depth.get(k, 2) + 1}
              for k, at in runs.items() if at >= cutoff and depth.get(k, 2) < MAX_PAGES]
    # employers and agencies of good leads: their colleagues recruit for the same roles
    snowball = []
    for v in sorted(leads.values(), key=lambda v: -v["score"]):
        c = (v.get("company") or "").strip()
        if v["score"] >= st["min_score"] and c and c.lower() != "unknown" and not is_banned(c, st) and c not in snowball:
            snowball.append(c)
    cand = userconfig.snapshot()
    seeds = {"role_terms": [st["target_role"]] + list(cand.get("core_areas") or []),
             "region_terms": [cand.get("country"), cand.get("region")] + list(cand.get("region_aliases") or []),
             "recruiter_nouns": ["recruiter", "technical recruiter", "talent partner", "talent acquisition",
                                 "sourcer", "headhunter", "hiring", "we're hiring"],
             "languages": [x for x in ["English", cand.get("native_language")] if x],
             "pay_terms": ["USD", "contractor", "B2B", f"${st['pay_target_usd_month'] // 1000}k"],
             "hashtags": ["#hiring", f"#{(cand.get('region') or '').lower()}", "#remotework", "#" + squash(st["target_role"])]}
    max_leads = args.max_leads or max(30, needed * 2)
    out = work_dir(args.home)
    return emit({"people_queries": people, "post_queries": posts, "exhausted": not people and not posts,
                 "needed": needed, "contactable_now": have, "quotas": q, "max_leads": max_leads,
                 "queries_tried": sorted(runs), "deeper": deeper[:12], "snowball_companies": snowball[:15],
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
    for k in list(runs):
        if (pages.get(k) or 0) > depth.get(k, 0):
            depth[k] = int(pages[k])
    added = updated = banned = 0
    for L in res.get("leads") or []:
        key = norm_profile(L.get("profile_url", ""))
        if not key.startswith("linkedin.com/in/"):
            continue
        if is_banned(L.get("company", ""), st):
            banned += 1
            continue
        cur = leads.get(key)
        entry = {**(cur or {"found_at": NOW.isoformat(), "status": "new", "contact": None}),
                 "profile_url": L.get("profile_url"), "name": L.get("name", ""), "headline": L.get("headline", ""),
                 "company": L.get("company", ""), "location": L.get("location", ""), "degree": L.get("degree", ""),
                 "language": L.get("language", "en"), "signals": L.get("signals") or {},
                 "evidence": L.get("evidence") or [], "recent_roles": L.get("recent_roles") or [],
                 "vibe": L.get("vibe", ""), "irrelevant": bool(L.get("irrelevant")), "found_via": L.get("found_via", ""),
                 "probed_at": NOW.isoformat()}
        if entry["irrelevant"] and entry["status"] == "new":
            entry["status"] = "skipped"
            entry["skip_reason"] = L.get("irrelevant_reason", "not a relevant recruiter")
        rescore(entry)
        leads[key] = entry
        added += cur is None
        updated += cur is not None
    save(args.home, "leads.json", leads)
    save(args.home, "discovery-runs.json", runs)
    save(args.home, "discovery-depth.json", depth)
    ranked = contactable(leads, st, args.home)
    needed, _ = need(args.home, st)
    return emit({"added": added, "updated": updated, "skipped_banned": banned, "leads_total": len(leads),
                 "contactable_now": len(ranked), "needed": needed,
                 "top": [f'{v["score"]} {v["name"]} - {v["headline"][:60]}' for v in ranked[:10]]})


def cmd_score(args) -> int:
    leads = load(args.home, "leads.json", {})
    for v in leads.values():
        rescore(v)
    save(args.home, "leads.json", leads)
    return emit({"rescored": len(leads), "by_status": dict(Counter(v["status"] for v in leads.values()))})


# ---------------------------------------------------------------- outreach

def pacing(home: str) -> dict:
    p = load(home, "pacing.json", {})
    if p.get("day") != TODAY:
        p.update(day=TODAY, connect=0, inmail=0, message=0)
    p.setdefault("last", {})
    return p


def quotas(p: dict, st: dict) -> dict:
    caps = {"connect": st["connect_per_day"], "inmail": st["inmail_per_day"], "message": st["message_per_day"]}
    return {k: {"used": p.get(k, 0), "cap": v, "left": max(0, v - p.get(k, 0))} for k, v in caps.items()} | \
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
        if v.get("status") != "new" or v["score"] < st["min_score"]:
            continue
        if cts.get(key, {}).get("status") == "sent":
            v["status"] = "contacted"  # contacted elsewhere (e.g. by the old autopilot follow-ups)
            continue
        if is_banned(v.get("company", ""), st):
            continue
        pile.append(v)
    # best first; 1st-degree connections cost no quota, so they break ties upward
    pile.sort(key=lambda v: (-v["score"], v.get("degree") != "1st", v.get("found_at", "")))
    save(args.home, "leads.json", leads)
    needed, _ = need(args.home, st)
    budget = needed + q["message"]["left"]
    # at most max_per_company people per employer/agency, counting everyone contacted before today
    room: dict = {}
    chosen = []
    for v in pile:
        left, k = company_room(args.home, st, v.get("company", ""), cos)
        if k:
            left = room.setdefault(k, left)
        if left <= 0:
            capped.append(f'{v["name"]} ({v.get("company", "")})')
            continue
        if k:
            room[k] -= 1
        chosen.append(v)
        if len(chosen) >= min(args.n, budget):
            break
    tasks = [{k: v.get(k) for k in ("profile_url", "name", "headline", "company", "location", "degree", "language",
                                     "score", "parts", "signals", "evidence", "recent_roles", "vibe")}
             for v in chosen]
    out = work_dir(args.home)
    return emit({"quotas": q, "needed": needed, "pile": len(pile) - len(capped), "short_by": max(0, needed - len(chosen)),
                 "max_per_company": st["max_per_company"], "held_by_company_cap": capped, "tasks": tasks,
                 "order": "connection requests first; when they're used up, InMail while credits last; 1st-degree leads get a plain message",
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
    elif q[args.kind]["left"] <= 0:
        res.update(allowed=False, reason=f"daily {args.kind} cap of {q[args.kind]['cap']} reached")
    else:
        last = p["last"].get(args.kind)
        gap = st["gap_seconds"].get(args.kind, 60)
        wait = gap - (NOW - datetime.fromisoformat(last)).total_seconds() if last else 0
        res.update(allowed=wait <= 0, left_today=q[args.kind]["left"], company=company, company_room=room,
                   **({"wait_seconds": math.ceil(wait), "reason": "minimum gap"} if wait > 0 else {}))
    save(args.home, "pacing.json", p)
    return emit(res)


_BAD_ESCAPE = re.compile(r'\\(?!["\\/bfnrtu])')


def load_result(path) -> dict:
    raw = js.fix_path(path).read_text(encoding="utf-8-sig")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return json.loads(_BAD_ESCAPE.sub(r"\\\\", raw))


def cmd_record(args) -> int:
    res = load_result(args.result)
    st = settings(args.home)
    leads = load(args.home, "leads.json", {})
    cts = contacted(args.home)
    p = pacing(args.home)
    sent = 0
    for c in res.get("contacts") or []:
        who = norm_profile(c.get("profile_url", ""))
        if not who:
            continue
        entry = {**{k: c.get(k, "") for k in ("name", "headline", "company", "profile_url", "channel", "subject",
                                              "message", "status", "detail")},
                 "purpose": "recruiter", "at": NOW.isoformat()}
        if cts.get(who, {}).get("status") == "sent" and entry["status"] != "sent":
            continue
        cts[who] = entry
        lead = leads.get(who)
        if entry["status"] == "sent":
            sent += 1
            kind = entry["channel"] if entry["channel"] in ("connect", "inmail", "message") else "message"
            p[kind] = p.get(kind, 0) + 1
            p["last"][kind] = NOW.isoformat()
            if lead:
                lead.update(status="contacted", contact={"at": entry["at"], "channel": kind, "subject": entry["subject"],
                                                         "message": entry["message"]})
        elif lead and entry["status"] == "skipped" and lead.get("status") == "new":
            lead.update(status="skipped", skip_reason=entry.get("detail", ""))
    if res.get("inmail_credits") is not None:
        p["inmail_credits"] = res["inmail_credits"]
    save(args.home, "contacts.json", cts)
    cos = companies(args.home, cts)
    at_cap = sorted(e["name"] for e in cos.values() if e["contacted"] >= st["max_per_company"])
    save(args.home, "leads.json", leads)
    save(args.home, "pacing.json", p)
    return emit({"recorded": len(res.get("contacts") or []), "sent": sent, "quotas": quotas(p, st),
                 "companies_at_cap": at_cap})


def cmd_mark(args) -> int:
    leads = load(args.home, "leads.json", {})
    who = norm_profile(args.profile)
    if who not in leads:
        return emit({"error": f"unknown lead {who}"})
    leads[who]["status"] = args.status
    if args.note:
        leads[who]["note"] = args.note
    if args.status == "replied":
        leads[who]["replied_at"] = NOW.isoformat()
    save(args.home, "leads.json", leads)
    return emit({"marked": who, "status": args.status})


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
    data = {"built": NOW.strftime("%Y-%m-%d %H:%M UTC"), "quotas": quotas(p, st), "min_score": st["min_score"],
            "leads": [{"name": v.get("name", ""), "headline": v.get("headline", ""), "company": v.get("company", ""),
                       "location": v.get("location", ""), "profile_url": v.get("profile_url", ""), "degree": v.get("degree", ""),
                       "score": v.get("score", 0), "parts": v.get("parts") or {}, "signals": v.get("signals") or {},
                       "evidence": (v.get("evidence") or [])[:4], "recent_roles": (v.get("recent_roles") or [])[:3],
                       "status": v.get("status", "new"), "skipReason": v.get("skip_reason", ""),
                       "contact": v.get("contact"), "foundAt": (v.get("found_at") or "")[:10],
                       "repliedAt": (v.get("replied_at") or "")[:10]} for v in rows]}
    save(args.home, "dashboard.json", data)
    return emit({"leads": len(rows), "by_status": dict(Counter(v["status"] for v in rows)),
                 "file": enrich.host_path(rc_dir(args.home) / "dashboard.json")})


def cmd_status(args) -> int:
    st = settings(args.home)
    leads = load(args.home, "leads.json", {})
    p = pacing(args.home)
    new = contactable(leads, st, args.home)
    needed, q = need(args.home, st)
    cos = companies(args.home)
    at_cap = sorted(e["name"] for e in cos.values() if e["contacted"] >= st["max_per_company"])
    return emit({"leads": len(leads), "by_status": dict(Counter(v["status"] for v in leads.values())),
                 "contactable_now": len(new), "needed": needed, "done": needed == 0,
                 "companies_contacted": len(cos), "companies_at_cap": at_cap, "max_per_company": st["max_per_company"],
                 "quotas": q, "inmail_credits": p.get("inmail_credits"), "contacts": len(contacted(args.home)),
                 "top": [f'{v["score"]} {v["name"]} - {v["headline"][:50]}' for v in new[:8]]})


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
    p = sub.add_parser("import-contacts"); p.add_argument("--file", required=True); p.set_defaults(fn=cmd_import_contacts)
    sub.add_parser("dashboard").set_defaults(fn=cmd_dashboard)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    sub.add_parser("companies").set_defaults(fn=cmd_companies)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
