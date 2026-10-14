"""Company facts shared by job-source-sweep, job-autopilot and recruiter-outreach.

    jobsweep.py boards        [--max-age-hours 20] [--batches 14]   # find and read companies' ATS job boards
    jobsweep.py people-queue  [--limit 60] [--companies a,b]        # batches for the people check (references/people-check.md)
    jobsweep.py people-ingest [--dir DIR]                           # fold people_out_*.jsonl into people_checks.json

What lives here, all in <home>:

    boards.json         company key -> its Ashby / Greenhouse / Lever / Workable board and every open posting on it
                        (title, location, region label, posted date, monthly USD pay). Boards are found from the apply
                        links of roles the sweep already saw and from recruiter-outreach's funded-company registry,
                        and read through each ATS's public API. Two uses: the sweep's `company_boards` source (the
                        companies' own roles like the target, with their own posted dates), and job-autopilot's pay
                        estimate (what a company posts for its other engineering roles).
    people_checks.json  company key -> whether engineers who work there are based in the candidate's country
                        (`brazil`, named for historical reasons: it means candidate.country), elsewhere in the
                        candidate's region (`latam`), elsewhere outside the US (`non_us`) or nowhere we could find
                        (`none`), with the search results that showed it.

Why the people check exists: a posting's region label (in the JD or the ATS location field) is written by automated
systems and is often wrong. The only evidence that a company really hires outside the US is engineers who work there
from outside the US. It is found with search engines (`site:linkedin.com/in` queries), never with LinkedIn's own
search, which rate-limits the account fast (user rule 2026-10-14).
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jobsweep as js  # noqa: E402
import userconfig  # noqa: E402

NOW = datetime.now(timezone.utc)
TODAY = NOW.strftime("%Y-%m-%d")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def key_of(name: str) -> str:
    return js.company_key(name or "")


# ------------------------------------------------------------ posting regions
# Labels read from a posting's own location and workplace fields. They only order roles; they are never evidence
# that a company hires in the candidate's region (that is the people check's job).

REGIONS = ("open", "nearby", "unclear", "us_only", "other_region", "onsite")
TARGET_REGIONS = ("open", "nearby", "unclear")
# countries of a candidate region (candidate.region, lowercased): a posting in one of them is "nearby"
NEIGHBOURS = {"latam": re.compile(r"\b(mexico|méxico|colombia|argentina|chile|peru|perú|uruguay|paraguay|bolivia|ecuador|"
                                  r"venezuela|costa rica|panama|panamá|guatemala|dominican republic|puerto rico|brazil|brasil|"
                                  r"andean|andes|caribbean|central america|bogot[aá]|medell[ií]n|buenos aires|santiago|lima|"
                                  r"montevideo|cdmx|mexico city|são paulo|sao paulo)\b")}
_US_LOC = re.compile(r"\b(us|u\.s\.?|usa|united states|canada|north america|new york|nyc|san francisco|sf|bay area|"
                     r"seattle|boston|austin|los angeles|chicago|denver|miami|toronto)\b", re.I)
_OTHER_LOC = re.compile(r"\b(uk|united kingdom|london|europe|emea|eu|germany|berlin|france|paris|spain|netherlands|"
                        r"poland|portugal|ireland|italy|austria|belgium|switzerland|sweden|denmark|norway|finland|czech\w*|"
                        r"romania|greece|hungary|ukraine|serbia|india|apac|asia|singapore|japan|korea|china|australia|"
                        r"new zealand|israel|tel aviv|uae|dubai|turkey|south africa|nigeria|kenya|egypt)\b", re.I)


def region_of(location: str, remote, cand: dict) -> str:
    """open / nearby / unclear / us_only / other_region / onsite, from a posting's location text and remote flag."""
    loc = location or ""
    low = loc.lower()
    mine = [x.lower() for x in [cand.get("country"), cand.get("region")] + list(cand.get("region_aliases") or []) if x]
    if any(re.search(r"\b" + re.escape(t) + r"\b", low) for t in mine) or \
            re.search(r"\b(anywhere|worldwide|global|international|americas|latam|latin america|south america)\b", low):
        return "open"
    near = NEIGHBOURS.get((cand.get("region") or "").lower())
    if near and near.search(low):
        return "nearby"  # another country of the candidate's region: the company hires there, worth asking
    if remote is False or (remote is None and "remote" not in low):
        return "onsite"
    if _OTHER_LOC.search(loc):  # "Poland; United States; Germany" is a multi-country list without the region
        return "other_region"
    return "us_only" if _US_LOC.search(loc) or re.search(r",\s*[A-Z]{2}\b", loc) else "unclear"


