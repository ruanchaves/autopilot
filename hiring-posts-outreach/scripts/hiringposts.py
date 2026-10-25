#!/usr/bin/env python3
"""Hiring-posts outreach: LinkedIn post search for "<role> hiring" phrasings, ranked by the posted pay and by
explicit eligibility for the candidate's region, then contacted best-first. A simpler sibling of recruiter-outreach:
no profile visits, no scoring rubric, no company lane. The post is the evidence. Leads whose posts state neither pay
nor eligibility are ordered by the poster's likely country, nearest to the candidate's country first. The country is
judged by a lightweight LLM subagent (references/origin.md) from the name, headline and company; `origin-plan` lists
the leads still waiting for a judgement and `origin` stores the answers (names.py holds the distance table).

    hiringposts.py search-plan  [--max-queries 12]   # the next phrasings to run (least recently run first) + result path
    hiringposts.py ingest       --result FILE        # posts from a search subagent -> leads.json, ranked
    hiringposts.py rank         [--top 30]           # the ranked pile (what `plan` will hand to the sender)
    hiringposts.py origin-plan  [--all]              # tier-1 no-region leads awaiting the origin judge (--all: every one)
    hiringposts.py origin       --result FILE        # the judge's countries -> leads.json, re-ranked
    hiringposts.py plan         [--n 40]             # today's tasks for the sender, best first, within the shared caps
    hiringposts.py check        --kind connect|inmail|message --profile URL [--company C]   # shared with recruiter-outreach
    hiringposts.py record       --result FILE        # the sender's result: shared contacts.json + pacing + own statuses
    hiringposts.py status

Pay and region limits, caps and the candidate come from config.yaml (hiring_posts, recruiter_outreach, pay, candidate,
banned_companies). Contacts, daily counters and the per-company cap are the same files recruiter-outreach uses
(<home>/recruiters/contacts.json, pacing.json, companies.json): one LinkedIn account, one budget.
Own data lives in <home>/hiring-posts/:
    leads.json      one entry per post author: their posts, best pay, eligibility, origin guess, rank tier, status
    searches.json   every phrasing run: when, how many posts it found, how many leads made the pile
    work/<date>/    search and outreach result files
"""

from __future__ import annotations

import argparse
import json
import hashlib
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve()
SWEEP = HERE.parents[2] / "job-source-sweep" / "scripts"
RC = HERE.parents[2] / "recruiter-outreach" / "scripts"
sys.path.insert(0, str(SWEEP))
sys.path.insert(0, str(RC))

import jobsweep as js  # noqa: E402
import enrich  # noqa: E402
import userconfig  # noqa: E402
import recruiters as rc  # noqa: E402  (shared contacts, pacing, caps, check/record)
import names  # noqa: E402  (nationality guess from a name, distance from the candidate's country)
from companies import NEIGHBOURS, NOT_EMPLOYER  # noqa: E402

NOW = datetime.now(timezone.utc)
TODAY = NOW.strftime("%Y-%m-%d")

DEFAULTS = {
    "roles": [],                   # role names searched; default: role.title from config.yaml
    "suffixes": ["hiring", "LATAM hiring", "remote hiring", "Latin America hiring", "Brazil hiring"],
    "extra_queries": [],           # whole phrasings added as they are
    "floor_usd_month": 7000,       # posts whose highest stated pay is under this are dropped
    "max_post_age_days": 30,       # older posts are ignored
    "query_cooldown_days": 2,      # a phrasing is rerun after this long (results are sorted by date, so new posts appear)
    "scrolls_per_query": 4,        # how far the search subagent scrolls each result page
    "fx_usd": {"BRL": 0.18, "EUR": 1.08, "GBP": 1.27, "CAD": 0.72, "MXN": 0.05, "ARS": 0.001, "COP": 0.00025, "INR": 0.012},
}


# ------------------------------------------------------------------ files

def hp_dir(home: str) -> Path:
    p = js.fix_path(home) / "hiring-posts"
    p.mkdir(parents=True, exist_ok=True)
    return p


def work_dir(home: str) -> Path:
    p = hp_dir(home) / "work" / TODAY
    p.mkdir(parents=True, exist_ok=True)
    return p


