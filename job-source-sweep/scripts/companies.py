"""Company facts shared by job-source-sweep, job-autopilot and recruiter-outreach.

    jobsweep.py boards        [--max-age-hours 20] [--batches 14]   # find and read companies' ATS job boards
    jobsweep.py people-queue  [--limit N] [--mode search|chrome]    # search + judge batches (people-judge.md), or the
                                                                    # Chrome fallback (people-check.md)
    jobsweep.py people-ingest [--dir DIR]                           # fold people_out_*.jsonl into people_checks.json
    jobsweep.py search QUERY  [--no-cache]                          # one query through the search layer
    jobsweep.py people-harvest [--pages 3] / people-harvest-ingest  # weekly: employers of engineers in the country

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
import os
import re
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, quote_plus, urlsplit

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
    kind, slug = (ats.get("kind") or "").lower(), quote(ats.get("slug") or "", safe="")
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

    def add(company: str, ats: dict | None, via: str, sector: str = "") -> None:
        nonlocal added
        k = key_of(company)
        if not k or not ats or name_type(company):
            return
        if sector and k in reg and not reg[k].get("sector"):
            reg[k]["sector"] = sector  # a seed can tag a board the sweep already found
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
        if sector:
            reg[k]["sector"] = sector
        reg[k].pop("board_checked_at", None)
        added += cur is None

    # boards the catalogue seeds (sources.json company_boards.seed): read from the first run, before any role link
    for s in next((x.get("seed") or [] for x in js.load_catalogue()["http"] if x.get("id") == "company_boards"), []):
        if (s.get("kind") or "").lower() in BOARD_KINDS and s.get("slug"):
            add(s.get("name", ""), {"kind": s["kind"].lower(), "slug": s["slug"]}, "catalogue seed", s.get("sector", ""))

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


# ---------------------------------------------------------------- search layer
# Every people-check and harvest query goes through Searcher: the ddgs metasearch library (MIT, pinned at
# DDGS_VERSION, run by serp_helper.py inside the search virtualenv), engines tried in config order with a circuit
# breaker each, 4-6 s jittered gaps, a per-run query budget, a canary query that tells "no results" from "quietly
# blocked", and a 30-day cache of raw results in <home>/serp_cache/. Tested 2026-10-14 from WSL: about 130 queries,
# no block. Only ddgs's Yahoo engine (Bing's index) answers a script: Google, Brave, DuckDuckGo, Mojeek and Startpage
# returned nothing, and in ddgs 9.16.0 "bing" and "yandex" are disabled (asking for them silently ran "auto"; the
# helper now refuses them). One working engine, so Chrome stays the fallback (job-autopilot/references/
# target-tier-research.md).

DDGS_VERSION = "9.16.0"
SEARCH_DEFAULTS = {"python": "~/.venvs/search/bin/python", "backends": ["yahoo"], "min_gap_seconds": 4,
                   "jitter_seconds": 2, "max_queries_per_run": 300, "cache_days": 30, "empty_cache_days": 3,
                   "suspend_hours": 6, "errors_to_suspend": 2, "empty_streak_halt": 8,
                   "canary": 'site:br.linkedin.com/in engineer "Stripe"'}
HELPER = Path(__file__).resolve().parent / "serp_helper.py"


def search_settings() -> dict:
    s = dict(SEARCH_DEFAULTS)
    s.update((userconfig.load().get("search") or {}))
    return s


class Searcher:
    """Paced, cached, budgeted search with a circuit breaker per engine. One instance per command run."""

    def __init__(self, home: str, budget: int | None = None):
        import time
        self.time = time
        self.home = home
        self.st = search_settings()
        hp = js.fix_path(home)
        self.state_path = hp / "search_state.json"
        self.state = read_json(self.state_path, {"backends": {}})
        self.cache_dir = hp / "serp_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.budget = int(budget if budget is not None else self.st["max_queries_per_run"])
        self.used = 0
        self.last = 0.0
        self.empty_streak = 0
        self.halted = ""
        self.stats: dict = {"queries": 0, "cache_hits": 0, "by_engine": {}, "empty": 0, "errors": 0, "suspended": [],
                            "halted": "", "canary": ""}
        self.python = str(Path(os.path.expanduser(self.st["python"])))

    # -- engines
    def _save(self) -> None:
        write_json(self.state_path, self.state)

    def available(self, be: str) -> bool:
        return (self.state["backends"].get(be) or {}).get("suspended_until", "") < datetime.now(timezone.utc).isoformat()

    def suspend(self, be: str, why: str) -> None:
        until = (datetime.now(timezone.utc) + timedelta(hours=float(self.st["suspend_hours"]))).isoformat()
        self.state["backends"][be] = {"suspended_until": until, "reason": why[:200], "errors": 0}
        self.stats["suspended"].append(f"{be}: {why[:80]}")
        self._save()

    def _pace(self) -> None:
        import random
        gap = float(self.st["min_gap_seconds"]) + random.random() * float(self.st["jitter_seconds"])
        wait = self.last + gap - self.time.time()
        if wait > 0:
            self.time.sleep(wait)
        self.last = self.time.time()

    def _run(self, be: str, query: str, page: int, timelimit: str = "") -> dict:
        import subprocess
        self._pace()
        try:
            r = subprocess.run([self.python, str(HELPER), json.dumps({"query": query, "backend": be, "page": page,
                                                                       "max_results": 10, "timeout": 10,
                                                                       "timelimit": timelimit or None})],
                               capture_output=True, text=True, timeout=60)
            return json.loads(r.stdout.strip().splitlines()[-1]) if r.stdout.strip() else \
                {"kind": "error", "error": (r.stderr or "no output")[-200:]}
        except Exception as err:  # noqa: BLE001
            return {"kind": "error", "error": f"{type(err).__name__}: {err}"[:200]}

    # -- cache
    def _cache_file(self, query: str, page: int, timelimit: str = "") -> Path:
        import hashlib
        key = f"{query}|{page}" + (f"|{timelimit}" if timelimit else "")
        return self.cache_dir / (hashlib.sha1(key.encode()).hexdigest()[:20] + ".json")

    def _cached(self, query: str, page: int, timelimit: str = "") -> dict | None:
        c = read_json(self._cache_file(query, page, timelimit), None)
        if not c:
            return None
        days = self.st["cache_days"] if c.get("results") else self.st["empty_cache_days"]
        return c if c.get("at", "") >= (datetime.now(timezone.utc) - timedelta(days=float(days))).isoformat() else None

    # -- the one entry point
    def query(self, query: str, page: int = 1, use_cache: bool = True, timelimit: str = "") -> dict:
        """{"results": [...], "engine": str, "cached": bool, "error": str}. Never raises. timelimit: d / w / m,
        the engine's own recency filter (ats_search asks for the past week; a fresh-results cache entry is kept
        apart from the unfiltered one)."""
        if use_cache:
            c = self._cached(query, page, timelimit)
            if c:
                self.stats["cache_hits"] += 1
                return {**c, "cached": True}
        if self.halted:
            return {"results": [], "engine": "", "cached": False, "error": self.halted}
        if self.used >= self.budget:
            self.halted = f"query budget of {self.budget} used"
            self.stats["halted"] = self.halted
            return {"results": [], "engine": "", "cached": False, "error": self.halted}
        tried, empty_on = [], []
        for be in self.st["backends"]:
            if not self.available(be):
                continue
            if len(empty_on) >= 2:
                break  # two engines found nothing: it is a real empty (they share Bing's index)
            res = self._run(be, query, page, timelimit)
            self.used += 1
            self.stats["queries"] += 1
            self.stats["by_engine"][be] = self.stats["by_engine"].get(be, 0) + 1
            tried.append(be)
            kind = res.get("kind")
            if kind == "ok":
                self.empty_streak = 0
                st = self.state["backends"].setdefault(be, {})
                st["errors"] = 0
                out = {"query": query, "page": page, "engine": be, "at": datetime.now(timezone.utc).isoformat(),
                       "timelimit": timelimit, "results": res.get("results") or []}
                write_json(self._cache_file(query, page, timelimit), out)
                self._save()
                return {**out, "cached": False, "error": ""}
            if kind == "empty":
                empty_on.append(be)
                continue
            if kind == "unavailable":
                self.suspend(be, res.get("error", "engine not available"))
                continue
            self.stats["errors"] += 1
            st = self.state["backends"].setdefault(be, {})
            st["errors"] = int(st.get("errors") or 0) + 1
            if kind == "ratelimit" or st["errors"] >= int(self.st["errors_to_suspend"]):
                self.suspend(be, f"{kind}: {res.get('error', '')}")
            self._save()
        if not tried:
            self.halted = "every search engine is suspended"
            self.stats["halted"] = self.halted
            return {"results": [], "engine": "", "cached": False, "error": self.halted}
        self.stats["empty"] += 1
        self.empty_streak += 1
        if self.empty_streak >= int(self.st["empty_streak_halt"]):
            # a quiet block looks like "no results" everywhere: confirm with the canary before going on
            if not self.canary():
                return {"results": [], "engine": "", "cached": False, "error": self.halted}
            self.empty_streak = 0
        out = {"query": query, "page": page, "engine": ",".join(empty_on), "at": datetime.now(timezone.utc).isoformat(),
               "results": []}
        if empty_on:
            write_json(self._cache_file(query, page), out)
        return {**out, "cached": False, "error": ""}

    def canary(self) -> bool:
        """A query that always has results. If it comes back empty, the engines are blocking: stop searching."""
        res = self.query(self.st["canary"], use_cache=False) if not self.halted else {"results": []}
        ok = bool(res.get("results"))
        self.stats["canary"] = "ok" if ok else "failed"
        if not ok:
            self.halted = "canary query returned nothing: the search engines look blocked"
            self.stats["halted"] = self.halted
            for be in self.st["backends"]:
                if self.available(be):
                    self.suspend(be, "canary failed")
        return ok


# --------------------------------------------------------------- people check

LEVELS = ("brazil", "latam", "non_us", "none")      # strongest first; "brazil" means candidate.country
POSITIVE_TTL_DAYS = 90
NONE_TTL_DAYS = 30
NEEDS_GOOGLE_DAYS = 7     # a company the search couldn't settle goes to the Chrome fallback for this long
METHOD = "search-v2"      # verdicts judged with company context (namesakes rejected); older ones are re-checked
METHODS = {"search-v2", "chrome-v2"}
COUNTRY_SUBDOMAIN = {"brazil": "br", "mexico": "mx", "argentina": "ar", "colombia": "co", "chile": "cl", "peru": "pe",
                     "uruguay": "uy", "paraguay": "py", "ecuador": "ec", "bolivia": "bo", "venezuela": "ve",
                     "costa rica": "cr", "panama": "pa", "guatemala": "gt", "dominican republic": "do",
                     "portugal": "pt", "spain": "es", "india": "in", "canada": "ca", "united kingdom": "uk"}
REGION_COUNTRIES = {"latam": ["mexico", "argentina", "colombia", "chile", "peru", "uruguay"]}


def people_checks(home: str) -> dict:
    return read_json(js.fix_path(home) / "people_checks.json", {})


def fresh(v: dict | None) -> bool:
    if not v or v.get("level") not in LEVELS:
        return False
    ttl = NONE_TTL_DAYS if v["level"] == "none" else POSITIVE_TTL_DAYS
    return (v.get("checked") or "") >= (NOW - timedelta(days=ttl)).strftime("%Y-%m-%d")


def needs_google(v: dict | None) -> bool:
    return bool(v) and v.get("status") == "needs_google" and \
        (v.get("checked") or "") >= (NOW - timedelta(days=NEEDS_GOOGLE_DAYS)).strftime("%Y-%m-%d")


def people_level(cache: dict, company: str) -> str | None:
    """brazil / latam / non_us / none for a checked company (stale verdicts still count until re-checked), else None."""
    v = cache.get(key_of(company))
    return v.get("level") if v and v.get("level") in LEVELS else None


def ambiguous_name(company: str) -> bool:
    """One plain word ("Neon", "Octane", "Circle", "Wonderful") or a very short name: search results are full of
    namesakes, so the judge needs a second clue, and the fetch never stops early."""
    tokens = re.findall(r"[A-Za-z0-9]+", company or "")
    return len(tokens) == 1 and (tokens[0].isalpha() or len(tokens[0]) <= 3)


def search_queries(company: str, cand: dict | None = None) -> list[dict]:
    """The script path: the profile-country subdomain is the location evidence (br.linkedin.com = a profile registered
    in Brazil). Candidate country first, the rest of the region in one OR query, then anywhere."""
    cand = cand or userconfig.snapshot()
    own = COUNTRY_SUBDOMAIN.get((cand.get("country") or "").lower(), "")
    near = [COUNTRY_SUBDOMAIN[c] for c in REGION_COUNTRIES.get((cand.get("region") or "").lower(), [])
            if COUNTRY_SUBDOMAIN.get(c) and COUNTRY_SUBDOMAIN[c] != own]
    qs = []
    if own:
        qs.append({"level": "brazil", "q": f'site:{own}.linkedin.com/in engineer "{company}"'})
    if near:
        sites = " OR ".join(f"site:{c}.linkedin.com/in" for c in near)
        qs.append({"level": "latam", "q": f'"{company}" engineer ({sites})'})
    qs.append({"level": "non_us", "q": f'"{company}" engineer site:linkedin.com/in'})
    return qs


def browser_queries(company: str, cand: dict | None = None) -> list[str]:
    """The Chrome fallback (references/people-check.md): Google first, Yahoo when Google blocks."""
    cand = cand or userconfig.snapshot()
    country = cand.get("country") or "Brazil"
    near = {"latam": "Argentina OR Mexico OR Colombia OR Chile OR Uruguay OR Peru"}.get((cand.get("region") or "").lower(), "")
    qs = [f'engineer "{company}" {country} site:linkedin.com/in']
    if near:
        qs.append(f'engineer "{company}" {near} site:linkedin.com/in')
    qs.append(f'engineer "{company}" site:linkedin.com/in')
    return qs


def search_urls(query: str) -> dict:
    # google first, yahoo when Google blocks (2026-10-14 tests: Bing, DuckDuckGo and Brave challenged on the first search)
    q = quote_plus(query)
    return {"google": f"https://www.google.com/search?q={q}&hl=en", "yahoo": f"https://search.yahoo.com/search?p={q}"}


def company_context(home: str) -> dict:
    """company key -> what we know about it, so the judge can tell it from a namesake."""
    hp = js.fix_path(home)
    out: dict[str, dict] = {}
    for k, t in read_json(hp / "autopilot" / "company_types.json", {}).items():
        out[k] = {"type": t.get("type", ""), "hq": t.get("hq_country", ""), "about": t.get("evidence", "")}
    for e in read_json(hp / "recruiters" / "funded_companies.json", {}).values():
        c = out.setdefault(key_of(e.get("name", "")), {})
        c.update({k: e[k] for k in ("website", "description", "hq") if e.get(k)})
    for e in boards(home).values():
        c = out.setdefault(key_of(e.get("name", "")), {})
        if e.get("ats"):
            c["ats_board"] = f'{e["ats"]["kind"]}:{e["ats"]["slug"]}'
    return out


def _candidate_hit(company: str, results: list) -> bool:
    """A result that names the company as the current employer of an engineer: "@ Acme", "at Acme", "- Acme |" in the
    title, or LinkedIn's "Experiência: Acme" / "Experience: Acme" line. A bare mention (a past client, a skill) is not
    enough to stop fetching: the 2026-10-14 judge rejected two early stops made on bare mentions."""
    name = re.escape((company or "").strip())
    employer = re.compile(rf"(@\s*{name}\b|\bat\s+{name}\b|-\s*{name}\s*(\||$)|(Experiência|Experience|Experiencia)\s*:\s*{name}\b)", re.I)
    for r in results:
        title, body = r.get("title") or "", r.get("body") or ""
        if "linkedin.com/in" in (r.get("href") or "") and (employer.search(title) or employer.search(body)) \
                and ENG_TITLE.search(title + " " + body[:160]):
            return True
    return False


def fetch_company(searcher: "Searcher", company: str, cand: dict) -> dict:
    """Run the search queries for one company, stopping early when an unambiguous name already shows an engineer at
    the company in the candidate's country. Returns {serps, fetch} for the judge."""
    serps, any_results = [], False
    amb = ambiguous_name(company)
    for i, q in enumerate(search_queries(company, cand)):
        res = searcher.query(q["q"])
        if res.get("error") and not res.get("results"):
            serps.append({"q": q["q"], "for_level": q["level"], "engine": "", "error": res["error"], "results": []})
            if searcher.halted:
                break
            continue
        serps.append({"q": q["q"], "for_level": q["level"], "engine": res.get("engine", ""), "cached": res.get("cached", False),
                      "results": [{k: (r.get(k) or "")[:400] for k in ("title", "href", "body")} for r in res.get("results") or []]})
        any_results |= bool(res.get("results"))
        hit = _candidate_hit(company, res.get("results") or [])
        if hit and not amb and q["level"] in ("brazil", "latam"):
            break  # the strongest level that matters is already shown; the judge confirms it
        if q["level"] == "latam" and not amb and any(_candidate_hit(company, s["results"]) for s in serps):
            break
    status = "halted" if searcher.halted and len(serps) < 2 else "ok" if any_results else "empty"
    return {"serps": serps, "fetch": status}


