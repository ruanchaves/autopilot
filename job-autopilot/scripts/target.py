"""Target tiers: which roles are worth the autopilot's first effort.

The user's goal is roles paying at least pay.target_usd_month at companies that really hire outside the US. Two
judgements per role, and a tier built from them (references/target-tier-research.md has the data behind it):

* pay_tier - what the role likely pays, also when the posting states no pay (68% of roles in the 2026-10-14 study):
      posted_target   posted pay reaches the target
      posted_near     posted pay tops out between the floor and the target
      posted_below    posted pay tops out under the floor (the role is skipped, as before)
      likely_target   no posted pay, but the evidence says target pay
      unclear         no posted pay and no evidence either way
      likely_below    no posted pay, but the evidence says below the target
  Evidence, strongest first: pay the company quoted the user (recruiters/pay_ceilings.json, which also takes
  applika's salary ranges), pay the company posts for its other engineering roles (scores.json, boards.json, the
  funded-company registry), the kind of company it is (company_types.json, one judgement per company by the
  company-type subagent), then a recent funding round with a senior title.
* people - the people check (job-source-sweep/scripts/companies.py): engineers working there from the candidate's
  country (brazil), elsewhere in the region (latam), elsewhere outside the US (non_us), none found, or unchecked.

      tier A  pay at target (posted or likely) and engineers in the candidate's country or region
      tier B  one of the two proven, the other unknown (pay unclear, or people unchecked or only non_us)
      tier C  the rest
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

SWEEP = Path(__file__).resolve().parents[2] / "job-source-sweep" / "scripts"
sys.path.insert(0, str(SWEEP))
import jobsweep as js  # noqa: E402
import enrich  # noqa: E402
import companies as co  # noqa: E402
import userconfig  # noqa: E402

NOW = datetime.now(timezone.utc)
TODAY = NOW.strftime("%Y-%m-%d")

PAY_TIERS = ("posted_target", "likely_target", "unclear", "posted_near", "likely_below", "posted_below")
PAY_OK = {"posted_target", "likely_target"}
PAY_BAD = {"posted_near", "likely_below", "posted_below"}
REGION_OK = {"brazil", "latam"}

COMPANY_TYPES = ("funded_startup", "product_company", "big_company", "staff_aug", "consultancy", "marketplace",
                 "task_platform", "local_company", "unknown")
# Company type x headquarters -> pay tier, set from the 2026-10-14 backtest (537 roles with posted pay, 307 companies;
# references/target-tier-research.md). Share of roles posting >= $10k/month:
#   US/Canada HQ: funded startup 95%, big company 91%, product company 94%, consultancy 94%, staffing 100%, task platform 14%
#   elsewhere:    funded startup 82%, big company 62%, product company 0% (n=4), consultancy 0%, staffing 20%
# US staffing firms post US salaries for US placements, while every quote to the user for remote LATAM work, from
# staffing firms, nearshore agencies and marketplaces alike, was under the floor (recruiter-outreach/references/
# pay-tier-research.md; marketplaces post "up to" ranges they don't pay the user: micro1 $130-160k posted, $7.5k
# quoted). What they post is therefore not what they'd pay this candidate, so the middleman types are
# `likely_below` whatever the HQ (2026-10-15; before that US-HQ ones were "unclear" and reached tier B). US
# consultancies employ their engineers directly, so they stay "unclear".
HIGH_PAY_HQ = {"united states", "usa", "us", "u.s.", "united states of america", "canada"}
MIDDLEMAN_TYPES = ("staff_aug", "marketplace", "task_platform")
TYPE_PAY = {  # (type, US/Canada HQ?) -> tier
    ("funded_startup", True): "likely_target", ("funded_startup", False): "likely_target",
    ("big_company", True): "likely_target", ("big_company", False): "unclear",
    ("product_company", True): "likely_target", ("product_company", False): "likely_below",
    ("consultancy", True): "unclear", ("consultancy", False): "likely_below",
    ("staff_aug", True): "likely_below", ("staff_aug", False): "likely_below",
    ("marketplace", True): "likely_below", ("marketplace", False): "likely_below",
    ("task_platform", True): "likely_below", ("task_platform", False): "likely_below",
    ("local_company", True): "likely_below", ("local_company", False): "likely_below",
}
# Sources where the poster is the hiring company itself, with no staffing or marketplace layer in between: the
# people check takes their companies first (autopilot people-queue), since the user's goal is direct hires.
DIRECT_SOURCES = re.compile(r"HN Who is hiring|Work at a Startup|portfolio|Company job boards|Wellfound", re.I)


def type_pay(ctype: str, hq: str) -> str:
    return TYPE_PAY.get((ctype, (hq or "").strip().lower() in HIGH_PAY_HQ), "unclear")
SENIOR = re.compile(r"\b(senior|sr\.?|staff|principal|lead|founding|head)\b", re.I)
FUNDED_SRC = re.compile(r"portfolio|work at a startup|y ?combinator", re.I)
FUNDED_DAYS = 180


def norm(name: str) -> str:
    return co.key_of(name)


def squash(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


# ------------------------------------------------------------------- context

def context(home: str, roles: list[dict], st: dict, scores: dict | None = None) -> dict:
    """Everything pay_tier needs, built once per command."""
    hp = js.fix_path(home)
    scores = scores if scores is not None else enrich.read_json(hp / "autopilot" / "scores.json", {})
    ceil = {}
    for e in enrich.read_json(hp / "recruiters" / "pay_ceilings.json", {}).values():
        for k in {norm(e.get("name", "")), squash(e.get("name", ""))}:
            if k:
                ceil[k] = e
    # posted pay of every engineering role per company: role key -> (company key, monthly top)
    by_company: dict[str, list[tuple[str, float]]] = {}
    for r in roles:
        sp = scores.get(r["key"]) or {}
        hi = sp.get("pay_max") or sp.get("pay_min")
        if hi and co.engineering_title(r.get("title", "")):
            by_company.setdefault(norm(r.get("company", "")), []).append((r["key"], float(hi)))
    for k, pays in co.company_pay(home).items():
        by_company.setdefault(k, []).extend(("board", float(x)) for x in pays)
    funded = {}
    for e in enrich.read_json(hp / "recruiters" / "funded_companies.json", {}).values():
        dates = [x.get("date") for x in e.get("funding") or [] if x.get("date")]
        funded[norm(e.get("name", ""))] = max(dates) if dates else "portfolio"
    return {"st": st, "target": float(st["pay_target_usd_month"]), "floor": float(st.get("pay_skip_below_usd_month") or 0),
            "ceil": ceil, "pay_by_company": by_company, "types": company_types(home), "funded": funded,
            "people": co.people_checks(home), "scores": scores}


def company_types(home: str) -> dict:
    return enrich.read_json(js.fix_path(home) / "autopilot" / "company_types.json", {})


# ------------------------------------------------------------------- pay tier

def pay_tier(role: dict, sp: dict | None, ctx: dict, use_posted: bool = True) -> dict:
    """{tier, source, detail, gate}. `gate` is True when the company's own quotes to the user top out under the floor:
    the role is held back like recruiter-outreach's pay gate. use_posted=False is the backtest: pretend the posting
    stated no pay, and leave the role itself out of its company's sibling pay."""
    sp = sp or {}
    target, floor = ctx["target"], ctx["floor"]
    company = role.get("company", "")
    k = norm(company)
    hi = sp.get("pay_max") or sp.get("pay_min")
    if use_posted and hi:
        note = sp.get("pay_note") or f"~USD {round(hi):,}/month"
        if hi >= target:
            return {"tier": "posted_target", "source": "posted", "detail": f"Posted: {note}"}
        if floor and hi < floor:
            return {"tier": "posted_below", "source": "posted", "detail": f"Posted: {note}"}
        return {"tier": "posted_near", "source": "posted", "detail": f"Posted: {note} (under the target)"}
    # 1. what the company quoted the user
    q = ctx["ceil"].get(k) or ctx["ceil"].get(squash(company))
    if q and q.get("max_usd_month"):
        mx = q["max_usd_month"]
        last = (q.get("quotes") or [{}])[-1]
        if mx >= target:
            return {"tier": "likely_target", "source": "your quotes",
                    "detail": f"{q.get('name')} quoted you up to ${mx:,}/month ({len(q.get('quotes') or [])} quotes, latest {last.get('at', '')})"}
        if floor and mx < floor:
            return {"tier": "likely_below", "source": "your quotes", "gate": True,
                    "detail": f"{q.get('name')}'s highest quote to you was ${mx:,}/month, under your ${round(floor):,} floor"}
    # 2. what the company posts for its other engineering roles
    sib = [p for rk, p in ctx["pay_by_company"].get(k, []) if rk != role.get("key")]
    if not use_posted and hi:
        # the backtest: the same posting often appears twice (a board copy, another URL); drop every sibling with
        # the role's own pay so the answer can't leak in
        sib = [p for p in sib if abs(p - hi) > 0.01 * hi]
    if sib:
        med = statistics.median(sib)
        detail = f"its other {len(sib)} engineering posting{'s' if len(sib) > 1 else ''} top out at a median ~${round(med):,}/month"
        if med >= target:
            return {"tier": "likely_target", "source": "sibling postings", "detail": detail}
        if floor and med < floor:
            return {"tier": "likely_below", "source": "sibling postings", "detail": detail}
    # 3. the kind of company
    t = ctx["types"].get(k) or {}
    if t.get("confidence") == "guess":
        t = {**t, "type": "unknown"}  # a guess from the name or a vague JD doesn't move the estimate
    ctype = t.get("type") or co.name_type(company) or "unknown"
    tier = type_pay(ctype, t.get("hq_country", ""))
    detail = f"company type: {ctype.replace('_', ' ')}" + (f", HQ {t['hq_country']}" if t.get("hq_country") else "") + \
        (f" ({t['evidence']})" if t.get("evidence") else "")
    if tier != "unclear":
        return {"tier": tier, "source": "company type", "detail": detail}
    # 4. a recent round (registry, the company-type pass, or a VC/YC board) with a senior title
    since = (NOW - timedelta(days=FUNDED_DAYS)).strftime("%Y-%m-%d")
    rnd = ctx["funded"].get(k) or ((t.get("funding") or {}).get("date") or "")
    vc = any(FUNDED_SRC.search(s) for s in role.get("sources") or [])
    if (rnd and (rnd == "portfolio" or rnd >= since) or vc) and SENIOR.search(role.get("title", "")):
        why = f"funding round {rnd}" if rnd and rnd != "portfolio" else "VC/YC portfolio" if (vc or rnd) else ""
        return {"tier": "likely_target", "source": "funding + seniority", "detail": f"{why}, senior title"}
    return {"tier": "unclear", "source": "", "detail": "no posted pay and no evidence either way"}