def load(home: str, name: str, default):
    return enrich.read_json(hp_dir(home) / name, default)


def save(home: str, name: str, data) -> None:
    enrich.write_json(hp_dir(home) / name, data, indent=1)


def settings(home: str) -> dict:
    cfg = userconfig.load()
    s = dict(DEFAULTS)
    s.update(cfg.get("hiring_posts") or {})
    if not s["roles"]:
        s["roles"] = [(cfg.get("role") or {}).get("title", "AI Engineer")]
    shared = rc.settings(home)  # caps, gaps, max_per_company, cta_styles, banned companies, pay target
    for k in ("connect_per_day", "inmail_per_day", "message_per_day", "gap_seconds", "max_per_company", "cta_styles",
              "banned_companies", "pay_target_usd_month", "target_role"):
        s[k] = shared[k]
    s["candidate"] = userconfig.candidate()
    return s


# ------------------------------------------------------------------ pay

_CUR = {"r$": "BRL", "brl": "BRL", "us$": "USD", "usd": "USD", "$": "USD", "€": "EUR", "eur": "EUR", "£": "GBP",
        "gbp": "GBP", "cad": "CAD", "mxn": "MXN", "ars": "ARS", "cop": "COP", "inr": "INR"}
_NUM = re.compile(r"(?P<cur>r\$|us\$|usd|brl|eur|gbp|cad|mxn|ars|cop|inr|[$€£])?\s*(?P<n>\d{1,3}(?:[.,]\d{3})+|\d+(?:[.,]\d+)?)\s*"
                  r"(?P<k>k\b|mil\b)?\s*(?P<cur2>usd|brl|eur|gbp|cad|mxn|dollars?)?", re.I)
_HOUR = re.compile(r"/\s*h(ou)?r|\bper hour\b|\bhourly\b|\bhora\b|\bp/h\b|\bph\b", re.I)
_MONTH = re.compile(r"/\s*mo(nth)?\b|\bper month\b|\bmonthly\b|\bm[eê]s\b|\bmensal|\bmensual|\bal mes\b|\bpor m[eê]s\b", re.I)
_YEAR = re.compile(r"/\s*y(ea)?r\b|\bper year\b|\bper annum\b|\bannual|\byearly\b|\bp\.?a\.?\b|\bano\b|\bano\b|\bbase salary\b", re.I)


def pay_usd_month(text: str, fx: dict) -> tuple[int | None, str]:
    """The highest pay a text states, in USD per month, and how it was stated (annual|monthly|hourly|'')."""
    t = (text or "").replace(" ", " ")
    if not t.strip():
        return None, ""
    interval = "hourly" if _HOUR.search(t) else "monthly" if _MONTH.search(t) else "annual" if _YEAR.search(t) else ""
    best, basis = None, ""
    for m in _NUM.finditer(t):
        raw = m["n"]
        if not (m["cur"] or m["k"] or m["cur2"]):
            continue  # a bare number (years, headcount, a date) is not pay
        raw = raw.replace(".", "").replace(",", "") if re.fullmatch(r"\d{1,3}([.,]\d{3})+", raw) else raw.replace(",", ".")
        try:
            v = float(raw)
        except ValueError:
            continue
        if m["k"]:
            v *= 1000
        cur = _CUR.get((m["cur"] or m["cur2"] or "$").lower().strip(), "USD")
        if cur == "USD" and (m["cur2"] or "").lower().startswith("dollar"):
            cur = "USD"
        rate = 1.0 if cur == "USD" else float(fx.get(cur, 0) or 0)
        if rate <= 0 or v <= 0:
            continue
        iv = interval or ("hourly" if v < 400 else "monthly" if v < 30000 else "annual")
        usd = v * rate
        month = usd * 160 if iv == "hourly" else usd if iv == "monthly" else usd / 12
        if month < 500 or month > 100000:
            continue
        if best is None or month > best:
            best, basis = round(month), iv
    return best, basis


# ------------------------------------------------------------------ eligibility