def people_queue(home: str, wanted: list[dict], limit: int, out_dir: Path, batch_size: int = 25,
                 mode: str = "search", recheck: bool = False, budget: int | None = None) -> dict:
    """Batch files for the people check. `wanted`: [{company, why}] in the order to check.

    mode "search" (default): fetch each company's search results through Searcher and write judge batches
    (references/people-judge.md, no browser). mode "chrome": the fallback for companies the search couldn't settle
    (status needs_google in the last NEEDS_GOOGLE_DAYS days), batches for references/people-check.md.
    recheck: also take companies whose verdict predates the judged method (METHOD), fresh or not."""
    cache = people_checks(home)
    cand = userconfig.snapshot()
    ctx = company_context(home)
    seen, queue, cached = set(), [], 0
    for w in wanted:
        k = key_of(w.get("company", ""))
        if not k or k in seen or name_type(w["company"]):
            continue
        seen.add(k)
        v = cache.get(k)
        if mode == "chrome":
            if needs_google(v):
                queue.append({"company": w["company"], "key": k, "why": w.get("why", ""), "context": ctx.get(k, {}),
                              "ambiguous_name": ambiguous_name(w["company"]), "search_said": (v or {}).get("detail", ""),
                              "queries": [{"q": q, "urls": search_urls(q)} for q in browser_queries(w["company"], cand)]})
            continue
        if needs_google(v):
            continue  # waiting for the Chrome fallback
        if fresh(v) and not (recheck and v.get("method") not in METHODS):
            cached += 1
            continue
        queue.append({"company": w["company"], "key": k, "why": w.get("why", "")})
    todo = queue[:limit]
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = NOW.strftime("%H%M")
    res = {"mode": mode, "queued": len(todo), "left_after_cap": max(0, len(queue) - limit), "fresh_cached": cached,
           "country": cand.get("country"), "region": cand.get("region"), "batches": []}
    if mode == "search" and todo:
        searcher = Searcher(home, budget)
        if not searcher.canary():
            res.update(search=searcher.stats, note="search engines look blocked: run the Chrome fallback for the top companies")
            todo = []
        fetched = []
        for e in todo:
            if searcher.halted:
                break
            e.update(fetch_company(searcher, e["company"], cand), ambiguous_name=ambiguous_name(e["company"]),
                     context=ctx.get(e["key"], {}))
            fetched.append(e)
        todo = fetched
        res["search"] = searcher.stats
        res["fetched"] = len(fetched)
        res["not_fetched_this_run"] = len(queue[:limit]) - len(fetched)
        batch_size = max(batch_size, 1)
    elif mode == "chrome":
        batch_size = 10
    prefix = "people_batch" if mode == "search" else "people_chrome_batch"
    for i in range(0, len(todo), batch_size):
        n = i // batch_size + 1
        bf = out_dir / f"{prefix}_{stamp}_{n}.json"
        of = out_dir / f"people_out_{stamp}{'c' if mode == 'chrome' else ''}_{n}.jsonl"
        bf.write_text(json.dumps(todo[i:i + batch_size], indent=1, ensure_ascii=False), encoding="utf-8")
        res["batches"].append({"batch": host_path(bf), "out": host_path(of), "companies": len(todo[i:i + batch_size]),
                               "reference": "people-judge.md" if mode == "search" else "people-check.md"})
    return res