def usd_month(lo, hi, interval: str = "", currency: str = "USD") -> int | None:
    if (currency or "USD").upper() != "USD":
        return None
    v = hi or lo
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    i = (interval or "year").lower()
    return round(v * 160 if "hour" in i else v * 4.33 if "week" in i else v if "month" in i else v / 12)


# --------------------------------------------------------------- role titles

# A raw ATS board lists every job at the company, so a board title needs an engineering word as well as a target
# term: "AI Engineer" counts, "Account Executive, AI Native" does not.
ENG_TITLE = re.compile(r"\b(engineer\w*|scientist|developer|research\w*|architect|programmer|swe|mle|founding|"
                       r"member of (the )?technical staff|mts)\b", re.I)
NOT_ENG = re.compile(r"\b(intern(ship)?|sales|account executive|recruit\w*|marketing|solutions engineer|"
                     r"customer success|support engineer)\b", re.I)


def title_terms() -> list[str]:
    """Posting-title words that count as the target role: recruiter_outreach.company_lane_title_terms in config.yaml,
    or else the words of role.title."""
    cfg = userconfig.load()
    terms = [t for t in ((cfg.get("recruiter_outreach") or {}).get("company_lane_title_terms") or []) if t]
    if not terms:
        role = (cfg.get("role") or {}).get("title", "")
        terms = [w for w in re.split(r"\W+", role) if len(w) > 1 and w.lower() not in ("engineer", "senior", "staff")]
    return terms


def title_re(terms: list[str] | None = None) -> re.Pattern:
    terms = terms if terms is not None else title_terms()
    return re.compile(r"\b(" + "|".join(re.escape(t) for t in terms) + r")\b", re.I) if terms else re.compile(".")


def target_title(title: str, pattern: re.Pattern | None = None) -> bool:
    pattern = pattern or title_re()
    t = title or ""
    return bool(pattern.search(t) and ENG_TITLE.search(t) and not NOT_ENG.search(t))


def engineering_title(title: str) -> bool:
    return bool(ENG_TITLE.search(title or "") and not NOT_ENG.search(title or ""))


# ------------------------------------------------------------ employer types
# Names and text that mark a staffing firm, a talent marketplace or a task platform rather than the hiring company.
# In the 2026-10-13 inbox study these quoted under the $8k floor almost every time (recruiter-outreach/references/
# pay-tier-research.md); in applika they produced the "Beyond the offered range" rejections.

VENDOR = re.compile(r"\b(c2c|corp[- ]to[- ]corp|w2|bench\s*sale|us it (staffing|recruit)|it staffing|"
                    r"staffing solutions|vendor management|h1b|implementation partner)\b", re.I)
PLATFORM = re.compile(r"\b(annotation|data labell?ing|rlhf|task creator|ai trainer|ai training|freelance tasks|"
                      r"expert network|honorarium)\b", re.I)
STAFF_AUG = re.compile(r"\b(nearshore|near-shore|staff augmentation|staffing|outsourc\w*|offshore|software factory|"
                       r"talent as a service|dedicated teams?|delivery manager|account manager)\b", re.I)
PREMIUM = re.compile(r"\b(y ?combinator|yc ?[ws]\d{2}|\byc\b|series [abc]\b|seed[- ]stage|founding (engineer|team|ai)|"
                     r"vc[- ]backed|venture[- ]backed|venture capital|portfolio compan\w+|talent partner at [a-z ]*(ventures|capital|partners)|"
                     r"executive search|ai talent search|startups?\b)", re.I)
# talent marketplaces and staffing names that show up as the "company" of a posting; their roles are not a hiring
# company's own
NOT_EMPLOYER = re.compile(r"\b(jobgether|toptal|turing|andela|crossover|arc\.dev|braintrust|micro1|strider|mercor|"
                          r"index\.dev|outlier|remotasks|data ?annotation|bairesdev|deel|remote\.com|jobright|"
                          r"dailyremote|himalayas|remote ?rocketship|lupa|hired|confidential)\b", re.I)