ELIG = ("latam", "worldwide", "unclear", "excluded")
_WORLD = re.compile(r"\b(anywhere|worldwide|global(ly)?|international|remote[- ]first|from any country|any location|"
                    r"work from anywhere|across the americas|americas)\b", re.I)
_EXCL = re.compile(r"\b(us[- ]only|u\.s\.[- ]only|usa only|us[- ]based only|must be (located |based )?in the (us|usa|u\.s\.)|"
                   r"authori[sz]ed to work in the (us|usa|u\.s\.|uk|eu)|eu only|europe only|uk only|emea only|apac only|"
                   r"india only|canada only|on[- ]?site|onsite|in[- ]office|hybrid|relocat\w+|w2 only|c2c|"
                   r"no (sponsorship|visa)[^.]{0,30}(us|usa)|us citizens?|green card)\b", re.I)


def eligibility(cand: dict, *texts: str) -> str:
    t = " ".join(x for x in texts if x).lower()
    mine = [x.lower() for x in [cand.get("country"), cand.get("region")] + list(cand.get("region_aliases") or []) if x]
    near = NEIGHBOURS.get((cand.get("region") or "").lower())
    explicit = any(re.search(r"\b" + re.escape(x) + r"\b", t) for x in mine) or (near and near.search(t))
    if explicit:
        return "latam"   # the value name is historical: read it as "the candidate's region, named"
    if _EXCL.search(t):
        return "excluded"
    if _WORLD.search(t):
        return "worldwide"
    return "unclear"


# ------------------------------------------------------------------ leads

def post_key(url: str) -> str:
    m = re.search(r"urn:li:(activity|share|ugcPost):(\d+)", url or "")
    return f"{m[1]}:{m[2]}" if m else (url or "").strip().lower().rstrip("/")


def text_key(who: str, text: str) -> str:
    """Fallback post key when the search card exposed no post URL: the author plus a hash of the text."""
    norm = re.sub(r"s+", " ", (text or "").strip().lower())[:300]
    return f"{who}#text:{hashlib.sha1(norm.encode('utf-8')).hexdigest()[:12]}" if norm else ""


def post_age_days(posted: str) -> int | None:
    s = (posted or "").strip().lower()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        try:
            return (NOW.date() - datetime(int(m[1]), int(m[2]), int(m[3])).date()).days
        except ValueError:
            return None
    m = re.match(r"(\d+)\s*([smhdw])", s)
    if m:
        n, u = int(m[1]), m[2]
        return 0 if u in "smh" else n if u == "d" else n * 7
    m = re.match(r"(\d+)\s*(mo|month)", s)
    return int(m[1]) * 30 if m else None


def tier(lead: dict, st: dict) -> int | None:
    """0: pay at or above the floor; 1: no pay stated; None: out (below floor, excluded region, banned, stale)."""
    if lead.get("eligibility") == "excluded" or lead.get("banned") or lead.get("stale"):
        return None
    pay = lead.get("pay_usd_month")
    if pay is None:
        return 1
    return 0 if pay >= st["floor_usd_month"] else None


def origin_km(lead: dict) -> int:
    """The sort distance for the name-origin tie-break: unknown names sit at names.UNKNOWN_KM."""
    o = lead.get("origin") or {}
    return o["distance_km"] if o.get("distance_km") is not None else names.UNKNOWN_KM


def needs_origin(lead: dict) -> bool:
    """Only leads with no stated pay and no region words are ordered by the poster's likely country."""
    return lead.get("tier") == 1 and lead.get("eligibility") == "unclear"


def rank_key(lead: dict) -> tuple:
    e = ELIG.index(lead.get("eligibility", "unclear"))
    km = origin_km(lead) if needs_origin(lead) else 0
    return (lead.get("tier", 9), -(lead.get("pay_usd_month") or 0), e, km, lead.get("age_days", 99))


def set_origin(lead: dict) -> None:
    """lead['origin']: the judge's answer when there is one, else pending (unknown distance until judged)."""
    ov = lead.get("origin_override")
    if ov:
        lead["origin"] = {"country": ov.get("country"), "distance_km": names.distance_km(ov.get("country")),
                          "confidence": ov.get("confidence", "high"), "basis": ov.get("basis", "judge"),
                          "by": ov.get("by", "judge")}
        return
    if "linkedin.com/company/" in (lead.get("profile_url") or ""):
        lead["origin"] = {"country": None, "distance_km": None, "confidence": "none", "basis": "company page", "by": "script"}
        return
    lead["origin"] = dict(names.PENDING)