def host_path(path: Path) -> str:
    import enrich
    return enrich.host_path(path)


def ingest_people(home: str, files: list[Path]) -> dict:
    """Fold people-check output lines into people_checks.json. A positive level needs evidence. "needs_google" lines
    are kept for NEEDS_GOOGLE_DAYS so the Chrome fallback picks them up; blocked or failed lines are not cached, so the
    company is queued again."""
    path = js.fix_path(home) / "people_checks.json"
    cache = read_json(path, {})
    got: dict[str, int] = {}
    blocked, fallback = [], []
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
            if v.get("status") == "needs_google" and level not in LEVELS:
                cur = cache.get(k) or {}
                # a fresh judged verdict stays; an unjudged (legacy) one gives way, so a re-check can reopen it
                if not (fresh(cur) and cur.get("method") in METHODS):
                    cache[k] = {"company": v.get("company"), "level": None, "status": "needs_google",
                                "checked": v.get("checked") or TODAY, "engine": v.get("engine", ""),
                                "detail": (v.get("detail") or "")[:300]}
                    fallback.append(v.get("company"))
                continue
            if level not in LEVELS:
                blocked.append(f'{v.get("company")}: {v.get("status") or "no verdict"}')
                continue
            ev = [e for e in v.get("evidence") or [] if e.get("url")][:5]
            if level != "none" and not ev:
                level = "none"  # a positive level needs a search result behind it
            cache[k] = {"company": v.get("company"), "level": level, "checked": v.get("checked") or TODAY,
                        "engine": v.get("engine", ""), "method": v.get("method") if v.get("method") in METHODS else "legacy",
                        "queries": v.get("queries_run") or [], "evidence": ev,
                        "same_company": v.get("same_company", ""), "detail": (v.get("detail") or "")[:300]}
            got[level] = got.get(level, 0) + 1
    write_json(path, cache)
    return {"ingested": got, "needs_google": fallback[:40], "not_cached": blocked[:20], "cache": len(cache)}