def name_type(name: str) -> str:
    """A company type from its name alone: marketplace, staff_aug or task_platform, else ''."""
    n = name or ""
    if NOT_EMPLOYER.search(n):
        return "marketplace"
    if VENDOR.search(n) or STAFF_AUG.search(n):
        return "staff_aug"
    if PLATFORM.search(n):
        return "task_platform"
    return ""


# ---------------------------------------------------------------- ATS boards

BOARD_KINDS = ("ashby", "greenhouse", "lever", "workable")
_ATS_URL = [
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([^/?#]+)", re.I)),
    ("greenhouse", re.compile(r"(?:job-boards|boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_app\?for=)?([^/?#&]+)", re.I)),
    ("lever", re.compile(r"jobs\.(?:eu\.)?lever\.co/([^/?#]+)", re.I)),
    ("workable", re.compile(r"apply\.workable\.com/([^/?#]+)", re.I)),
]
_BAD_SLUG = {"j", "jobs", "embed", "api", "v1", "careers", ""}


def ats_from_url(url: str) -> dict | None:
    """{"kind", "slug"} when a URL points into an Ashby / Greenhouse / Lever / Workable board."""
    for kind, pat in _ATS_URL:
        m = pat.search(url or "")
        if m and m[1].lower() not in _BAD_SLUG:
            return {"kind": kind, "slug": m[1]}
    return None


def board_roles(f, ats: dict) -> list:
    """Every open posting on a company's ATS board: title, url, location, remote flag, posted date, monthly USD pay."""
    kind, slug = (ats.get("kind") or "").lower(), ats.get("slug") or ""
    out = []
    if kind == "ashby":
        for j in f.json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true", interval=1).get("jobs") or []:
            if j.get("isListed") is False:
                continue
            locs = [j.get("location") or ""] + [x.get("location") or "" for x in j.get("secondaryLocations") or []]
            pay = None
            for c in (j.get("compensation") or {}).get("summaryComponents") or []:
                if c.get("compensationType") == "Salary":
                    pay = usd_month(c.get("minValue"), c.get("maxValue"), c.get("interval"), c.get("currencyCode"))
            wt = (j.get("workplaceType") or "").lower()
            out.append({"title": j.get("title", ""), "url": j.get("jobUrl", ""), "location": "; ".join(x for x in locs if x),
                        "remote": True if j.get("isRemote") or wt == "remote" else False if wt in ("onsite", "hybrid") else None,
                        "posted": (j.get("publishedAt") or "")[:10],
                        "pay_usd_month": pay})
    elif kind == "greenhouse":
        for j in f.json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", interval=1).get("jobs") or []:
            out.append({"title": j.get("title", ""), "url": j.get("absolute_url", ""),
                        "location": (j.get("location") or {}).get("name", ""), "remote": None,
                        "posted": (j.get("first_published") or j.get("updated_at") or "")[:10], "pay_usd_month": None})
    elif kind == "lever":
        for j in f.json(f"https://api.lever.co/v0/postings/{slug}?mode=json", interval=1) or []:
            cat = j.get("categories") or {}
            locs = list(dict.fromkeys([cat.get("location") or ""] + list(cat.get("allLocations") or [])))
            sr = j.get("salaryRange") or {}
            wt = (j.get("workplaceType") or "").lower()
            out.append({"title": j.get("text", ""), "url": j.get("hostedUrl", ""), "location": "; ".join(x for x in locs if x),
                        "remote": True if wt == "remote" else False if wt in ("on-site", "onsite", "hybrid") else None,
                        "posted": datetime.fromtimestamp((j.get("createdAt") or 0) / 1000, timezone.utc).strftime("%Y-%m-%d")
                        if j.get("createdAt") else "",
                        "pay_usd_month": usd_month(sr.get("min"), sr.get("max"), sr.get("interval"), sr.get("currency"))})
    elif kind == "workable":
        for j in f.json(f"https://apply.workable.com/api/v1/widget/accounts/{slug}", interval=1).get("jobs") or []:
            locs = [", ".join(x for x in [l.get("city"), l.get("region"), l.get("country")] if x)
                    for l in j.get("locations") or [] if not l.get("hidden")] or \
                [", ".join(x for x in [j.get("city"), j.get("state"), j.get("country")] if x)]
            out.append({"title": j.get("title", ""), "url": j.get("url") or j.get("shortlink", ""),
                        "location": "; ".join(x for x in locs if x), "remote": bool(j.get("telecommuting")) or None,
                        "posted": (j.get("published_on") or j.get("created_at") or "")[:10], "pay_usd_month": None})
    else:
        raise js.SourceError(f"no board reader for '{kind}'")
    return out


def boards(home: str) -> dict:
    return read_json(js.fix_path(home) / "boards.json", {})


def save_boards(home: str, data: dict) -> None:
    write_json(js.fix_path(home) / "boards.json", data)


def looks_named(name: str) -> bool:
    """A real company name ("CI&T", "Jeeves") rather than a board slug ("ciandt", "tryjeeves")."""
    return bool(re.search(r"[A-Z\s&.]", name or ""))


def discover_boards(home: str, batches: int = 14) -> dict:
    """Add every company whose role links (sweep rows, autopilot apply links) or registry entry point at a public
    ATS board. Returns {added, known}."""
    import enrich  # local: enrich imports jobsweep, which is already loaded
    hp = js.fix_path(home)
    reg = boards(home)
    added = 0
    # one entry per board: a role's "company" is sometimes the board slug ("ciandt" for CI&T), so a board already
    # known under another name is kept once, under the name that looks like a real company name
    by_board = {}
    for k, e in list(reg.items()):
        b = (e.get("ats") or {}).get("kind", ""), ((e.get("ats") or {}).get("slug") or "").lower()
        if b in by_board:
            keep, drop = (by_board[b], k) if looks_named(reg[by_board[b]]["name"]) or not looks_named(e["name"]) else (k, by_board[b])
            reg.pop(drop, None)
            by_board[b] = keep
        else:
            by_board[b] = k

    def add(company: str, ats: dict | None, via: str) -> None:
        nonlocal added
        k = key_of(company)
        if not k or not ats or name_type(company):
            return
        b = (ats["kind"], ats["slug"].lower())
        if b in by_board and by_board[b] != k:
            other = by_board[b]
            if looks_named(company) and not looks_named(reg[other]["name"]):
                reg[k] = {**reg.pop(other), "name": company}  # rename the slug-named entry
                by_board[b] = k
            return
        by_board[b] = k
        cur = reg.get(k)
        if cur and cur.get("ats") == ats:
            return
        if cur and cur.get("ats") and not cur.get("board_error"):
            return  # keep the first working board; a second slug is usually a mirror
        reg[k] = {**(cur or {}), "name": company, "ats": ats, "found_via": via, "found_at": (cur or {}).get("found_at") or TODAY}
        reg[k].pop("board_checked_at", None)
        added += cur is None

    try:
        roles = enrich.collect_roles(home, None, enrich.run_dates(home, batches))
    except SystemExit:
        roles = []
    for r in roles:
        add(r.get("company", ""), ats_from_url(r.get("url", "")), "sweep role link")
    for e in read_json(hp / "autopilot" / "ledger.json", {}).values():
        add(e.get("company", ""), ats_from_url(e.get("apply_url", "")), "autopilot apply link")
    for e in read_json(hp / "recruiters" / "funded_companies.json", {}).values():
        ats = e.get("ats") or {}
        if (ats.get("kind") or "").lower() in BOARD_KINDS and ats.get("slug"):
            add(e.get("name", ""), {"kind": ats["kind"].lower(), "slug": ats["slug"]}, "funded-company registry")
    save_boards(home, reg)
    return {"added": added, "known": len(reg)}


def refresh_boards(home: str, max_age_hours: float = 20, deadline: float | None = None, workers: int = 6) -> dict:
    """Read every board not read in the last max_age_hours. With a deadline (time.time() value), stop starting new
    reads after it; the rest is read next time."""
    import time
    reg = boards(home)
    cand = userconfig.snapshot()
    pattern = title_re()
    cutoff = (NOW - timedelta(hours=max_age_hours)).isoformat()
    due = [k for k, e in reg.items() if (e.get("ats") or {}).get("kind") in BOARD_KINDS and (e.get("board_checked_at") or "") < cutoff]
    f = js.Fetcher(UA)

    def one(k: str):
        if deadline and time.time() > deadline:
            return k, None, "deferred"
        try:
            return k, board_roles(f, reg[k]["ats"]), ""
        except js.SourceError as err:
            return k, None, str(err)[:160]

    with ThreadPoolExecutor(workers) as ex:
        results = list(ex.map(one, due))
    read = errors = deferred = 0
    for k, roles, err in results:
        e = reg[k]
        if err == "deferred":
            deferred += 1
            continue
        e["board_checked_at"] = NOW.isoformat()
        if roles is None:
            e["board_error"] = err
            errors += 1
            continue
        read += 1
        e.pop("board_error", None)
        e["open_roles_total"] = len(roles)
        # every engineering posting is kept (its pay feeds the pay estimate); `target` marks the ones like the role
        e["roles"] = [{**x, "region": region_of(x["location"], x["remote"], cand), "target": target_title(x["title"], pattern)}
                      for x in roles if engineering_title(x["title"])]
    save_boards(home, reg)
    return {"due": len(due), "read": read, "errors": errors, "deferred": deferred, "boards": len(reg),
            "target_roles": sum(1 for e in reg.values() for x in e.get("roles") or [] if x.get("target"))}


def company_pay(home: str) -> dict[str, list[int]]:
    """company key -> posted monthly USD pay tops of its engineering roles, from the boards and the funded registry."""
    out: dict[str, list[int]] = {}
    hp = js.fix_path(home)
    for src in (boards(home), read_json(hp / "recruiters" / "funded_companies.json", {})):
        for e in src.values():
            k = key_of(e.get("name", ""))
            for x in e.get("roles") or []:
                if x.get("pay_usd_month") and engineering_title(x.get("title", "")):
                    out.setdefault(k, []).append(int(x["pay_usd_month"]))
    return out


def median(values: list) -> float | None:
    return statistics.median(values) if values else None


# --------------------------------------------------------------- people check

LEVELS = ("brazil", "latam", "non_us", "none")      # strongest first; "brazil" means candidate.country
POSITIVE_TTL_DAYS = 90
NONE_TTL_DAYS = 30


def people_checks(home: str) -> dict:
    return read_json(js.fix_path(home) / "people_checks.json", {})


def fresh(v: dict | None) -> bool:
    if not v or v.get("level") not in LEVELS:
        return False
    ttl = NONE_TTL_DAYS if v["level"] == "none" else POSITIVE_TTL_DAYS
    return (v.get("checked") or "") >= (NOW - timedelta(days=ttl)).strftime("%Y-%m-%d")


def people_level(cache: dict, company: str) -> str | None:
    """brazil / latam / non_us / none for a checked company (stale verdicts still count until re-checked), else None."""
    v = cache.get(key_of(company))
    return v.get("level") if v and v.get("level") in LEVELS else None


def people_queries(company: str, cand: dict | None = None) -> list[str]:
    cand = cand or userconfig.snapshot()
    country = cand.get("country") or "Brazil"
    near = {"latam": "Argentina OR Mexico OR Colombia OR Chile OR Uruguay OR Peru"}.get((cand.get("region") or "").lower(), "")
    qs = [f'engineer "{company}" {country} site:linkedin.com/in']
    if near:
        qs.append(f'engineer "{company}" {near} site:linkedin.com/in')
    qs.append(f'engineer "{company}" site:linkedin.com/in')
    return qs


def search_urls(query: str) -> dict:
    q = quote_plus(query)
    # google first, yahoo when Google blocks (2026-10-14 tests: Bing, DuckDuckGo and Brave challenged on the first search)
    return {"google": f"https://www.google.com/search?q={q}&hl=en", "yahoo": f"https://search.yahoo.com/search?p={q}"}


def people_queue(home: str, wanted: list[dict], limit: int, out_dir: Path, batch_size: int = 10) -> dict:
    """Batch files for the people-check subagents. `wanted`: [{company, why, priority}] in the order to check; companies
    with a fresh verdict, staffing names and marketplaces are left out."""
    cache = people_checks(home)
    cand = userconfig.snapshot()
    seen, queue, cached = set(), [], 0
    for w in wanted:
        k = key_of(w.get("company", ""))
        if not k or k in seen or name_type(w["company"]):
            continue
        seen.add(k)
        if fresh(cache.get(k)):
            cached += 1
            continue
        qs = people_queries(w["company"], cand)
        queue.append({"company": w["company"], "key": k, "why": w.get("why", ""),
                      "queries": [{"q": q, "urls": search_urls(q)} for q in qs]})
    todo = queue[:limit]
    out_dir.mkdir(parents=True, exist_ok=True)
    batches = []
    stamp = NOW.strftime("%H%M")
    for i in range(0, len(todo), batch_size):
        n = i // batch_size + 1
        bf = out_dir / f"people_batch_{stamp}_{n}.json"
        of = out_dir / f"people_out_{stamp}_{n}.jsonl"
        bf.write_text(json.dumps(todo[i:i + batch_size], indent=1, ensure_ascii=False), encoding="utf-8")
        batches.append({"batch": host_path(bf), "out": host_path(of), "companies": len(todo[i:i + batch_size])})
    return {"queued": len(todo), "left_after_cap": max(0, len(queue) - limit), "fresh_cached": cached,
            "country": cand.get("country"), "region": cand.get("region"), "batches": batches}


def host_path(path: Path) -> str:
    import enrich
    return enrich.host_path(path)


def ingest_people(home: str, files: list[Path]) -> dict:
    """Fold people-check output lines into people_checks.json. A line without a usable level (blocked, failed) is not
    cached, so the company is queued again next time."""
    path = js.fix_path(home) / "people_checks.json"
    cache = read_json(path, {})
    got: dict[str, int] = {}
    blocked = []
    for f in files:
        for line in f.read_text(encoding="utf-8-sig").splitlines() if f.exists() else []:
            try:
                v = json.loads(line)
            except json.JSONDecodeError:
                continue
            k = v.get("key") or key_of(v.get("company", ""))
            level = v.get("level")
            if not k:
                continue
            if level not in LEVELS:
                blocked.append(f'{v.get("company")}: {v.get("status") or "no verdict"}')
                continue
            ev = [e for e in v.get("evidence") or [] if e.get("url")][:5]
            if level != "none" and not ev:
                level = "none"  # a positive level needs a search result behind it
            cache[k] = {"company": v.get("company"), "level": level, "checked": v.get("checked") or TODAY,
                        "engine": v.get("engine", ""), "queries": v.get("queries_run") or [], "evidence": ev,
                        "detail": (v.get("detail") or "")[:300]}
            got[level] = got.get(level, 0) + 1
    write_json(path, cache)
    return {"ingested": got, "not_cached": blocked[:20], "cache": len(cache)}


# ------------------------------------------------------------------ commands

def cmd_boards(args) -> int:
    import time
    found = discover_boards(args.home, args.batches)
    res = refresh_boards(args.home, args.max_age_hours, time.time() + args.budget if args.budget else None)
    print(json.dumps({**found, **res}, indent=1))
    return 0


def cmd_people_queue(args) -> int:
    import enrich
    out = js.fix_path(args.home) / "runs" / f"{enrich.today()}-explore"
    if args.companies:
        wanted = [{"company": c} for c in args.companies]
    else:  # the sweep's own use: companies of today's new rows, busiest first
        roles = enrich.collect_roles(args.home, None)
        counts: dict[str, int] = {}
        names = {}
        for r in roles:
            k = key_of(r.get("company", ""))
            if k:
                counts[k] = counts.get(k, 0) + 1
                names[k] = r["company"]
        wanted = [{"company": names[k]} for k in sorted(counts, key=lambda k: -counts[k])]
    print(json.dumps(people_queue(args.home, wanted, args.limit, out), indent=1, ensure_ascii=False))
    return 0


def cmd_people_ingest(args) -> int:
    import enrich
    d = js.fix_path(args.dir) if args.dir else js.fix_path(args.home) / "runs" / f"{enrich.today()}-explore"
    print(json.dumps(ingest_people(args.home, sorted(d.glob("people_out_*.jsonl"))), indent=1, ensure_ascii=False))
    return 0


def add_commands(sub) -> None:
    import os
    home_default = os.environ.get("JOBSWEEP_HOME") or userconfig.home()
    p = sub.add_parser("boards", help="find companies' ATS boards from role links and read them (boards.json)")
    p.add_argument("--home", default=home_default)
    p.add_argument("--max-age-hours", type=float, default=20)
    p.add_argument("--batches", type=int, default=14)
    p.add_argument("--budget", type=float, default=0, help="stop starting board reads after this many seconds")
    p.set_defaults(fn=cmd_boards)
    p = sub.add_parser("people-queue", help="batches for the people check (references/people-check.md)")
    p.add_argument("--home", default=home_default)
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--companies", type=lambda s: [x.strip() for x in s.split(",") if x.strip()])
    p.set_defaults(fn=cmd_people_queue)
    p = sub.add_parser("people-ingest", help="fold people_out_*.jsonl into people_checks.json")
    p.add_argument("--home", default=home_default)
    p.add_argument("--dir", help="folder with people_out_*.jsonl (default: today's explore folder)")
    p.set_defaults(fn=cmd_people_ingest)