def origin_pending(lead: dict) -> bool:
    """A lead the judge has not answered yet (company pages never need one)."""
    return needs_origin(lead) and (lead.get("origin") or {}).get("by") == "pending"


def refresh(lead: dict, st: dict) -> None:
    """Recompute a lead's summary from its posts."""
    posts = lead.get("posts", [])
    for p in posts:
        if p.get("posted"):
            p["age_days"] = post_age_days(p["posted"])
    fresh = [p for p in posts if p.get("age_days") is None or p["age_days"] <= st["max_post_age_days"]]
    lead["stale"] = not fresh
    pool = fresh or posts
    pays = [p["pay_usd_month"] for p in pool if p.get("pay_usd_month")]
    lead["pay_usd_month"] = max(pays) if pays else None
    lead["pay_basis"] = next((p.get("pay_basis", "") for p in pool if p.get("pay_usd_month") == lead["pay_usd_month"]), "")
    el = {p.get("eligibility", "unclear") for p in pool}
    lead["eligibility"] = next((x for x in ELIG if x in el), "unclear")  # ELIG is ordered best-first
    lead["age_days"] = min((p["age_days"] for p in pool if p.get("age_days") is not None), default=99)
    lead["banned"] = rc.is_banned(lead.get("company", ""), st) or any(rc.is_banned(p.get("company", ""), st) for p in pool)
    lead["tier"] = tier(lead, st)
    set_origin(lead)
    if lead.get("status") in (None, "new", "out"):
        lead["status"] = "new" if lead["tier"] is not None else "out"
        if lead["status"] == "out":
            lead["out_reason"] = ("stale post" if lead["stale"] else "banned company" if lead["banned"] else
                                  "region excluded" if lead["eligibility"] == "excluded" else
                                  f"pay {lead['pay_usd_month']} under floor {st['floor_usd_month']}")
        else:
            lead.pop("out_reason", None)


def pile(leads: dict, st: dict, cts: dict) -> list:
    out = []
    for who, v in leads.items():
        if v.get("status") != "new" or v.get("tier") is None:
            continue
        if cts.get(who, {}).get("status") == "sent":
            v["status"] = "contacted_elsewhere"
            continue
        out.append(v)
    return sorted(out, key=rank_key)


def first_degree(v: dict) -> bool:
    return (v.get("degree") or "").strip().lower().startswith("1")


# ------------------------------------------------------------------ commands

def all_queries(st: dict) -> list:
    qs = [f"{r} {s}".strip() for r in st["roles"] for s in st["suffixes"]]
    qs += list(st.get("extra_queries") or [])
    seen, out = set(), []
    for q in qs:
        k = q.lower()
        if k not in seen:
            seen.add(k)
            out.append(q)
    return out


def cmd_search_plan(args) -> int:
    st = settings(args.home)
    runs = load(args.home, "searches.json", {})
    leads = load(args.home, "leads.json", {})
    cool = NOW - timedelta(days=st["query_cooldown_days"])
    due = []
    for q in all_queries(st):
        r = runs.get(q.lower())
        last = datetime.fromisoformat(r["last"]) if r and r.get("last") else None
        if last is None or last < cool:
            due.append((last or datetime.min.replace(tzinfo=timezone.utc), q))
    due.sort()
    queries = [q for _, q in due][: args.max_queries]
    known = sorted({post_key(p.get("url", "")) for v in leads.values() for p in v.get("posts", []) if p.get("url")})
    c = st["candidate"]
    return rc.emit({"queries": queries, "due": len(due), "total": len(all_queries(st)),
                    "cooldown_days": st["query_cooldown_days"], "scrolls_per_query": st["scrolls_per_query"],
                    "max_post_age_days": st["max_post_age_days"], "floor_usd_month": st["floor_usd_month"],
                    "roles": st["roles"], "target_role": st["target_role"],
                    "region_terms": [c.get("country"), c.get("region")] + list(c.get("region_aliases") or []),
                    "banned_companies": st["banned_companies"], "known_posts": known,
                    "known_authors": sorted(leads), "search_url": "https://www.linkedin.com/search/results/content/?keywords=<query>&sortBy=%22date_posted%22",
                    "result": str(work_dir(args.home) / f"search-{NOW.strftime('%H%M%S')}.json")})