# ------------------------------------------------------------ employer harvest
# The other direction: profiles of engineers in the candidate's country, read for their current employer. A weekly
# side source (about 15 employers per 60 profiles in the 2026-10-14 test). The employers go to
# <home>/region_employers.json, which recruiter-outreach's funding research reads; they are never written into
# people_checks.json, because a profile's employer name can be a namesake of a company we know.

HARVEST_DAYS = 7


def harvest(home: str, out_dir: Path, pages: int = 3, force: bool = False) -> dict:
    state_path = js.fix_path(home) / "search_state.json"
    state = read_json(state_path, {"backends": {}})
    last = state.get("harvest_last", "")
    if not force and last >= (NOW - timedelta(days=HARVEST_DAYS)).isoformat():
        return {"due": False, "last": last[:10]}
    cand = userconfig.snapshot()
    own = COUNTRY_SUBDOMAIN.get((cand.get("country") or "").lower(), "")
    terms = [t for t in ["AI Engineer", "Machine Learning Engineer", "LLM Engineer", "GenAI Engineer", "Applied AI"]]
    searcher = Searcher(home, budget=len(terms) * pages + 2)
    if not searcher.canary():
        return {"due": True, "search": searcher.stats, "note": "search engines look blocked"}
    serps = []
    for t in terms:
        for p in range(1, pages + 1):
            res = searcher.query(f'site:{own}.linkedin.com/in "{t}"', page=p)
            serps.append({"q": f'site:{own}.linkedin.com/in "{t}"', "page": p, "engine": res.get("engine", ""),
                          "results": [{k: (r.get(k) or "")[:400] for k in ("title", "href", "body")} for r in res.get("results") or []]})
            if searcher.halted:
                break
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = NOW.strftime("%H%M")
    bf, of = out_dir / f"harvest_batch_{stamp}.json", out_dir / f"harvest_out_{stamp}.jsonl"
    bf.write_text(json.dumps({"country": cand.get("country"), "serps": serps}, indent=1, ensure_ascii=False), encoding="utf-8")
    state = read_json(state_path, {"backends": {}})  # the searcher may have updated it
    state["harvest_last"] = NOW.isoformat()
    write_json(state_path, state)
    return {"due": True, "batch": host_path(bf), "out": host_path(of), "profiles": sum(len(s["results"]) for s in serps),
            "search": searcher.stats}