def company_type_of(role: dict, ctx: dict) -> dict:
    """{type, confidence, detail} for the role's company: the company-type subagent's judgement when it isn't a
    guess, else the type its name gives away (companies.name_type), else unknown."""
    company = role.get("company", "")
    t = ctx["types"].get(norm(company)) or {}
    if t.get("type") and t.get("confidence") != "guess":
        detail = f"{t['type'].replace('_', ' ')}" + (f": {t['evidence']}" if t.get("evidence") else "")
        return {"type": t["type"], "confidence": t.get("confidence", ""), "detail": detail}
    by_name = co.name_type(company)
    if by_name:
        return {"type": by_name, "confidence": "name", "detail": f"{by_name.replace('_', ' ')} (from the company name)"}
    return {"type": t.get("type") or "unknown", "confidence": t.get("confidence", ""), "detail": ""}


def is_direct_source(labels: list[str] | None) -> bool:
    return any(DIRECT_SOURCES.search(s or "") for s in labels or [])


def pay_points(pt: dict) -> int:
    """The score change for a pay tier with no posted pay (posted pay keeps its own +10/+6/-10)."""
    return {"likely_target": 6, "likely_below": -8}.get(pt.get("tier"), 0)


# -------------------------------------------------------------------- tiers

def tier_of(pay: str, people: str | None) -> str:
    p = 2 if pay in PAY_OK else 0 if pay in PAY_BAD else 1
    r = 2 if people in REGION_OK else 0 if people == "none" else 1
    return "A" if p + r == 4 else "B" if p + r == 3 else "C"