def cmd_ingest(args) -> int:
    st = settings(args.home)
    res = rc.load_result(args.result)
    leads = load(args.home, "leads.json", {})
    runs = load(args.home, "searches.json", {})
    cts = rc.contacted(args.home)
    added = new_posts = 0
    touched = set()
    per_query: Counter = Counter()
    for p in res.get("posts") or []:
        who = rc.norm_profile(p.get("author_profile_url", ""))
        if not who or "linkedin.com/in/" not in who and "linkedin.com/company/" not in who:
            continue
        text = p.get("text", "") or ""
        pay, basis = pay_usd_month(" ".join([p.get("pay_text", "") or "", text]), st["fx_usd"])
        if pay is None and p.get("pay_usd_month_hint"):
            try:
                pay, basis = int(p["pay_usd_month_hint"]), p.get("pay_basis_hint", "")
            except (TypeError, ValueError):
                pass
        el = p.get("eligibility") if p.get("eligibility") in ELIG else eligibility(st["candidate"], p.get("region_text", ""), text)
        if el == "unclear" and p.get("region_text"):
            el = eligibility(st["candidate"], p.get("region_text", ""))
        company = (p.get("company") or p.get("author_company") or "").strip()
        key = post_key(p.get("url", "")) or text_key(who, text)  # cards without a post link: author + text
        post = {"key": key, "url": p.get("url", ""), "posted": p.get("posted", ""),
                "age_days": post_age_days(p.get("posted", "")), "roles": p.get("roles") or [], "company": company,
                "pay_text": p.get("pay_text", ""), "pay_usd_month": pay, "pay_basis": basis,
                "region_text": p.get("region_text", ""), "eligibility": el, "excerpt": text[:700],
                "reposted": bool(p.get("reposted")), "found_via": p.get("found_via", ""), "seen": NOW.isoformat()}
        lead = leads.get(who)
        if lead is None:
            lead = leads[who] = {"found_at": NOW.isoformat(), "status": "new", "profile_url": p.get("author_profile_url", ""),
                                 "name": p.get("author_name", ""), "headline": p.get("author_headline", ""),
                                 "company": company or p.get("author_company", ""), "degree": p.get("degree", ""),
                                 "source": p.get("found_via", ""), "sources": [], "posts": [], "contact": None}
            added += 1
        else:
            for k in ("name", "headline", "degree"):
                if p.get(f"author_{k}" if k != "degree" else "degree") and not lead.get(k):
                    lead[k] = p.get(f"author_{k}" if k != "degree" else "degree")
            if not lead.get("company") and company:
                lead["company"] = company
        if p.get("found_via") and p["found_via"] not in lead["sources"] and p["found_via"] != lead.get("source"):
            lead["sources"].append(p["found_via"])
        if post["key"] and post["key"] not in {x.get("key") for x in lead["posts"]}:
            lead["posts"].append(post)
            new_posts += 1
            per_query[(p.get("found_via") or "").lower()] += 1
        touched.add(who)
    for who in touched:
        refresh(leads[who], st)
    for q in res.get("queries_run") or []:
        key = (q if isinstance(q, str) else q.get("query", "")).lower()
        if not key:
            continue
        r = runs.setdefault(key, {"query": key, "runs": 0, "posts": 0, "first": NOW.isoformat()})
        r["runs"] += 1
        r["last"] = NOW.isoformat()
        r["posts"] += per_query.get(f"posts:{key}", 0) + per_query.get(key, 0)
        r["last_posts"] = per_query.get(f"posts:{key}", 0) + per_query.get(key, 0)
    save(args.home, "leads.json", leads)
    save(args.home, "searches.json", runs)
    ranked = pile(leads, st, cts)
    return rc.emit({"posts_in_result": len(res.get("posts") or []), "new_posts": new_posts, "leads_added": added,
                    "pile": len(ranked), "with_pay": sum(v["tier"] == 0 for v in ranked),
                    "first_degree": sum(first_degree(v) for v in ranked),
                    "out": dict(Counter(v.get("out_reason", "") for v in leads.values() if v.get("status") == "out")),
                    "blocked": res.get("blocked", False), "origin": origin_summary(ranked),
                    "top": [line(v) for v in ranked[:8]]})


