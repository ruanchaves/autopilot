#!/usr/bin/env python3
"""Job autopilot: global fit scores, the apply queue, per-host pacing, the answer bank, the
application ledger and applika registration. It sits on top of job-source-sweep and reuses
its caches (JD text, eligibility classes, the fit rubric) in the same <home>.

By default the skill never submits an application. The apply subagent fills each form completely
(CV, answers, cover letter when one is required), stops before Submit and leaves the tab open; the
user reviews and submits, then confirms it here (dashboard "Submitted" button or `confirm`), and
only confirmed applications are registered in applika. With config.yaml autopilot.auto_submit: true
the subagent clicks Submit itself and records `applied` with the confirmation it saw.

    autopilot.py score-queue   [--batches 14] [--batch-size 20] [--decisions DIR]
    autopilot.py score-ingest
    autopilot.py queue         [--batches 14] [--decisions DIR]
    autopilot.py tailor-queue  [--n 6]
    autopilot.py pace-start
    autopilot.py next
    autopilot.py pace-check    --host HOST [--company NAME]
    autopilot.py record        --result FILE
    autopilot.py answers       (--import DIR_OR_FILE | --pending)   # dashboard answers, overrides, submitted marks
    autopilot.py confirm       --id ID [--id ID ...] (--submitted | --discarded)
    autopilot.py ready                                            # filled forms waiting for the user
    autopilot.py applika-sync  [--dry-run]
    autopilot.py dashboard                                        # dashboard.json for the role explorer
    autopilot.py status

Settings come from config.yaml at the repo root (sections autopilot, pay, tracker, banned_companies,
candidate). Everything else lives in <home>/autopilot/:
    answer-bank.json   applicant profile, answered/estimated/pending form questions, confirmed/denied skills
    scores.json        score parts per role key (the total is computed on read, so a changed
                       eligibility class re-ranks a role without re-scoring it)
    ledger.json        one entry per role id: status, attempts, tailored CV, answers given, cover letter, applika id
    pacing.json        per-host budgets that adapt between runs
    queue.json         the current apply order
    work/<date>/       batch files, tailor and apply result files

Recruiter outreach is a separate pipeline: the recruiter-outreach skill (sibling folder).
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
import math
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

SWEEP = Path(__file__).resolve().parents[2] / "job-source-sweep" / "scripts"
sys.path.insert(0, str(SWEEP))
import jobsweep as js  # noqa: E402
import enrich  # noqa: E402
import userconfig  # noqa: E402  (config.yaml at the repo root)

NOW = datetime.now(timezone.utc)
TODAY = NOW.strftime("%Y-%m-%d")

# ------------------------------------------------------------------ files

def ap_dir(home: str) -> Path:
    path = js.fix_path(home) / "autopilot"
    path.mkdir(parents=True, exist_ok=True)
    return path


def work_dir(home: str) -> Path:
    path = ap_dir(home) / "work" / TODAY
    path.mkdir(parents=True, exist_ok=True)
    return path


def load(home: str, name: str, default):
    return enrich.read_json(ap_dir(home) / name, default)


def save(home: str, name: str, data) -> None:
    enrich.write_json(ap_dir(home) / name, data, indent=1)


def emit(obj) -> int:
    print(json.dumps(obj, indent=1, ensure_ascii=False))
    return 0


DEFAULT_SETTINGS = {
    "channels": ["ats", "linkedin_easy_apply", "email"],  # never public comments
    "max_minutes": 120,       # wall-clock cap for the apply loop
    "max_load_retries": 3,    # type-3 failures before a role is given up
    "tailor_ahead": 6,        # tailored CVs kept ready ahead of the apply loop
    "tailor": True,           # False: skip /tailor-resume and upload config resume.pdf as-is
    "per_company_per_run": 2,
    # filled forms (tabs left open) waiting for the user; the apply loop stops at this many
    "max_ready": 15,
    # False: fill and stop in front of Submit (the user submits and confirms). True: the apply
    # subagent clicks Submit itself and records `applied` with the confirmation it saw.
    "auto_submit": False,
    # cover letters: "required" = draft one only when the form requires it or the posting asks for
    # one; "always" = draft one wherever a form accepts it; "never"
    "cover_letter": "required",
    # filled in from config.yaml by settings(): banned_companies (current employers/clients, never
    # applied to), pay_target_usd_month / pay_skip_below_usd_month (pay section), and the tracker
    # section (applika_enabled, rejection_cooldown_days, rejection_neutral_feedback,
    # rejection_ignore_companies)
}


def squash(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def is_banned(company: str, st: dict) -> bool:
    """Cheap pre-filter only; the scoring/apply/outreach subagents also judge variants (e.g. a
    recruiter post hiring for a banned company)."""
    c = squash(company)
    if len(c) < 3:
        return False
    return any(c.startswith(squash(b)) for b in st["banned_companies"] if squash(b))


def settings(home: str) -> dict:
    """DEFAULT_SETTINGS under config.yaml's autopilot, pay, tracker and banned_companies sections."""
    cfg = userconfig.load()
    s = dict(DEFAULT_SETTINGS)
    s.update(cfg.get("autopilot") or {})
    p, t = cfg.get("pay") or {}, cfg.get("tracker") or {}
    s.update(banned_companies=userconfig.banned_companies(),
             pay_target_usd_month=p.get("target_usd_month", 10000), pay_skip_below_usd_month=p.get("skip_below_usd_month", 0),
             applika_enabled=t.get("enabled", True), rejection_cooldown_days=t.get("rejection_cooldown_days", 0),
             rejection_neutral_feedback=t.get("rejection_neutral_feedback") or [],
             rejection_ignore_companies=t.get("rejection_ignore_companies") or [])
    return s


# --------------------------------------------------------------- answer bank

