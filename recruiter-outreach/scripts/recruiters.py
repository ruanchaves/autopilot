#!/usr/bin/env python3
"""Recruiter outreach: a ranked database of LinkedIn recruiters who place AI engineers from LATAM
or worldwide, consumed by connection requests (20 a day) and InMails (while credits last).
Separate from job-autopilot: nothing here depends on an application.

    recruiters.py discover-plan [--max-leads 30]          # queries + result file for the discovery subagent
    recruiters.py ingest        --result FILE               # discovered leads with their signals -> leads.json
    recruiters.py score                                     # recompute every lead's score (after a settings change)
    recruiters.py plan          [--n 30]                    # today's ranked pile, quotas, task file for the outreach subagent
    recruiters.py check         --kind connect|inmail|message --profile URL
    recruiters.py record        --result FILE               # outreach result: contacts, counters, credits
    recruiters.py mark          --profile URL --status replied|skipped|contacted [--note TEXT]
    recruiters.py import-contacts --file FILE               # seed contacts from job-autopilot's contacts.json
    recruiters.py dashboard                                 # dashboard.json for the role explorer's Outreach view
    recruiters.py status

Everything lives in <home>/recruiters/ (<home> is job-source-sweep's, default ~/jobsweep):
    settings.json   quotas, queries, scoring thresholds, banned companies
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
    "target_role": "AI Engineer",
    "pay_target_usd_month": 10000,
    # the user's current employers/clients: their recruiters are never contacted
    "banned_companies": ["ExampleEmployer", "Example Labs", "example-client.io", "Example Client Co"],
    # daily caps (UTC day). Connection requests are the user's hard limit; InMails are bounded by
    # the credits LinkedIn shows, and this cap is only a second guard.
    "connect_per_day": 20,
    "inmail_per_day": 15,
    "message_per_day": 20,
    "gap_seconds": {"connect": 90, "inmail": 120, "message": 60},
    # leads below this score are not contacted
    "min_score": 45,
    # a lead's signals are re-probed after this many days
    "stale_days": 60,
    # LinkedIn people-search phrasings (the discovery subagent picks the ones not run recently)
    "people_queries": [
        "technical recruiter AI engineer LATAM",
        "AI ML recruiter Latin America remote",
        "talent partner machine learning engineers worldwide remote",
        "recruiter hiring LLM engineers remote anywhere",
        "nearshore recruiter senior AI engineer Brazil",
        "talent acquisition GenAI engineers remote LATAM",
        "recruiter contract AI engineer USD remote",
        "recrutador tech IA remoto internacional",
    ],
    # LinkedIn post-search phrasings: hiring posts whose authors are recruiters
    "post_queries": [
        "hiring AI engineer LATAM remote",
        "hiring senior AI engineer remote worldwide",
        "LLM engineer remote Latin America hiring",
        "machine learning engineer remote anywhere hiring contractor",
        "AI engineer remote Brazil USD",
        "GenAI engineer nearshore hiring",
    ],
}


def settings(home: str) -> dict:
    s = dict(DEFAULT_SETTINGS)
    s.update(load(home, "settings.json", {}))
    return s


def squash(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


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

def cmd_discover_plan(args) -> int:
    st = settings(args.home)
    leads = load(args.home, "leads.json", {})
    runs = load(args.home, "discovery-runs.json", {})
    cutoff = (NOW - timedelta(days=7)).isoformat()
    people = [q for q in st["people_queries"] if (runs.get("people:" + q) or "") < cutoff]
    posts = [q for q in st["post_queries"] if (runs.get("posts:" + q) or "") < cutoff]
    stale = (NOW - timedelta(days=st["stale_days"])).isoformat()
    reprobe = [{"profile_url": v["profile_url"], "name": v.get("name"), "probed_at": v.get("probed_at")}
               for v in leads.values() if (v.get("probed_at") or "") < stale and v.get("status") == "new"][:10]
    out = work_dir(args.home)
    return emit({"people_queries": people, "post_queries": posts, "max_leads": args.max_leads,
                 "known_profiles": sorted(leads), "reprobe": reprobe,
                 "target_role": st["target_role"], "pay_target_usd_month": st["pay_target_usd_month"],
                 "banned_companies": st["banned_companies"],
                 "result": enrich.host_path(out / f"discover_result_{NOW.strftime('%H%M')}.json")})


def cmd_ingest(args) -> int:
    res = load_result(args.result)
    st = settings(args.home)
    leads = load(args.home, "leads.json", {})
    runs = load(args.home, "discovery-runs.json", {})
    for q in res.get("people_queries_run") or []:
        runs["people:" + q] = NOW.isoformat()
    for q in res.get("post_queries_run") or []:
        runs["posts:" + q] = NOW.isoformat()
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
    ranked = sorted((v for v in leads.values() if v["status"] == "new"), key=lambda v: -v["score"])
    return emit({"added": added, "updated": updated, "skipped_banned": banned, "leads_total": len(leads),
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
    pile = []
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
    budget = q["connect"]["left"] + q["inmail"]["left"] + q["message"]["left"]
    tasks = [{k: v.get(k) for k in ("profile_url", "name", "headline", "company", "location", "degree", "language",
                                     "score", "parts", "signals", "evidence", "recent_roles", "vibe")}
             for v in pile[: min(args.n, budget)]]
    out = work_dir(args.home)
    return emit({"quotas": q, "pile": len(pile), "tasks": tasks,
                 "order": "connection requests first; when they're used up, InMail while credits last; 1st-degree leads get a plain message",
                 "banned_companies": st["banned_companies"], "target_role": st["target_role"],
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
    if prev and prev.get("status") == "sent":
        res.update(allowed=False, reason=f"already contacted on {prev.get('at', '')[:10]} via {prev.get('channel')}")
    elif q[args.kind]["left"] <= 0:
        res.update(allowed=False, reason=f"daily {args.kind} cap of {q[args.kind]['cap']} reached")
    else:
        last = p["last"].get(args.kind)
        gap = st["gap_seconds"].get(args.kind, 60)
        wait = gap - (NOW - datetime.fromisoformat(last)).total_seconds() if last else 0
        res.update(allowed=wait <= 0, left_today=q[args.kind]["left"],
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
    save(args.home, "leads.json", leads)
    save(args.home, "pacing.json", p)
    return emit({"recorded": len(res.get("contacts") or []), "sent": sent, "quotas": quotas(p, st)})


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
    return emit({"imported": n, "contacts_total": len(cts)})


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
    new = [v for v in leads.values() if v["status"] == "new" and v["score"] >= st["min_score"]]
    return emit({"leads": len(leads), "by_status": dict(Counter(v["status"] for v in leads.values())),
                 "contactable_now": len(new), "quotas": quotas(p, st), "contacts": len(contacted(args.home)),
                 "top": [f'{v["score"]} {v["name"]} - {v["headline"][:50]}' for v in sorted(new, key=lambda v: -v["score"])[:8]]})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--home", default=os.environ.get("JOBSWEEP_HOME", "~/jobsweep"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("discover-plan"); p.add_argument("--max-leads", type=int, default=30); p.set_defaults(fn=cmd_discover_plan)
    p = sub.add_parser("ingest"); p.add_argument("--result", required=True); p.set_defaults(fn=cmd_ingest)
    sub.add_parser("score").set_defaults(fn=cmd_score)
    p = sub.add_parser("plan"); p.add_argument("--n", type=int, default=30); p.set_defaults(fn=cmd_plan)
    p = sub.add_parser("check"); p.add_argument("--kind", required=True, choices=["connect", "inmail", "message"])
    p.add_argument("--profile", required=True); p.set_defaults(fn=cmd_check)
    p = sub.add_parser("record"); p.add_argument("--result", required=True); p.set_defaults(fn=cmd_record)
    p = sub.add_parser("mark"); p.add_argument("--profile", required=True)
    p.add_argument("--status", required=True, choices=["replied", "skipped", "contacted", "new"]); p.add_argument("--note")
    p.set_defaults(fn=cmd_mark)
    p = sub.add_parser("import-contacts"); p.add_argument("--file", required=True); p.set_defaults(fn=cmd_import_contacts)
    sub.add_parser("dashboard").set_defaults(fn=cmd_dashboard)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