def origin_text(v: dict) -> str:
    o = v.get("origin") or {}
    if o.get("by") == "pending":
        return "origin pending"
    if not o.get("country"):
        return "origin ?"
    return f"~{o['country']} {o['distance_km']:,}km {o.get('confidence', '')}"


def line(v: dict) -> str:
    pay = f"${v['pay_usd_month']:,}/mo ({v.get('pay_basis', '')})" if v.get("pay_usd_month") else "no pay"
    roles = "; ".join(r for p in v["posts"][:1] for r in p.get("roles", [])[:2]) or "-"
    el = v.get("eligibility") + (f" ({origin_text(v)})" if needs_origin(v) else "")
    return f"{pay} | {el} | {v.get('degree') or '?'} | {v.get('name')} ({v.get('company') or '-'}): {roles}"


def origin_summary(ranked: list) -> dict:
    """How the no-pay, no-region part of the pile reads by judged country, how many still await the judge, and how
    many the judge could not place."""
    sub = [v for v in ranked if needs_origin(v)]
    return {"leads": len(sub),
            "pending": sum(1 for v in sub if origin_pending(v)),
            "by_country": dict(Counter((v.get("origin") or {}).get("country") or "?" for v in sub if not origin_pending(v)).most_common()),
            "unknown": sum(1 for v in sub if not origin_pending(v) and not (v.get("origin") or {}).get("country")),
            "low_confidence": sum(1 for v in sub if (v.get("origin") or {}).get("confidence") == "low")}


def cmd_rank(args) -> int:
    st = settings(args.home)
    leads = load(args.home, "leads.json", {})
    for v in leads.values():
        refresh(v, st)
    save(args.home, "leads.json", leads)
    ranked = pile(leads, st, rc.contacted(args.home))
    return rc.emit({"pile": len(ranked), "with_pay": sum(v["tier"] == 0 for v in ranked),
                    "floor_usd_month": st["floor_usd_month"], "origin": origin_summary(ranked),
                    "ranked": [line(v) for v in ranked[: args.top]]})


def cmd_origin_plan(args) -> int:
    """The leads whose order depends on the poster's country and that the judge has not answered yet (--all: every
    such lead, judged or not), with the inputs the judge may use: name, headline, company."""
    st = settings(args.home)
    leads = load(args.home, "leads.json", {})
    for v in leads.values():
        refresh(v, st)
    save(args.home, "leads.json", leads)
    ranked = pile(leads, st, rc.contacted(args.home))
    want = needs_origin if args.all else origin_pending
    rows = [{"profile_url": v.get("profile_url"), "name": v.get("name"), "headline": v.get("headline", ""),
             "company": v.get("company", ""), "origin": v.get("origin")} for v in ranked if want(v)]
    c = st["candidate"]
    return rc.emit({"home_country": c.get("country"), "countries": sorted(names.COUNTRY), "unknown_km": names.UNKNOWN_KM,
                    "summary": origin_summary(ranked), "leads": rows, "judge_reference": str(HERE.parent.parent / "references" / "origin.md"),
                    "result": str(work_dir(args.home) / f"origins-{NOW.strftime('%H%M%S')}.json")})