def norm_q(text: str) -> str:
    text = re.sub(r"\b([a-z])\.(?=[a-z]\.)", r"\1", (text or "").lower())  # "u.s." -> "us."
    text = re.sub(r"[*:?()\[\].,;!\"'/]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def qid_of(text: str) -> str:
    return hashlib.sha1(norm_q(text).encode()).hexdigest()[:10]


def skill_slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def empty_bank() -> dict:
    return {"profile": {}, "questions": {}, "skills": {"confirmed": [], "denied": [], "pending": {}}}


def bank(home: str) -> dict:
    b = load(home, "answer-bank.json", None) or empty_bank()
    for k, v in empty_bank().items():
        b.setdefault(k, v)
    for k, v in empty_bank()["skills"].items():
        b["skills"].setdefault(k, v)
    b["profile"] = userconfig.profile()  # always from config.yaml; the file's copy is only a mirror
    return b


def ask(b: dict, q: dict, role_id: str) -> str:
    """Queue a form question for the user; returns its id."""
    qid = qid_of(q["label"])
    e = b["questions"].setdefault(qid, {"question": q["label"].strip(), "answer": None, "asked": TODAY, "roles": []})
    e["type"] = q.get("type") or e.get("type", "text")
    if q.get("options"):
        e["options"] = q["options"]
    if role_id not in e["roles"]:
        e["roles"].append(role_id)
    return qid


# ------------------------------------------------------------------ scoring

LOGISTICS = {1: 15, 2: 10, 3: 0}
PART_MAX = {"core": 35, "requirements": 25, "seniority": 15, "domain": 10}


def pay_bonus(parts: dict, target: float) -> int:
    """+10 when even the bottom of the posted range reaches the target, +6 when its top does,
    -10 when the top is below it, 0 when no pay is posted."""
    lo, hi = parts.get("pay_min"), parts.get("pay_max")
    if not hi and not lo:
        return 0
    if lo and lo >= target:
        return 10
    return 6 if (hi or lo) >= target else -10


def fresh_bonus(parts: dict) -> int:
    """New postings get far more responses: +5 up to 3 days old, +2 up to a week, 0 otherwise."""
    parsed, _ = js.parse_date(parts.get("posted") or "")
    if not parsed:
        return 0
    days = (NOW - parsed.astimezone(timezone.utc)).total_seconds() / 86400
    return 5 if days <= 3 else 2 if days <= 7 else 0


def total_score(parts: dict | None, elig_cat, target: float = 10000) -> int | None:
    if not parts:
        return None
    s = sum(max(0, min(PART_MAX[k], float(parts.get(k, 0)))) for k in PART_MAX)
    s += LOGISTICS.get(elig_cat, 7)
    s *= 0.6 ** min(len(parts.get("blockers") or []), 2)
    if parts.get("confidence") == "title_only":
        s = min(s, 60)
    s += pay_bonus(parts, target) + fresh_bonus(parts)
    return round(max(0, min(100, s)))


def apply_decision(parts: dict, st: dict) -> tuple[bool, str, str]:
    """The model's liberal apply call, plus the user's pay floor on posted pay."""
    if parts.get("apply") is False:
        return False, parts.get("skip_reason", ""), parts.get("skip_evidence", "")
    hi = parts.get("pay_max") or parts.get("pay_min")
    if hi and st.get("pay_skip_below_usd_month") and hi < st["pay_skip_below_usd_month"]:
        return False, "pay_below_target", (f'Posted pay: {parts["pay_note"]} (~USD {round(hi):,}/month)' if parts.get("pay_note")
                                           else f"Posted pay tops out at ~USD {round(hi):,}/month")
    return True, "", ""


def load_decisions(path: str | None) -> dict[str, str]:
    """Explorer decisions exported with ArtifactData list (out_dir) - {role id: accepted|rejected}."""
    if not path:
        return {}
    p = js.fix_path(path)
    files = [p] if p.is_file() else list(p.rglob("*.json"))
    out = {}
    for f in files:
        data = json.loads(f.read_text(encoding="utf-8"))
        docs = data if isinstance(data, list) else [data]
        for d in docs:
            rid = (d.get("role") or {}).get("id") or d.get("id") or f.stem
            if d.get("status") in ("accepted", "rejected"):
                out[rid] = d["status"]
    return out


def all_roles(home: str, batches: int) -> list[dict]:
    return enrich.collect_roles(home, None, enrich.run_dates(home, batches))


def cmd_score_queue(args) -> int:
    home = js.fix_path(args.home)
    roles = all_roles(args.home, args.batches)
    cache = enrich.ingest_outputs(home, enrich.explore_dir(args.home))
    index = enrich.read_json(home / "jds" / "index.json", {})
    scores = load(args.home, "scores.json", {})
    rubric = enrich.current_rubric_sha(home)
    if not rubric:
        sys.exit("no fit-rubric.md for the current resume - run job-source-sweep step 8c first")
    rejected = {k for k, v in load_decisions(args.decisions).items() if v == "rejected"}
    ledger = load(args.home, "ledger.json", {})
    st = settings(args.home)
    existing, applika_err = applika_list(args.home)
    rej = recent_rejections(existing, st)
    rejected_company = 0
    entries = []
    for r in roles:
        e = ledger.get(r["id"], {})
        if r["id"] in rejected or e.get("status") in FINAL:
            continue
        rj = rejection_for(rej, r["company"])
        if rj and e.get("override") != "apply":
            ledger[r["id"]] = {**e, "id": r["id"], "key": r["key"], "title": r["title"], "company": r["company"],
                               "url": r["url"], "location": r["location"], "attempts": e.get("attempts", []),
                               "status": "skipped", "detail": rejection_detail(rj)}
            rejected_company += 1
            continue
        if scores.get(r["key"], {}).get("rubric") == rubric:
            continue
        jd = index.get(r["key"], {})
        el = cache.get(r["key"], {}).get("elig") or {}
        entries.append({"id": r["id"], "key": r["key"], "title": r["title"], "company": r["company"],
                        "location": r["location"],
                        "file": enrich.host_path(home / "jds" / jd["file"]) if jd.get("status") == "ok" else "",
                        "eligibility": enrich.ELIG_LABEL.get(el.get("category"), "unclassified"),
                        "eligibility_evidence": el.get("evidence", ""),
                        "posted": r["posted_iso"], "listed_pay": r["compensation"]})
    out = work_dir(args.home)
    b = bank(args.home)
    ctx = {"rubric": enrich.host_path(home / "fit-rubric.md"),
           "confirmed_skills": b["skills"]["confirmed"], "denied_skills": b["skills"]["denied"],
           "languages": b["profile"].get("languages", ""), "candidate": userconfig.snapshot(),
           "banned_companies": settings(args.home)["banned_companies"],
           "pay_target_usd_month": settings(args.home)["pay_target_usd_month"]}
    enrich.write_json(out / "score_context.json", ctx)
    if rejected_company:
        save(args.home, "ledger.json", ledger)
    return emit({"pending": len(entries), "skipped_rejected_company": rejected_company,
                 "rejection_cooldown_days": st["rejection_cooldown_days"], "applika_error": applika_err,
                 "context": enrich.host_path(out / "score_context.json"),
                 "batches": enrich.write_batches(out, "score", entries, args.batch_size)})


def num(v) -> float | None:
    try:
        return float(v) if v not in (None, "", 0) else None
    except (TypeError, ValueError):
        return None


def cmd_score_ingest(args) -> int:
    home = js.fix_path(args.home)
    out = work_dir(args.home)
    rubric = enrich.current_rubric_sha(home)
    keys, posted = {}, {}
    for bf in out.glob("score_batch_*.json"):
        for e in json.loads(bf.read_text(encoding="utf-8")):
            keys[e["id"]] = e["key"]
            posted[e["id"]] = e.get("posted", "")
    scores = load(args.home, "scores.json", {})
    n = 0
    for f in sorted(out.glob("score_out_*.jsonl")):
        for d in enrich.read_jsonl(f):
            key = keys.get(str(d.get("id")))
            if not key:
                continue
            scores[key] = {"id": str(d["id"]), "rubric": rubric, "date": TODAY,
                           **{k: d.get(k, 0) for k in PART_MAX},
                           "blockers": d.get("blockers") or [], "confidence": d.get("confidence", "jd"),
                           "why": d.get("why", ""), "level": d.get("level", ""), "apply": d.get("apply", True) is not False,
                           "skip_reason": d.get("skip_reason", ""), "skip_evidence": d.get("skip_evidence", ""),
                           "pay_min": num(d.get("pay_min_usd_month")), "pay_max": num(d.get("pay_max_usd_month")),
                           "pay_note": d.get("pay_note", ""), "posted": posted.get(str(d["id"]), "")}
            n += 1
    save(args.home, "scores.json", scores)
    return emit({"ingested": n, "scored_total": len(scores)})


# --------------------------------------------------------------------- queue

FINAL = {"applied", "skipped", "awaiting_user", "discarded"}


def is_ready(e: dict) -> bool:
    """A form left open in Chrome for the user to review and submit: filled completely, or filled
    as far as the answers go (failed_fill) by the no-submit apply flow. Older failed_* entries from
    before that flow have no filled_at and are plain failures."""
    return e.get("status") == "filled" or (e.get("status") == "failed_fill" and bool(e.get("filled_at")))


def role_scores(home: str, roles: list[dict]) -> dict[str, int | None]:
    cache = enrich.read_json(js.fix_path(home) / "classifications.json", {})
    scores = load(home, "scores.json", {})
    target = settings(home)["pay_target_usd_month"]
    return {r["id"]: total_score(scores.get(r["key"]), (cache.get(r["key"], {}).get("elig") or {}).get("category"), target)
            for r in roles}


def blocked_on_questions(entry: dict, b: dict) -> bool:
    pend = entry.get("waiting_on") or []
    return any(b["questions"].get(q, {}).get("answer") in (None, "") for q in pend if not q.startswith("skill:")) or \
        any(s[6:] in b["skills"]["pending"] for s in pend if s.startswith("skill:"))


def applika_list(home: str, refresh: bool = False) -> tuple[list | None, str]:
    """Existing applika applications, cached for the day. Returns (list or None, error output)."""
    cache = work_dir(home) / "applika_list.json"
    if cache.exists() and not refresh:
        return json.loads(cache.read_text(encoding="utf-8")), ""
    res = run_applika(["applications", "list", "--output-format", "json"])
    try:
        existing = json.loads(res.stdout)
    except json.JSONDecodeError:
        return None, (res.stdout + res.stderr)[-500:]
    cache.write_text(json.dumps(existing), encoding="utf-8")
    return existing, ""


def recent_rejections(existing: list | None, st: dict) -> dict[str, dict]:
    """Companies that rejected the user within the cooldown, by company key. A rejection is an
    applika application finalized with the strict step Denied (any feedback except the neutral
    ones, e.g. "Role closed"), dated by its feedback date, else its application date."""
    days = int(st.get("rejection_cooldown_days") or 0)
    if days <= 0:
        return {}
    cutoff = (NOW - timedelta(days=days)).strftime("%Y-%m-%d")
    neutral = {(n or "").strip().lower() for n in st.get("rejection_neutral_feedback") or []}
    ignore = {js.company_key(n) for n in st.get("rejection_ignore_companies") or []}
    out: dict[str, dict] = {}
    for x in existing or []:
        if not x.get("finalized"):
            continue
        step = ((x.get("last_step") or {}).get("name") or "").strip().lower()
        fb = x.get("feedback") or {}
        fb_name = (fb.get("name") or "").strip()
        if step != "denied" or fb_name.lower() in neutral or fb_name.lower() == "accepted":
            continue
        # A ghosted application is dated by when it was sent: the finalization in applika often
        # happens much later, in bulk. An explicit rejection is dated by its feedback date.
        if fb_name.lower() == "ghost":
            date = x.get("application_date") or fb.get("date") or ""
        else:
            date = fb.get("date") or x.get("application_date") or ""
        if date < cutoff:
            continue
        key = js.company_key(x.get("company_name", ""))
        if not key or key in ignore:
            continue
        cur = out.get(key)
        if not cur or date > cur["date"]:
            rec = {"company": x.get("company_name"), "role": x.get("role"), "date": date,
                   "feedback": fb_name, "applika_id": x.get("id")}
            out[key] = rec
            # "RYZ Labs" in applika vs "RyzLabs" on a board: company_key strips "Labs" from the
            # first but not the second, so also index the squashed raw name and squashed key
            for alt in (squash(x.get("company_name", "")), squash(key)):
                if len(alt) >= 4:
                    out["~" + alt] = rec
    return out


def rejection_for(rej: dict[str, dict], company: str) -> dict | None:
    if not rej:
        return None
    key = js.company_key(company or "")
    if key in rej:
        return rej[key]
    for alt in (squash(company or ""), squash(key)):
        if len(alt) >= 4 and "~" + alt in rej:
            return rej["~" + alt]
    return None


def rejection_detail(r: dict) -> str:
    fb = f" ({r['feedback']})" if r.get("feedback") else ""
    return f"Rejected by {r.get('company')} on {r.get('date')} for {r.get('role')}{fb}"


def applika_exact(existing: list | None, e: dict):
    """Cheap exact pre-filter (same job URL, or same company and title). Near-duplicates are left
    to the apply subagent, which sees the company's earlier applications and judges."""
    for x in existing or []:
        if x.get("link_to_job") and js._norm_url(x["link_to_job"]) in {js._norm_url(u) for u in (e.get("url"), e.get("apply_url")) if u}:
            return x
        if js.company_key(x.get("company_name", "")) == js.company_key(e.get("company", "")) and \
                (x.get("role") or "").strip().lower() == (e.get("title") or "").strip().lower():
            return x
    return None


BALANCE_GAP = 25  # points; see balance_queue


def source_group(labels: list[str]) -> str:
    """The source a role is credited to for queue balancing: its first source label, with every VC
    portfolio network folded into one group so twelve small boards don't outvote one big one."""
    label = (labels or ["unknown"])[0]
    low = label.lower()
    if "portfolio" in low:
        return "VC portfolio boards"
    if "linkedin" in low:
        return "LinkedIn Jobs" if "job" in low else "LinkedIn posts"
    if "rocketship" in low:
        return "Remote Rocketship"
    if low.startswith("wellfound"):
        return "Wellfound"
    # Google site: shards label rows by host or shard name; fold them into two groups.
    if low.startswith("ats") or re.search(r"workday|ashby|lever|greenhouse|workable|smartrecruiters|icims|oracle|taleo|"
                                            r"breezy|recruitee|jobvite|pinpoint|teamtailor|personio|rippling|bamboo|gem|"
                                            r"successfactors", low):
        return "ATS pages via Google"
    if low.startswith("google") or "/" in low or re.search(r"dailyremote|remote\.com|web3|arc\.dev|cryptocareers", low):
        return "Job boards via Google"
    return label


def balance_queue(q: list[dict]) -> list[dict]:
    """Deal the queue out round-robin across sources, each source best score first, accepted roles
    first within a source and unscored roles last. A score-only sort fills the run with whichever
    feed is largest; this keeps every source in play from the first application on."""
    groups: dict[str, list[dict]] = {}
    for x in q:
        groups.setdefault(x["source"], []).append(x)
    for g in groups.values():
        g.sort(key=lambda x: (x["score"] is None, not x["accepted"], -(x["score"] or 0)))
    order = sorted(groups, key=lambda k: -(groups[k][0]["score"] or 0))  # the strongest source leads each round
    out = []
    while any(groups.values()):
        best = max((g[0]["score"] or 0) for g in groups.values() if g)
        took = False
        for k in order:
            # A source only takes its turn while its best remaining role is within BALANCE_GAP points of
            # the best role left anywhere, so balance never pushes a weak role above a much stronger one.
            if groups[k] and (groups[k][0]["score"] or 0) >= best - BALANCE_GAP:
                out.append(groups[k].pop(0))
                took = True
        if not took:  # only possible with unscored roles; drain them in source order
            for k in order:
                if groups[k]:
                    out.append(groups[k].pop(0))
                    break
    return out


def cmd_queue(args) -> int:
    roles = all_roles(args.home, args.batches)
    sc = role_scores(args.home, roles)
    parts = load(args.home, "scores.json", {})
    existing, applika_err = applika_list(args.home, refresh=True)
    dec = load_decisions(args.decisions)
    ledger = load(args.home, "ledger.json", {})
    b = bank(args.home)
    st = settings(args.home)
    rej = recent_rejections(existing, st)
    q, skipped = [], {"rejected": 0, "final": 0, "banned": 0, "rejected_company": 0, "in_applika": 0,
                      "not_worth_applying": 0, "needs_you": 0, "waiting_answers": 0, "load_retries": 0}
    for r in roles:
        e = ledger.get(r["id"], {})
        status = e.get("status")
        base = {"id": r["id"], "key": r["key"], "title": r["title"], "company": r["company"], "url": r["url"],
                "location": r["location"], "score": sc.get(r["id"]), "attempts": e.get("attempts", [])}
        if is_banned(r["company"], st) and status != "applied":
            ledger[r["id"]] = {**e, **base, "status": "skipped", "detail": "Banned company (a current employer)"}
            skipped["banned"] += 1
            continue
        rj = rejection_for(rej, r["company"])
        if rj and status != "applied" and e.get("override") != "apply":
            ledger[r["id"]] = {**e, **base, "status": "skipped", "detail": rejection_detail(rj)}
            skipped["rejected_company"] += 1
            continue
        if status not in FINAL:
            m = applika_exact(existing, {**r, **e})
            if m:  # applied before, outside this skill
                ledger[r["id"]] = {**e, **base, "status": "applied", "applied_at": m.get("application_date"),
                                   "applika": {"id": m.get("id"), "note": "already in applika"},
                                   "detail": "Already in applika"}
                skipped["in_applika"] += 1
                continue
            sp = parts.get(r["key"]) or {}
            if e.get("override") == "skip":  # the user overrode the model on the dashboard
                ledger[r["id"]] = {**e, **base, "status": "skipped", "detail": "You chose not to apply"}
                skipped["final"] += 1
                continue
            ok, why, evidence = apply_decision(sp, st)
            if not ok and e.get("override") != "apply":
                ledger[r["id"]] = {**e, **base, "status": "not_applying", "detail": f"{why}: {evidence}".strip(": ")}
                skipped["not_worth_applying"] += 1
                continue
        if dec.get(r["id"]) == "rejected":
            skipped["rejected"] += 1
            continue
        if status in FINAL:
            skipped["final"] += 1
            continue
        if status in ("filled", "failed_submit"):
            skipped["needs_you"] += 1  # waiting for the user to submit (or discard) it
            continue
        if status == "failed_fill" and blocked_on_questions(e, b):
            # The form is filled as far as it goes and its tab is open. The user either finishes it
            # by hand (then marks it submitted) or answers the questions on the dashboard, after
            # which the role is queued again and filled once more on the next run.
            skipped["waiting_answers"] += 1
            continue
        if status == "failed_load" and e.get("load_failures", 0) >= st["max_load_retries"]:
            skipped["load_retries"] += 1
            continue
        q.append({"id": r["id"], "key": r["key"], "title": r["title"], "company": r["company"], "url": r["url"],
                  "location": r["location"], "score": sc.get(r["id"]), "accepted": dec.get(r["id"]) == "accepted",
                  "source": source_group(r.get("sources"))})
    q = balance_queue(q)
    save(args.home, "queue.json", q)
    for r in q:  # keep the ledger's copy of each queued role current, so the dashboard can list it
        e = ledger.setdefault(r["id"], {"id": r["id"], "status": "queued", "attempts": []})
        e.update({k: r[k] for k in ("key", "title", "company", "url", "location", "score", "source")})
        if e.get("status") in (None, "failed_fill", "not_applying"):  # answers arrived / decision changed
            e["status"] = "queued"
    save(args.home, "ledger.json", ledger)
    by_source = Counter(x["source"] for x in q)
    return emit({"queued": len(q), "unscored": sum(1 for x in q if x["score"] is None), "skipped": skipped,
                 "by_source": dict(by_source.most_common()),
                 "rejected_companies": sorted({v["company"] for v in rej.values()}),
                 "applika_error": applika_err,
                 "top": [f'{x["score"]} {x["title"]} @ {x["company"]} [{x["source"]}]' for x in q[:12]]})


def slug(text: str, n: int) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "x").lower()).strip("-")[:n].strip("-") or "x"