def tier_reason(pay: dict, people: str | None, tier: str) -> str:
    ppl = {"brazil": f"engineers in {userconfig.country()}", "latam": f"engineers elsewhere in {userconfig.region()}",
           "non_us": "engineers only elsewhere outside the US", "none": "no engineers outside the US found"}.get(people, "people check not run yet")
    return f"Tier {tier}: pay {pay['tier'].replace('_', ' ')}; {ppl}"


# ------------------------------------------------------------ company types

def company_type_entries(home: str, roles: list[dict], keys: set[str] | None = None) -> list[dict]:
    """One entry per company without a type yet: a few titles, one JD file, its sources and locations."""
    hp = js.fix_path(home)
    have = company_types(home)
    index = enrich.read_json(hp / "jds" / "index.json", {})
    out: dict[str, dict] = {}
    for r in roles:
        k = norm(r.get("company", ""))
        if not k or k in have or (keys is not None and k not in keys) or co.name_type(r.get("company", "")):
            continue  # a staffing or marketplace name already decides the type (pay_tier reads it from the name)
        e = out.setdefault(k, {"key": k, "company": r["company"], "titles": [], "locations": [], "sources": [], "jd_file": ""})
        if len(e["titles"]) < 4 and r["title"] not in e["titles"]:
            e["titles"].append(r["title"])
        if r.get("location") and r["location"] not in e["locations"] and len(e["locations"]) < 3:
            e["locations"].append(r["location"])
        e["sources"] = sorted(set(e["sources"]) | set(r.get("sources") or []))[:4]
        jd = index.get(r["key"], {})
        if not e["jd_file"] and jd.get("status") == "ok":
            e["jd_file"] = enrich.host_path(hp / "jds" / jd["file"])
    return list(out.values())