def cmd_origin(args) -> int:
    """Store the judge's answers ({"origins": [{"profile_url", "country", "confidence", "basis"}]}) and re-rank.
    A null country is a real answer ("cannot tell"): the lead stays at the unknown distance and is not asked again.
    A country outside names.COUNTRY (after alias folding) is skipped and reported, so the lead stays pending."""
    st = settings(args.home)
    res = rc.load_result(args.result)
    leads = load(args.home, "leads.json", {})
    set_n = cannot_tell = 0
    unknown = []
    for o in res.get("origins") or []:
        v = leads.get(rc.norm_profile(o.get("profile_url", "")))
        if not v:
            continue
        raw = (o.get("country") or "").strip()
        country = names.canon(raw)
        if raw and not country:
            unknown.append({"profile_url": o.get("profile_url"), "country": raw})
            continue
        if not country:
            cannot_tell += 1
        v["origin_override"] = {"country": country, "confidence": o.get("confidence", "high" if country else "none"),
                                "basis": o.get("basis", "judge"), "by": o.get("by", "judge"), "at": NOW.isoformat()}
        set_n += 1
    for v in leads.values():
        refresh(v, st)
    save(args.home, "leads.json", leads)
    ranked = pile(leads, st, rc.contacted(args.home))
    return rc.emit({"origins_set": set_n, "cannot_tell": cannot_tell, "unknown_country_skipped": unknown,
                    "origin": origin_summary(ranked), "top": [line(v) for v in ranked[:8]]})


def cmd_plan(args) -> int:
    st = settings(args.home)
    leads = load(args.home, "leads.json", {})
    cts = rc.contacted(args.home)
    p = rc.pacing(args.home)
    q = rc.quotas(p, st)
    cos = rc.companies(args.home, cts)
    ranked = pile(leads, st, cts)
    budget = (q["connect"]["left"] or 0) + (q["inmail"]["left"] or 0)
    tasks, deferred_cap = [], []
    for v in ranked:
        room, _ = rc.company_room(args.home, st, v.get("company", ""), cos)
        if room <= 0:
            deferred_cap.append(v["name"])
            continue
        if not first_degree(v) and sum(not t["first_degree"] for t in tasks) >= budget:
            continue
        best = sorted(v["posts"], key=lambda x: (-(x.get("pay_usd_month") or 0), ELIG.index(x.get("eligibility", "unclear"))))[0]
        tasks.append({"name": v["name"], "profile_url": v["profile_url"], "headline": v.get("headline", ""),
                      "company": v.get("company", ""), "degree": v.get("degree", ""), "first_degree": first_degree(v),
                      "lead_type": "hiring_post", "cta": rc.cta_for(v["profile_url"], st["cta_styles"]),
                      "rank": {"pay_usd_month": v.get("pay_usd_month"), "pay_basis": v.get("pay_basis", ""),
                               "eligibility": v.get("eligibility"), "tier": v.get("tier"),
                               "origin": v.get("origin") if needs_origin(v) else None},
                      "post": {k: best.get(k) for k in ("url", "posted", "roles", "company", "pay_text", "region_text", "excerpt")},
                      "other_posts": [{k: x.get(k) for k in ("url", "posted", "roles", "pay_text", "region_text")}
                                      for x in v["posts"] if x is not best][:2]})
        if len(tasks) >= args.n:
            break
    save(args.home, "leads.json", leads)
    cfg = userconfig.load()
    out = {"tasks": tasks, "quotas": q, "needed": budget, "pile": len(ranked), "deferred_company_cap": deferred_cap,
           "candidate": userconfig.snapshot(), "banned_companies": st["banned_companies"],
           "fit_rubric": str(js.fix_path(args.home) / "fit-rubric.md"),
           "resume": str(js.fix_path((cfg.get("resume") or {}).get("base", ""))),
           "contacts_file": str(rc.rc_dir(args.home) / "contacts.json"),
           "message_reference": str(HERE.parents[1] / "references" / "message.md"),
           "result": str(work_dir(args.home) / f"outreach-{NOW.strftime('%H%M%S')}.json")}
    path = work_dir(args.home) / f"plan-{NOW.strftime('%H%M%S')}.json"
    enrich.write_json(path, out, indent=1)
    return rc.emit({**{k: v for k, v in out.items() if k != "tasks"}, "task_file": str(path), "tasks_count": len(tasks),
                    "tasks_preview": [f"{t['degree'] or '?'} {t['name']} ({t['company'] or '-'}) ${t['rank']['pay_usd_month'] or 0:,} {t['rank']['eligibility']}"
                                      for t in tasks[:12]]})