def cmd_tailor_queue(args) -> int:
    home = js.fix_path(args.home)
    if not settings(args.home).get("tailor", True):
        return emit({"jobs": [], "tailor": False, "note": "autopilot.tailor is false: resume.pdf is uploaded as-is"})
    config = js.load_config(args.home)
    base = js.fix_path(config.get("resume") or "")
    if not base.exists():
        sys.exit("config.resume is missing - set it with jobsweep config --set")
    index = enrich.read_json(home / "jds" / "index.json", {})
    ledger = load(args.home, "ledger.json", {})
    b = bank(args.home)
    out = work_dir(args.home)
    rc = userconfig.load().get("resume") or {}
    tailored_root = js.fix_path(rc["tailored_dir"]) if rc.get("tailored_dir") else base.parent / "tailored"
    prefix = rc.get("file_prefix") or base.stem
    others = sorted({str(Path(x["tailored"]["yaml_wsl"]).parent) for x in ledger.values()
                     if (x.get("tailored") or {}).get("yaml_wsl")})
    jobs = []
    for r in load(args.home, "queue.json", []):
        if len(jobs) >= args.n:
            break
        e = ledger.setdefault(r["id"], {"id": r["id"], "status": "queued", "attempts": []})
        t = e.get("tailored") or {}
        if t.get("pdf") and Path(t.get("pdf_wsl", "")).exists():
            continue
        started = e.get("tailor_started", "")
        if started and started > (NOW - timedelta(hours=1)).isoformat():
            continue
        folder = tailored_root / f"{slug(r['company'], 25)}-{slug(r['title'], 40)}"
        marker = folder / ".role_id"
        if folder.exists() and (not marker.exists() or marker.read_text().strip() != r["id"]):
            folder = folder.with_name(folder.name + "-" + r["id"][:6])
        folder.mkdir(parents=True, exist_ok=True)
        (folder / ".role_id").write_text(r["id"])
        jd = index.get(r["key"], {})
        jobs.append({"id": r["id"], "title": r["title"], "company": r["company"], "url": r["url"],
                     "jd_file": enrich.host_path(home / "jds" / jd["file"]) if jd.get("status") == "ok" else "",
                     "base_resume": enrich.host_path(base), "folder": enrich.host_path(folder),
                     "file_name": f"{prefix}-{folder.name}.yaml",
                     "result": enrich.host_path(out / f"tailor_result_{r['id']}.json")})
        e["tailor_started"] = NOW.isoformat()
    save(args.home, "ledger.json", ledger)
    return emit({"jobs": jobs, "answer_bank": enrich.host_path(ap_dir(args.home) / "answer-bank.json"),
                 "confirmed_skills": b["skills"]["confirmed"], "denied_skills": b["skills"]["denied"],
                 "previous_tailored": [enrich.host_path(Path(o)) for o in others]})