def ingest_company_types(home: str, files: list[Path]) -> dict:
    path = js.fix_path(home) / "autopilot" / "company_types.json"
    cache = enrich.read_json(path, {})
    n, bad = 0, []
    for f in files:
        for d in enrich.read_jsonl(f):
            k = d.get("key") or norm(d.get("company", ""))
            if not k:
                continue
            if d.get("type") not in COMPANY_TYPES:
                bad.append(f'{d.get("company")}: {d.get("type")}')
                continue
            cache[k] = {"company": d.get("company"), "type": d["type"], "hq_country": d.get("hq_country", ""),
                        "pays_in": d.get("pays_in", ""), "funding": d.get("funding") or None,
                        "evidence": (d.get("evidence") or "")[:200], "checked": TODAY, "web": bool(d.get("web_checked")),
                        "confidence": d.get("confidence") if d.get("confidence") in ("known", "jd", "web", "guess") else "guess"}
            n += 1
    enrich.write_json(path, cache, indent=1)
    return {"ingested": n, "invalid": bad[:20], "known": len(cache)}


# ------------------------------------------------------------------ backtest

def backtest(home: str, roles: list[dict], st: dict) -> dict:
    """Hide the posted pay of every role that posted it, estimate its tier from the other evidence, and compare.
    The weights are only worth trusting when likely_target beats the base rate clearly."""
    ctx = context(home, roles, st)
    target, floor = ctx["target"], ctx["floor"]
    rows = []
    for r in roles:
        sp = ctx["scores"].get(r["key"]) or {}
        hi = sp.get("pay_max") or sp.get("pay_min")
        if not hi:
            continue
        truth = "target" if hi >= target else "below_floor" if floor and hi < floor else "near"
        est = pay_tier(r, sp, ctx, use_posted=False)
        rows.append({"truth": truth, "tier": est["tier"], "source": est["source"] or "none", "company": r.get("company", "")})
    n = len(rows)
    base = sum(x["truth"] == "target" for x in rows) / n if n else 0

    def stats(sel):
        k = len(sel)
        return {"n": k, "truly_target": round(sum(x["truth"] == "target" for x in sel) / k, 3) if k else None,
                "truly_under_target": round(sum(x["truth"] != "target" for x in sel) / k, 3) if k else None}

    by_source = {}
    for src in sorted({x["source"] for x in rows}):
        sel = [x for x in rows if x["source"] == src]
        by_source[src] = {t: stats([x for x in sel if x["tier"] == t]) for t in ("likely_target", "likely_below", "unclear")
                          if any(x["tier"] == t for x in sel)}
    res = {"date": TODAY, "roles_with_posted_pay": n, "companies": len({x["company"] for x in rows}),
           "base_rate_target": round(base, 3),
           "likely_target": stats([x for x in rows if x["tier"] == "likely_target"]),
           "likely_below": stats([x for x in rows if x["tier"] == "likely_below"]),
           "unclear": stats([x for x in rows if x["tier"] == "unclear"]),
           "coverage": round(sum(x["tier"] != "unclear" for x in rows) / n, 3) if n else 0,
           "by_source": by_source,
           "note": "Roles that post pay skew to US product companies with pay-transparency rules, so the base rate here is "
                   "higher than among roles that post none."}
    # The verdict is about separation: "likely_target" must beat the base rate, and "likely_below" must catch roles
    # under the target at least twice as often as roles overall are. (With an 85% base rate, "beat the base rate by
    # 10 points" would be nearly impossible even for a perfect estimate.)
    lt, lb = res["likely_target"], res["likely_below"]
    under = 1 - base
    res["lift"] = {"target_side": round((lt["truly_target"] or 0) - base, 3),
                   "below_side": round((lb["truly_under_target"] or 0) / under, 2) if under and lb["n"] else None}
    if lt["n"] < 20:
        res["verdict"] = "too_little_evidence"
    elif lt["truly_target"] > base and (not lb["n"] or lb["n"] < 10 or (lb["truly_under_target"] or 0) >= 2 * under):
        res["verdict"] = "use"
    else:
        res["verdict"] = "no_better_than_base_rate"
    enrich.write_json(js.fix_path(home) / "autopilot" / "paytier_backtest.json", res, indent=1)
    return res