def ingest_harvest(home: str, files: list[Path]) -> dict:
    path = js.fix_path(home) / "region_employers.json"
    reg = read_json(path, {})
    added = 0
    for f in files:
        for line in f.read_text(encoding="utf-8-sig").splitlines() if f.exists() else []:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            name = (d.get("employer") or "").strip()
            k = key_of(name)
            if not k or name_type(name):
                continue
            e = reg.setdefault(k, {"name": name, "first_seen": TODAY, "profiles": []})
            added += not e["profiles"]
            e["last_seen"] = TODAY
            if d.get("profile_url") and d["profile_url"] not in {p.get("url") for p in e["profiles"]}:
                e["profiles"].append({"url": d["profile_url"], "title": (d.get("title") or "")[:160],
                                      "location": d.get("location", "")})
                e["profiles"] = e["profiles"][-5:]
    write_json(path, reg)
    return {"employers": len(reg), "new": added}


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
    print(json.dumps(people_queue(args.home, wanted, args.limit, out, mode=args.mode), indent=1, ensure_ascii=False))
    return 0


def cmd_people_ingest(args) -> int:
    import enrich
    d = js.fix_path(args.dir) if args.dir else js.fix_path(args.home) / "runs" / f"{enrich.today()}-explore"
    print(json.dumps(ingest_people(args.home, sorted(d.glob("people_out_*.jsonl"))), indent=1, ensure_ascii=False))
    return 0