# -------------------------------------------------------------------- pacing

ATS = re.compile(r"greenhouse|lever\.co|ashbyhq|workable|smartrecruiters|myworkdayjobs|workday|icims|jobvite|teamtailor|"
                 r"recruitee|breezy|bamboohr|personio|rippling|pinpoint|oraclecloud|taleo|successfactors|gem\.com|"
                 r"jazzhr|comeet|homerun|amplifyit|careers?\.")
# class -> (starting budget per run, ceiling, minimum seconds between submissions)
CLASSES = {"linkedin": (8, 25, 240), "email": (8, 20, 120), "ats": (6, 20, 60), "other": (3, 10, 90)}


def host_of(value: str) -> str:
    value = (value or "").strip().lower()
    if value in ("email", "gmail") or "@" in value:
        return "email"
    host = urlparse(value if "//" in value else "https://" + value).hostname or value
    host = re.sub(r"^(www|jobs|job-boards|boards|apply|careers)\.", "", host)
    parts = host.split(".")
    # one budget per ATS vendor, not per tenant subdomain (acme.greenhouse.io -> greenhouse.io)
    return ".".join(parts[-2:]) if len(parts) > 2 else host


def host_class(host: str) -> str:
    if host == "email":
        return "email"
    if "linkedin" in host:
        return "linkedin"
    return "ats" if ATS.search(host) else "other"


def pacing(home: str) -> dict:
    p = load(home, "pacing.json", {})
    p.setdefault("hosts", {})
    p.setdefault("run", {})
    p.setdefault("companies", {})
    return p


def host_state(p: dict, host: str) -> dict:
    cls = host_class(host)
    start, _, _ = CLASSES[cls]
    return p["hosts"].setdefault(host, {"class": cls, "budget": start, "used": 0, "friction": 0, "last_submit": ""})