def cmd_record(args) -> int:
    """Shared ledger first (contacts.json, pacing.json, companies.json via recruiter-outreach), then own statuses."""
    st = settings(args.home)
    res = rc.load_result(args.result)
    for c in res.get("contacts") or []:
        c.setdefault("lead_type", "hiring_post")
    rc.cmd_record(args)
    leads = load(args.home, "leads.json", {})
    sent = skipped = deferred = 0
    for c in res.get("contacts") or []:
        who = rc.norm_profile(c.get("profile_url", ""))
        v = leads.get(who)
        if not v:
            continue
        if c.get("status") == "sent":
            sent += 1
            v.update(status="contacted", contact={"at": NOW.isoformat(), "channel": c.get("channel"), "subject": c.get("subject", ""),
                                                  "message": c.get("message", ""), "cta": c.get("cta", "")})
        elif rc.deferred(c):
            deferred += 1
        elif c.get("status") in ("skipped", "failed"):
            skipped += 1
            if c.get("status") == "skipped":
                v.update(status="skipped", skip_reason=c.get("detail", ""))
    save(args.home, "leads.json", leads)
    p = rc.pacing(args.home)
    return rc.emit({"own_sent": sent, "own_skipped": skipped, "own_deferred": deferred, "quotas": rc.quotas(p, st),
                    "inmail_credits": p.get("inmail_credits")})


def cmd_status(args) -> int:
    st = settings(args.home)
    leads = load(args.home, "leads.json", {})
    for v in leads.values():
        refresh(v, st)
    save(args.home, "leads.json", leads)
    cts = rc.contacted(args.home)
    ranked = pile(leads, st, cts)
    p = rc.pacing(args.home)
    q = rc.quotas(p, st)
    first = sum(first_degree(v) for v in ranked) if q["message"]["cap"] != 0 else 0
    others = sum(not first_degree(v) for v in ranked)
    credits = p.get("inmail_credits")
    budget_left = (q["connect"]["left"] or 0) + (0 if credits == 0 else (q["inmail"]["left"] or 0))
    runs = load(args.home, "searches.json", {})
    cool = NOW - timedelta(days=st["query_cooldown_days"])
    due = sum(1 for qq in all_queries(st) if not runs.get(qq.lower(), {}).get("last") or datetime.fromisoformat(runs[qq.lower()]["last"]) < cool)
    return rc.emit({"leads": len(leads), "by_status": dict(Counter(v["status"] for v in leads.values())),
                    "pile": len(ranked), "pile_with_pay": sum(v["tier"] == 0 for v in ranked),
                    "first_degree_pending": first, "contactable_now": others,
                    "quotas": q, "inmail_credits": credits, "budget_left": budget_left,
                    "queries_due": due, "queries_total": len(all_queries(st)),
                    "done": (budget_left == 0 or (others == 0 and due == 0)) and first == 0,
                    "top": [line(v) for v in ranked[:8]]})


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--home", default=userconfig.home())
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("search-plan"); p.add_argument("--max-queries", type=int, default=12); p.set_defaults(fn=cmd_search_plan)
    p = sub.add_parser("ingest"); p.add_argument("--result", required=True); p.set_defaults(fn=cmd_ingest)
    p = sub.add_parser("rank"); p.add_argument("--top", type=int, default=30); p.set_defaults(fn=cmd_rank)
    p = sub.add_parser("origin-plan"); p.add_argument("--all", action="store_true"); p.set_defaults(fn=cmd_origin_plan)
    p = sub.add_parser("origin"); p.add_argument("--result", required=True); p.set_defaults(fn=cmd_origin)
    p = sub.add_parser("plan"); p.add_argument("--n", type=int, default=40); p.set_defaults(fn=cmd_plan)
    p = sub.add_parser("check"); p.add_argument("--kind", required=True, choices=["connect", "inmail", "message"])
    p.add_argument("--profile", required=True); p.add_argument("--company", default=""); p.set_defaults(fn=rc.cmd_check)
    p = sub.add_parser("record"); p.add_argument("--result", required=True); p.set_defaults(fn=cmd_record)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