def cmd_search(args) -> int:
    """One query through the search layer (for a look by hand, or a health check)."""
    s = Searcher(args.home, budget=3)
    res = s.query(args.query, page=args.page, use_cache=not args.no_cache)
    print(json.dumps({"engine": res.get("engine"), "cached": res.get("cached"), "error": res.get("error"),
                      "results": res.get("results"), "stats": s.stats}, indent=1, ensure_ascii=False))
    return 0


def cmd_people_harvest(args) -> int:
    import enrich
    out = js.fix_path(args.home) / "runs" / f"{enrich.today()}-explore"
    print(json.dumps(harvest(args.home, out, args.pages, args.force), indent=1, ensure_ascii=False))
    return 0


def cmd_people_harvest_ingest(args) -> int:
    import enrich
    d = js.fix_path(args.dir) if args.dir else js.fix_path(args.home) / "runs" / f"{enrich.today()}-explore"
    print(json.dumps(ingest_harvest(args.home, sorted(d.glob("harvest_out_*.jsonl"))), indent=1))
    return 0


def add_commands(sub) -> None:
    home_default = os.environ.get("JOBSWEEP_HOME") or userconfig.home()
    p = sub.add_parser("boards", help="find companies' ATS boards from role links and read them (boards.json)")
    p.add_argument("--home", default=home_default)
    p.add_argument("--max-age-hours", type=float, default=20)
    p.add_argument("--batches", type=int, default=14)
    p.add_argument("--budget", type=float, default=0, help="stop starting board reads after this many seconds")
    p.set_defaults(fn=cmd_boards)
    p = sub.add_parser("people-queue", help="people-check batches: search + judge (default) or the Chrome fallback")
    p.add_argument("--home", default=home_default)
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--mode", choices=["search", "chrome"], default="search")
    p.add_argument("--companies", type=lambda s: [x.strip() for x in s.split(",") if x.strip()])
    p.set_defaults(fn=cmd_people_queue)
    p = sub.add_parser("people-ingest", help="fold people_out_*.jsonl into people_checks.json")
    p.add_argument("--home", default=home_default)
    p.add_argument("--dir", help="folder with people_out_*.jsonl (default: today's explore folder)")
    p.set_defaults(fn=cmd_people_ingest)
    p = sub.add_parser("search", help="one query through the search layer (ddgs, cached, paced)")
    p.add_argument("--home", default=home_default)
    p.add_argument("query")
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--no-cache", action="store_true")
    p.set_defaults(fn=cmd_search)
    p = sub.add_parser("people-harvest", help="weekly: employers of engineers in the candidate's country (batch for references/people-harvest.md)")
    p.add_argument("--home", default=home_default)
    p.add_argument("--pages", type=int, default=3)
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_people_harvest)
    p = sub.add_parser("people-harvest-ingest", help="fold harvest_out_*.jsonl into region_employers.json")
    p.add_argument("--home", default=home_default)
    p.add_argument("--dir")
    p.set_defaults(fn=cmd_people_harvest_ingest)