def cmd_pace_start(args) -> int:
    p = pacing(args.home)
    changes = {}
    for host, h in p["hosts"].items():
        _, ceiling, _ = CLASSES[h.get("class") or host_class(host)]
        old = h["budget"]
        if h.get("friction"):
            h["budget"] = max(1, old // 2)
        elif h.get("used", 0) >= old:
            h["budget"] = min(ceiling, math.ceil(old * 1.5))
        h["used"], h["friction"] = 0, 0
        if h["budget"] != old:
            changes[host] = f"{old}->{h['budget']}"
    p["run"] = {"id": NOW.strftime("%Y%m%dT%H%M"), "started": NOW.isoformat()}
    p["companies"] = {}
    save(args.home, "pacing.json", p)
    return emit({"run": p["run"]["id"], "budget_changes": changes,
                 "budgets": {h: v["budget"] for h, v in p["hosts"].items()}})


def check(p: dict, host: str, company: str, st: dict) -> dict:
    h = host_state(p, host)
    if h["friction"]:
        return {"allowed": False, "reason": f"{host} pushed back earlier in this run (captcha or block)"}
    if h["used"] >= h["budget"]:
        return {"allowed": False, "reason": f"{host} budget of {h['budget']} used for this run"}
    if company and p["companies"].get(js.company_key(company), 0) >= st["per_company_per_run"]:
        return {"allowed": False, "reason": f"already {st['per_company_per_run']} applications to {company} this run"}
    gap = CLASSES[h["class"]][2]
    if h["last_submit"]:
        wait = gap - (NOW - datetime.fromisoformat(h["last_submit"])).total_seconds()
        if wait > 0:
            return {"allowed": False, "reason": "minimum gap", "wait_seconds": math.ceil(wait)}
    return {"allowed": True, "remaining": h["budget"] - h["used"]}


def cmd_pace_check(args) -> int:
    p = pacing(args.home)
    res = check(p, host_of(args.host), args.company or "", settings(args.home))
    save(args.home, "pacing.json", p)
    return emit({"host": host_of(args.host), **res})


def cmd_next(args) -> int:
    """The best queued role that is tailored and whose known host allows a submission now."""
    p = pacing(args.home)
    st = settings(args.home)
    ledger = load(args.home, "ledger.json", {})
    started = p["run"].get("started")
    if started and (NOW - datetime.fromisoformat(started)).total_seconds() > st["max_minutes"] * 60:
        return emit({"done": True, "reason": f"time cap of {st['max_minutes']} min reached"})
    ready = sum(1 for e in ledger.values() if is_ready(e))
    if ready >= st["max_ready"]:
        return emit({"done": True, "ready": ready,
                     "reason": f"{ready} filled forms are waiting for you to submit (max_ready {st['max_ready']}); "
                               "submit or discard some, then run the apply loop again"})
    waiting, untailored = [], 0
    tailor = bool(st.get("tailor", True))
    base_pdf = (userconfig.load().get("resume") or {}).get("pdf") or ""
    if not tailor and not base_pdf:
        return emit({"done": True, "reason": "autopilot.tailor is false but resume.pdf is not set in config.yaml"})
    index = {} if tailor else enrich.read_json(js.fix_path(args.home) / "jds" / "index.json", {})
    for r in load(args.home, "queue.json", []):
        e = ledger.get(r["id"], {})
        if e.get("status") not in ("queued", "failed_load", "deferred", None):
            continue
        # claimed by a fill subagent that is still running (parallel fills call `next` repeatedly)
        if e.get("filling_since") and e["filling_since"] > (NOW - timedelta(minutes=30)).isoformat():
            continue
        t = e.get("tailored") or {}
        if not tailor:   # no tailoring: the base PDF goes up as-is, the JD comes from the sweep's cache
            jd = index.get(r.get("key"), {})
            t = {"pdf": base_pdf, "summary": "",
                 "jd_file": enrich.host_path(js.fix_path(args.home) / "jds" / jd["file"]) if jd.get("status") == "ok" else ""}
        elif t.get("status") == "no_jd":
            continue
        elif not t.get("pdf"):
            untailored += 1
            continue
        # The listing URL is often an aggregator; only a known ATS/LinkedIn host is paced here.
        # Aggregator listings are paced by the apply subagent's pace-check once it finds the real form.
        host = e.get("apply_host") or host_of(r["url"])
        if host_class(host) not in ("ats", "linkedin") and not e.get("apply_host"):
            host = ""
        res = check(p, host, r["company"], st) if host else {"allowed": True}
        if not res["allowed"]:
            if res.get("wait_seconds"):
                waiting.append(res["wait_seconds"])
            continue
        existing, _ = applika_list(args.home)
        earlier = [{**{k: x.get(k) for k in ("role", "application_date", "link_to_job")},
                    "outcome": ((x.get("last_step") or {}).get("name") or ("open" if not x.get("finalized") else "finalized")),
                    "feedback": (x.get("feedback") or {}).get("name"), "outcome_date": (x.get("feedback") or {}).get("date")}
                   for x in existing or [] if js.company_key(x.get("company_name", "")) == js.company_key(r["company"])]
        rj = rejection_for(recent_rejections(existing, st), r["company"])
        ledger.setdefault(r["id"], {"id": r["id"], "status": "queued", "attempts": []})["filling_since"] = NOW.isoformat()
        save(args.home, "ledger.json", ledger)
        return emit({"banned_companies": st["banned_companies"], "cover_letter_policy": st["cover_letter"],
                     "auto_submit": bool(st.get("auto_submit")), "candidate": userconfig.profile(),
                     "role": {**r, "tailored_pdf": t["pdf"], "tailored_summary": t.get("summary", ""),
                              "tailored_folder": str(Path(t["pdf"]).parent.parent) if t.get("pdf") and tailor else "",
                              "jd_file": t.get("jd_file", ""),
                              "earlier_applications_to_company": earlier,
                              "recent_rejection": rejection_detail(rj) if rj else "",
                              "previous_attempt": {k: e.get(k) for k in ("status", "apply_url", "detail", "answers_given")}
                              if e.get("attempts") else None},
                     "host_guess": host, "untailored_in_queue": untailored, "ready_waiting": ready})
    if untailored:
        return emit({"need_tailor": True, "untailored_in_queue": untailored,
                     "wait_seconds": min(waiting) if waiting else 0})
    if waiting:
        return emit({"wait_seconds": min(waiting)})
    return emit({"done": True, "reason": "queue empty or every host's budget is used"})


# -------------------------------------------------------------------- record

# Subagents write result files by hand, and Windows paths with single backslashes are a recurring
# problem: "\\x" with an invalid escape breaks json.loads, and "\\r", "\\t", "\\n", "\\b", "\\f" decode
# into control characters inside paths. load_result repairs both, and tolerates a BOM.
_BAD_ESCAPE = re.compile(r'\\(?!["\\/bfnrtu])')
_CTRL = {"\r": "\\r", "\t": "\\t", "\n": "\\n", "\b": "\\b", "\f": "\\f"}
_PATHISH = re.compile(r"^[A-Za-z]:[\\/]|^\\\\|/mnt/[a-z]/")


def _fix_paths(obj):
    if isinstance(obj, dict):
        return {k: _fix_paths(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_fix_paths(v) for v in obj]
    if isinstance(obj, str) and _PATHISH.match(obj) and any(c in obj for c in _CTRL):
        for c, rep in _CTRL.items():
            obj = obj.replace(c, rep)
    return obj


def load_result(path) -> dict:
    raw = js.fix_path(path).read_text(encoding="utf-8-sig")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = json.loads(_BAD_ESCAPE.sub(r"\\\\", raw))
    return _fix_paths(data)


def cmd_record(args) -> int:
    res = load_result(args.result)
    ledger = load(args.home, "ledger.json", {})
    b = bank(args.home)
    rid = str(res["id"])
    e = ledger.setdefault(rid, {"id": rid, "status": "queued", "attempts": []})
    out = {"id": rid}
    if res.get("kind") == "tailor":
        e.pop("tailor_started", None)
        pend = []
        for s in res.get("skills_pending") or []:
            if s in b["skills"]["confirmed"] or s in b["skills"]["denied"]:
                continue
            ent = b["skills"]["pending"].setdefault(s, {"asked": TODAY, "roles": []})
            if rid not in ent["roles"]:
                ent["roles"].append(rid)
            pend.append(s)
        if res.get("status") == "ok":
            e["tailored"] = {k: res.get(k) for k in ("yaml", "pdf", "summary", "keywords", "skills_added", "jd_file")}
            e["tailored"]["yaml_wsl"] = str(js.fix_path(res["yaml"]))
            e["tailored"]["pdf_wsl"] = str(js.fix_path(res["pdf"]))
            e["tailored"]["status"] = "ok"
        else:
            e["tailored"] = {"status": res.get("status", "failed"), "detail": res.get("detail", "")}
            if res.get("status") == "no_jd":
                e.update(status="failed_load", failure_type=3, detail="JD text could not be loaded for tailoring: "
                         + res.get("detail", ""), load_failures=e.get("load_failures", 0) + 1)
        out.update(tailored=res.get("status"), skills_queued=pend)
    else:
        outcome = res["outcome"]
        host = host_of(res.get("host") or res.get("apply_url") or e.get("url", ""))
        attempt = {"at": NOW.isoformat(), "outcome": outcome, "channel": res.get("channel"), "host": host,
                   "detail": res.get("detail", ""), "apply_url": res.get("apply_url", "")}
        e.setdefault("attempts", []).append(attempt)
        e.pop("filling_since", None)
        e.update(apply_host=host, channel=res.get("channel"), apply_url=res.get("apply_url") or e.get("apply_url"),
                 detail=res.get("detail", ""), updated=NOW.isoformat())
        for k in ("platform", "work_mode", "country"):
            if res.get(k):
                e[k] = res[k]
        p = pacing(args.home)
        h = host_state(p, host)
        if outcome in ("filled", "failed_fill"):
            # The form was filled (completely, or as far as the answers go) and its tab is open for
            # the user. Keep everything the subagent typed, so the user can review it on the dashboard
            # and the form can be re-filled if the tab is lost.
            given = res.get("answers_given") or []
            e.update(answers_given=given, free_text=res.get("free_text") or [], cover_letter=res.get("cover_letter") or None,
                     tab_title=res.get("tab_title", ""), filled_at=NOW.isoformat())
            out["estimates_banked"] = bank_estimates(b, given, rid)
            if outcome == "filled":
                e.update(status="filled", failure_type=None, waiting_on=[])
                h["used"] += 1  # a filled form is this run's unit of work on that host
                h["last_submit"] = NOW.isoformat()
                ck = js.company_key(e.get("company", ""))
                if ck:
                    p["companies"][ck] = p["companies"].get(ck, 0) + 1
            else:
                ids = [ask(b, q, rid) for q in res.get("questions") or []]
                ids += ["skill:" + s for s in res.get("skills_asked") or []]
                for s in res.get("skills_asked") or []:
                    roles = b["skills"]["pending"].setdefault(s, {"asked": TODAY, "roles": []})["roles"]
                    if rid not in roles:
                        roles.append(rid)
                e.update(status="failed_fill", failure_type=2, waiting_on=ids)
                out["questions_queued"] = len(ids)
        elif outcome == "applied":
            # Only produced when config.yaml sets autopilot.auto_submit: true. Needs the confirmation
            # the subagent saw; without one it is recorded as a filled form for the user to check.
            given = res.get("answers_given") or []
            e.update(answers_given=given, free_text=res.get("free_text") or [], cover_letter=res.get("cover_letter") or None,
                     tab_title=res.get("tab_title", ""), filled_at=NOW.isoformat())
            out["estimates_banked"] = bank_estimates(b, given, rid)
            if res.get("confirmation"):
                e.update(status="applied", failure_type=None, applied_at=TODAY, confirmation=res["confirmation"],
                         submitted_by="autopilot", waiting_on=[])
            else:
                e.update(status="filled", failure_type=None, detail="(recorded as filled: no confirmation seen) " + e.get("detail", ""))
            h["used"] += 1
            h["last_submit"] = NOW.isoformat()
            ck = js.company_key(e.get("company", ""))
            if ck:
                p["companies"][ck] = p["companies"].get(ck, 0) + 1
        elif outcome == "failed_submit":
            e.update(status="failed_submit", failure_type=1)
            if res.get("friction", True):
                h["friction"] += 1
        elif outcome == "failed_load":
            e.update(status="failed_load", failure_type=3, load_failures=e.get("load_failures", 0) + 1)
            if res.get("friction", True):
                h["friction"] += 1
        elif outcome == "skipped":
            e.update(status="skipped", failure_type=None)
        elif outcome == "deferred":
            e.update(status="deferred")
        save(args.home, "pacing.json", p)
        out.update(status=e["status"], host=host)
    save(args.home, "ledger.json", ledger)
    save(args.home, "answer-bank.json", b)
    return emit(out)


def bank_estimates(b: dict, given: list[dict], rid: str) -> int:
    """Keep every reusable answer the subagent gave on a form. Estimated answers are stored with
    source "estimated" so the dashboard can show them for confirmation; a question the user has
    already answered is never overwritten. Role-specific free text (why us, cover letters) doesn't
    belong here and stays in the ledger's free_text."""
    n = 0
    for a in given:
        label = (a.get("q") or a.get("label") or "").strip()
        ans = a.get("a") if "a" in a else a.get("answer")
        if not label or ans in (None, "") or not a.get("reusable", True):
            continue
        qid = qid_of(label)
        cur = b["questions"].get(qid)
        if cur and cur.get("source") == "user" and cur.get("answer") not in (None, ""):
            continue
        ent = b["questions"].setdefault(qid, {"question": label, "roles": [], "asked": TODAY})
        ent["type"] = a.get("type") or ent.get("type", "text")
        if a.get("options"):
            ent["options"] = a["options"]
        if rid not in ent["roles"]:
            ent["roles"].append(rid)
        if a.get("estimated"):
            if cur and cur.get("answer") not in (None, "") and not cur.get("source") == "estimated":
                continue
            ent.update(answer=ans, source="estimated", answered_at=TODAY, confirmed=False)
        else:
            ent.update(answer=ans, source=ent.get("source") or "bank", answered_at=ent.get("answered_at") or TODAY)
        n += 1
    return n


def mark_confirmed(ledger: dict, rid: str, status: str, at: str = "") -> dict:
    """The user finished (or dropped) a filled application. Only this sets `applied`."""
    e = ledger.setdefault(rid, {"id": rid, "status": "queued", "attempts": []})
    if status == "submitted":
        e.update(status="applied", failure_type=None, applied_at=(at or TODAY)[:10], waiting_on=[],
                 confirmation="submitted by the user", submitted_by="user", confirmed_at=NOW.isoformat())
    else:
        e.update(status="discarded", failure_type=None, waiting_on=[], confirmed_at=NOW.isoformat(),
                 detail="Discarded by the user" + (f": {e.get('detail')}" if e.get("detail") else ""))
    return e


def cmd_confirm(args) -> int:
    ledger = load(args.home, "ledger.json", {})
    status = "discarded" if args.discarded else "submitted"
    done, unknown = [], []
    for rid in args.id:
        if rid not in ledger:
            unknown.append(rid)
            continue
        e = mark_confirmed(ledger, rid, status)
        done.append(f'{e.get("company")}: {e.get("title")} -> {e["status"]}')
    save(args.home, "ledger.json", ledger)
    return emit({"confirmed": done, "unknown_ids": unknown,
                 "next": "run applika-sync to register the submitted ones" if status == "submitted" and done else ""})


def cmd_ready(args) -> int:
    """Filled forms waiting for the user, best score first, with everything needed to find the tab."""
    ledger = load(args.home, "ledger.json", {})
    rows = [e for e in ledger.values() if is_ready(e)]
    rows.sort(key=lambda e: (-(e.get("score") or 0), e.get("filled_at") or ""))
    return emit({"ready": len(rows), "max_ready": settings(args.home)["max_ready"],
                 "forms": [{"id": e["id"], "title": e.get("title"), "company": e.get("company"), "score": e.get("score"),
                            "status": e.get("status"), "apply_url": e.get("apply_url") or e.get("url"),
                            "tab_title": e.get("tab_title", ""), "filled_at": (e.get("filled_at") or "")[:16],
                            "estimated_answers": sum(1 for a in e.get("answers_given") or [] if a.get("estimated")),
                            "cover_letter": bool(e.get("cover_letter")), "waiting_on": len(e.get("waiting_on") or []),
                            "detail": e.get("detail", "")} for e in rows]})


# ------------------------------------------------------------------- answers

def cmd_answers(args) -> int:
    b = bank(args.home)
    if args.pending:
        return emit({"questions": [{"qid": k, **v} for k, v in b["questions"].items() if v.get("answer") in (None, "")],
                     "skills": b["skills"]["pending"]})
    p = js.fix_path(args.import_)
    files = [p] if p.is_file() else sorted(p.rglob("*.json"))
    got = {"questions": 0, "skills_yes": 0, "skills_no": 0, "overrides": 0, "submitted": 0, "discarded": 0}
    ledger = load(args.home, "ledger.json", {})
    for f in files:
        data = json.loads(f.read_text(encoding="utf-8"))
        for d in (data if isinstance(data, list) else [data]):
            if d.get("status") in ("submitted", "discarded") and d.get("role"):
                # dashboard "Submitted" / "Discard" on a filled form: {role, status, at}
                cur = ledger.get(d["role"], {})
                if cur.get("status") not in ("applied", "discarded") or (cur.get("confirmed_at") or "") < (d.get("at") or ""):
                    mark_confirmed(ledger, d["role"], d["status"], d.get("at", ""))
                    got[d["status"]] += 1
                continue
            if d.get("override") in ("apply", "skip", "model") and d.get("role"):
                # dashboard override of the model's apply decision: {role, override, at}
                e = ledger.setdefault(d["role"], {"id": d["role"], "status": "queued", "attempts": []})
                if d["override"] == "model":
                    e.pop("override", None)
                else:
                    e["override"] = d["override"]
                if e.get("status") in ("skipped", "not_applying") and d["override"] != "skip":
                    e["status"] = "queued"  # re-evaluated by the next queue build
                got["overrides"] += 1
                continue
            ans = d.get("answer")
            if ans in (None, ""):
                continue
            if d.get("skill"):
                name = d["skill"]
                b["skills"]["pending"].pop(name, None)
                yes = str(ans).lower() in ("yes", "true", "y")
                dest, other = ("confirmed", "denied") if yes else ("denied", "confirmed")
                if name not in b["skills"][dest]:
                    b["skills"][dest].append(name)
                if name in b["skills"][other]:
                    b["skills"][other].remove(name)
                got["skills_yes" if yes else "skills_no"] += 1
            elif d.get("qid") or d.get("question"):
                qid = d.get("qid") or qid_of(d["question"])
                e = b["questions"].setdefault(qid, {"question": d.get("question", ""), "roles": [], "asked": TODAY})
                e.update(answer=ans, answered_at=d.get("at") or TODAY, source="user", confirmed=True)
                got["questions"] += 1
    save(args.home, "answer-bank.json", b)
    save(args.home, "ledger.json", ledger)
    return emit(got)


# -------------------------------------------------------------------- applika

def applika_bin() -> str:
    return shutil.which("applika") or str(Path.home() / ".local" / "bin" / "applika")


def run_applika(argv: list[str], timeout: int = 120) -> subprocess.CompletedProcess:
    if not userconfig.load().get("tracker", {}).get("enabled", True):
        return subprocess.CompletedProcess(argv, 0, stdout="[]", stderr="")  # tracker disabled: nothing registered
    return subprocess.run([applika_bin(), *argv], capture_output=True, text=True, timeout=timeout)


def cmd_applika_sync(args) -> int:
    """Register applied roles in applika. Failures are returned verbatim; the caller decides
    whether they mean a login problem (then runs `applika login` and syncs again)."""
    ledger = load(args.home, "ledger.json", {})
    scores = load(args.home, "scores.json", {})
    todo = [e for e in ledger.values() if e.get("status") == "applied" and not e.get("applika")]
    if not settings(args.home)["applika_enabled"]:
        return emit({"registered": 0, "note": "tracker disabled in config.yaml", "unregistered": len(todo)})
    if not todo:
        return emit({"registered": 0, "note": "nothing new to register"})
    existing, err = applika_list(args.home, refresh=True)
    if existing is None:
        return emit({"registered": 0, "list_failed": True, "output": err})
    done, errors = [], []
    for e in todo:
        match = applika_exact(existing, e)
        if match:
            e["applika"] = {"id": match.get("id"), "note": "already in applika"}
            done.append(f'{e.get("company")}: already registered')
            continue
        argv = ["applications", "new", "--company", e.get("company") or "Unknown", "--role", e.get("title") or "Role",
                "--platform", e.get("platform") or ("Email" if e.get("channel") == "email" else "Company website"),
                "--mode", "active", "--date", e.get("applied_at") or TODAY,
                "--job-url", e.get("url") or e.get("apply_url") or "",
                "--work-mode", e.get("work_mode") or "remote",
                "--observation", (f"Auto-applied by job-autopilot via {e.get('channel')} ({e.get('apply_url') or e.get('url')}). "
                                  f"Global fit score {e.get('score')}. Resume: {(e.get('tailored') or {}).get('pdf', '')}")[:900]]
        level = (scores.get(e.get("key") or "") or {}).get("level")
        if level:
            argv += ["--experience-level", level]
        if e.get("country"):
            argv += ["--country", e["country"]]
        if args.dry_run:
            done.append("DRY: applika " + " ".join(argv))
            continue
        res = run_applika(argv)
        if res.returncode and "Unknown platform" in (res.stdout + res.stderr):
            # applika only accepts its own platform list; the user files everything else under Lever.
            argv[argv.index("--platform") + 1] = "Lever"
            res = run_applika(argv)
        if res.returncode:
            errors.append({"company": e.get("company"), "output": (res.stdout + res.stderr)[-400:]})
            continue
        e["applika"] = {"at": NOW.isoformat(), "output": res.stdout.strip()[-300:]}
        done.append(f'{e.get("company")}: registered')
    if not args.dry_run:
        save(args.home, "ledger.json", ledger)
    return emit({"registered": len(done), "done": done, "errors": errors})


# ------------------------------------------------------------------ dashboard

STATUS_LABEL = {"filled": "Ready for you to submit", "failed_fill": "Filled, needs your answers",
                "failed_submit": "Blocked before filling", "failed_load": "Couldn't load",
                "applied": "Submitted", "discarded": "Discarded", "queued": "Queued", "deferred": "Deferred (pacing)",
                "not_applying": "Not applying", "skipped": "Skipped", "awaiting_user": "Waiting for you"}


def cmd_dashboard(args) -> int:
    """Write <home>/autopilot/dashboard.json, which `jobsweep explorer` embeds in the role explorer."""
    home = js.fix_path(args.home)
    ledger = load(args.home, "ledger.json", {})
    scores = load(args.home, "scores.json", {})
    cache = enrich.read_json(home / "classifications.json", {})
    b = bank(args.home)
    st = settings(args.home)
    target = st["pay_target_usd_month"]
    by_id = {}
    for key, sp in scores.items():
        rid_ = sp.get("id") or enrich.rid(key)
        cat = (cache.get(key, {}).get("elig") or {}).get("category")
        ok, why, evidence = apply_decision(sp, st)
        by_id[rid_] = {
            "score": total_score(sp, cat, target),
            "parts": {**{k: sp.get(k, 0) for k in PART_MAX}, "location": LOGISTICS.get(cat, 7),
                      "pay": pay_bonus(sp, target), "fresh": fresh_bonus(sp)},
            "pay": sp.get("pay_note", ""),
            "confidence": sp.get("confidence", "jd"), "level": sp.get("level", ""),
            "why": sp.get("why", ""), "apply": ok, "skipReason": why,
            "skipEvidence": evidence, "blockers": sp.get("blockers") or [],
            "override": (ledger.get(rid_) or {}).get("override", ""),
            "status": (ledger.get(rid_) or {}).get("status", "")}
    apps = []
    for e in ledger.values():
        last = (e.get("attempts") or [{}])[-1]
        apps.append({"id": e["id"], "title": e.get("title", ""), "company": e.get("company", ""), "url": e.get("url", ""),
                     "score": (by_id.get(e["id"]) or {}).get("score", e.get("score")),
                     "status": e.get("status", "queued"), "label": STATUS_LABEL.get(e.get("status", "queued"), e.get("status")),
                     "failureType": e.get("failure_type"), "channel": e.get("channel") or "", "platform": e.get("platform") or "",
                     "applyUrl": e.get("apply_url") or "", "detail": e.get("detail", ""),
                     "at": e.get("applied_at") or (last.get("at") or e.get("updated") or "")[:10],
                     "attempts": len(e.get("attempts") or []), "applika": bool(e.get("applika")),
                     "resume": (e.get("tailored") or {}).get("pdf", ""), "summary": (e.get("tailored") or {}).get("summary", ""),
                     "waitingOn": e.get("waiting_on") or [],
                     # what the subagent typed into the form, for review before the user submits
                     "answers": [{"q": a.get("q") or a.get("label", ""), "a": a.get("a", a.get("answer", "")),
                                  "estimated": bool(a.get("estimated"))} for a in e.get("answers_given") or []],
                     "freeText": e.get("free_text") or [],
                     "coverLetter": (e.get("cover_letter") or {}).get("text", "") if isinstance(e.get("cover_letter"), dict) else "",
                     "coverLetterPdf": (e.get("cover_letter") or {}).get("pdf", "") if isinstance(e.get("cover_letter"), dict) else "",
                     "tabTitle": e.get("tab_title", ""), "filledAt": (e.get("filled_at") or "")[:16].replace("T", " "),
                     "submittedBy": e.get("submitted_by", ""),
                     "ready": is_ready(e)})
    titles = {e["id"]: f'{e.get("title", "")} @ {e.get("company", "")}' for e in ledger.values()}
    questions = [{"qid": k, "question": v["question"], "type": v.get("type", "text"), "options": v.get("options") or [],
                  "roles": [titles.get(r, r) for r in v.get("roles", [])]}
                 for k, v in b["questions"].items() if v.get("answer") in (None, "")]
    # answers the apply subagent estimated from the resume; the user confirms or corrects them once
    estimates = [{"qid": k, "question": v["question"], "type": v.get("type", "text"), "options": v.get("options") or [],
                  "answer": v.get("answer"), "roles": [titles.get(r, r) for r in v.get("roles", [])]}
                 for k, v in b["questions"].items()
                 if v.get("source") == "estimated" and v.get("answer") not in (None, "") and not v.get("confirmed")]
    skills = [{"skill": k, "slug": skill_slug(k), "roles": [titles.get(r, r) for r in v.get("roles", [])]}
              for k, v in b["skills"]["pending"].items()]
    data = {"built": NOW.strftime("%Y-%m-%d %H:%M UTC"), "scores": by_id, "apps": apps,
            "questions": questions, "estimates": estimates, "skills": skills,
            "ready": sum(1 for a in apps if a["ready"]), "max_ready": st["max_ready"]}
    save(args.home, "dashboard.json", data)
    counts: dict[str, int] = {}
    for a in apps:
        counts[a["status"]] = counts.get(a["status"], 0) + 1
    return emit({"scores": len(by_id), "apps": counts, "questions": len(questions), "estimates": len(estimates),
                 "skills": len(skills), "file": enrich.host_path(ap_dir(args.home) / "dashboard.json")})


# --------------------------------------------------------------------- status

def cmd_status(args) -> int:
    ledger = load(args.home, "ledger.json", {})
    b = bank(args.home)
    counts: dict[str, int] = {}
    for e in ledger.values():
        counts[e.get("status", "queued")] = counts.get(e.get("status", "queued"), 0) + 1
    p = pacing(args.home)
    queue = load(args.home, "queue.json", [])
    return emit({"by_status": counts,
                 "queue_by_source": dict(Counter(x.get("source", "unknown") for x in queue).most_common()),
                 "applied_by_source": dict(Counter(e.get("source", "unknown") for e in ledger.values()
                                                   if e.get("status") == "applied").most_common()),
                 "ready_for_you": sum(1 for e in ledger.values() if is_ready(e)),
                 "pending_questions": sum(1 for v in b["questions"].values() if v.get("answer") in (None, "")),
                 "estimated_answers_to_confirm": sum(1 for v in b["questions"].values()
                                                     if v.get("source") == "estimated" and not v.get("confirmed")),
                 "pending_skills": len(b["skills"]["pending"]),
                 "applika_unregistered": sum(1 for e in ledger.values() if e.get("status") == "applied" and not e.get("applika")),
                 "pacing": {h: f'{v["used"]}/{v["budget"]}{" friction" if v["friction"] else ""}' for h, v in p["hosts"].items()}})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--home", default=os.environ.get("JOBSWEEP_HOME") or userconfig.home())
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("score-queue"); p.add_argument("--batches", type=int, default=14)
    p.add_argument("--batch-size", type=int, default=20); p.add_argument("--decisions"); p.set_defaults(fn=cmd_score_queue)
    sub.add_parser("score-ingest").set_defaults(fn=cmd_score_ingest)
    p = sub.add_parser("queue"); p.add_argument("--batches", type=int, default=14); p.add_argument("--decisions")
    p.set_defaults(fn=cmd_queue)
    p = sub.add_parser("tailor-queue"); p.add_argument("--n", type=int, default=6); p.set_defaults(fn=cmd_tailor_queue)
    sub.add_parser("pace-start").set_defaults(fn=cmd_pace_start)
    sub.add_parser("next").set_defaults(fn=cmd_next)
    p = sub.add_parser("pace-check"); p.add_argument("--host", required=True); p.add_argument("--company")
    p.set_defaults(fn=cmd_pace_check)
    p = sub.add_parser("record"); p.add_argument("--result", required=True); p.set_defaults(fn=cmd_record)
    p = sub.add_parser("answers"); g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--import", dest="import_"); g.add_argument("--pending", action="store_true")
    p.set_defaults(fn=cmd_answers)
    p = sub.add_parser("applika-sync"); p.add_argument("--dry-run", action="store_true"); p.set_defaults(fn=cmd_applika_sync)
    p = sub.add_parser("confirm"); p.add_argument("--id", action="append", required=True)
    g = p.add_mutually_exclusive_group(); g.add_argument("--submitted", action="store_true"); g.add_argument("--discarded", action="store_true")
    p.set_defaults(fn=cmd_confirm)
    sub.add_parser("ready").set_defaults(fn=cmd_ready)
    sub.add_parser("dashboard").set_defaults(fn=cmd_dashboard)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
