#!/usr/bin/env python3
"""jobsweep - a role-agnostic sweep of the job sources in ../sources.json.

Standard library only, so it runs on any python3 >= 3.11 with no venv.

    jobsweep.py plan       --profile ai-engineer --days 2     # browser-lane URLs
    jobsweep.py http       --profile ai-engineer --days 2     # feeds + APIs -> http.jsonl
    jobsweep.py merge      --profile ai-engineer --days 2     # all *.jsonl -> results.{csv,md}
    jobsweep.py test-match --profile ai-engineer [ids...]     # tune the title regexes
    jobsweep.py config     [--set '{"days": 3}']              # per-user preferences
    jobsweep.py jds | enrich-queue | explorer                 # step 8, see enrich.py

Every command takes --home (default $JOBSWEEP_HOME or ~/jobsweep). A run's files
live in <home>/runs/<YYYY-MM-DD>-<profile>/, and the seen-before history that
marks rows as new lives in <home>/history/<profile>.json. Per-user preferences
(<home>/config.json) and custom role profiles (<home>/profiles/) also live in
<home>, so the skill folder itself can be shared unchanged.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import email.utils
import html
import http.cookiejar
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from html.entities import name2codepoint
from pathlib import Path

SKILL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import userconfig  # noqa: E402  (config.yaml at the repo root)
CATALOGUE = SKILL / "sources.json"
PROFILES = SKILL / "profiles"
NOW = datetime.now(timezone.utc)
# Source ids that stopped early because the --budget deadline passed.
PARTIAL: set[str] = set()


class SourceError(RuntimeError):
    """A fetch or parse failed - distinct from a source that is legitimately empty."""


# ------------------------------------------------------------------ plumbing


def fix_path(value: str) -> Path:
    """Accept C:\\... paths when running under WSL."""
    match = re.match(r"^([A-Za-z]):[\\/](.*)$", value or "")
    if os.name != "nt" and match:
        value = f"/mnt/{match[1].lower()}/{match[2].replace(chr(92), '/')}"
    return Path(value).expanduser()


def load_catalogue() -> dict:
    return json.loads(CATALOGUE.read_text(encoding="utf-8"))


# Per-user preferences written by the skill's setup mode to <home>/config.json.
# They live outside the skill folder so the skill can be shared without them.
CONFIG_DEFAULTS = {
    "profile": "ai-engineer",
    "days": 2,
    "mode": "standard",
    "remote_only": True,
    "max_new_roles": 0,
    "report_rows": 15,
    "country_check": "on_request",
    "country_check_limit": 20,
    "skip_sources": [],
    "skip_hosts": [],
    "skip_tasks": [],
    "python": "",
    # Step 8 (enrich + explorer). explorer: full | table | off. resume: path to the YAML/PDF/MD resume
    # used for the fit rating ("" = no fit rating). explorer_url: the artifact the page is republished to.
    "explorer": "full",
    "resume": "",
    "explorer_url": "",
}
BROWSER_TASKS = {"linkedin_jobs": "LinkedIn Jobs search", "linkedin_posts": "LinkedIn hiring posts",
                 "rocketship": "Remote Rocketship", "wellfound": "Wellfound (signed-in Chrome)",
                 "yc_waas": "YC Work at a Startup search (signed-in Chrome)"}


def config_path(home: str) -> Path:
    return fix_path(home) / "config.json"


STATE_KEYS = ("explorer_url",)  # the only keys <home>/config.json still holds; everything else is config.yaml


def load_config(home: str) -> dict:
    """config.yaml's sweep/role/resume sections, with CONFIG_DEFAULTS underneath and the per-home
    state file (explorer_url) on top."""
    cfg = userconfig.load()
    merged = {**CONFIG_DEFAULTS, **(cfg.get("sweep") or {}), "python": cfg.get("python", ""),
              "profile": (cfg.get("role") or {}).get("profile") or CONFIG_DEFAULTS["profile"],
              "resume": (cfg.get("resume") or {}).get("base", ""), "role_title": (cfg.get("role") or {}).get("title", ""),
              "country": userconfig.country(), "region": userconfig.region()}
    path = config_path(home)
    saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    merged.update({k: v for k, v in saved.items() if k in STATE_KEYS})
    return merged


def profile_dirs(home: str) -> list[Path]:
    """User profiles in <home>/profiles shadow the bundled examples."""
    return [fix_path(home) / "profiles", PROFILES]


def load_profile(name: str, home: str = "~/jobsweep") -> dict:
    path = fix_path(name)
    if not path.suffix:
        path = next((d / f"{name}.json" for d in profile_dirs(home) if (d / f"{name}.json").exists()),
                    PROFILES / f"{name}.json")
    if not path.exists():
        names = sorted({p.stem for d in profile_dirs(home) for p in d.glob("*.json") if not p.stem.startswith("_")})
        sys.exit(f"profile not found: {path} (available: {', '.join(names)})")
    profile = json.loads(path.read_text(encoding="utf-8"))
    profile.setdefault("slug", path.stem)
    config = load_config(home)
    for key in ("skip_sources", "skip_hosts", "skip_tasks"):
        profile[key] = sorted(set(profile.get(key) or []) | set(config.get(key) or []))
    if not config.get("remote_only", True):
        profile.setdefault("linkedin", {})["remote"] = False
    profile["_include"] = [re.compile(p, re.I) for p in profile.get("include", [])]
    profile["_exclude"] = [re.compile(p, re.I) for p in profile.get("exclude", [])]
    profile["_loc_exclude"] = [re.compile(p, re.I) for p in profile.get("location_exclude", [])]
    profile["_home"] = home
    return profile


def run_dir(args, profile: dict) -> Path:
    home = fix_path(args.home)
    path = home / "runs" / f"{NOW:%Y-%m-%d}-{profile['slug']}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def window(days: float) -> tuple[datetime, datetime]:
    return NOW - timedelta(days=days), NOW


def title_ok(profile: dict, title: str) -> bool:
    if any(p.search(title) for p in profile["_exclude"]):
        return False
    return not profile["_include"] or any(p.search(title) for p in profile["_include"])


def location_ok(profile: dict, location: str) -> bool:
    return not any(p.search(location or "") for p in profile["_loc_exclude"])


# --------------------------------------------------------------------- dates

_REL = re.compile(r"(\d+)\s*\+?\s*(minute|min|hour|hr|h|day|d|week|wk|w|month|mo|year|yr|y)s?\b\s*(ago)?", re.I)
_UNITS = {"minute": 60, "min": 60, "hour": 3600, "hr": 3600, "h": 3600, "day": 86400,
          "d": 86400, "week": 604800, "wk": 604800, "w": 604800, "month": 2592000,
          "mo": 2592000, "year": 31536000, "yr": 31536000, "y": 31536000}


def parse_date(value) -> tuple[datetime | None, bool]:
    """-> (datetime in UTC, date_only). Unparseable or empty -> (None, False)."""
    if value in (None, ""):
        return None, False
    text = str(value).strip()
    if re.fullmatch(r"\d{9,13}(\.\d+)?", text):
        number = float(text)
        return datetime.fromtimestamp(number / 1000 if number > 1e11 else number, timezone.utc), False
    low = text.lower()
    if low in ("today", "just now", "now", "new"):
        return NOW, False
    if low == "yesterday":
        return NOW - timedelta(days=1), True
    rel = _REL.fullmatch(low) or (_REL.search(low) if "ago" in low else None)
    if rel:
        return NOW - timedelta(seconds=int(rel[1]) * _UNITS[rel[2].lower()]), False
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        date_only = bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", text))
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)), date_only
    except ValueError:
        pass
    try:
        parsed = email.utils.parsedate_to_datetime(text)
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)), False
    except (TypeError, ValueError, IndexError):
        return None, False


def in_window(posted, start: datetime) -> tuple[bool, str]:
    """Keep rows inside the window and rows whose date is unknown."""
    parsed, date_only = parse_date(posted)
    if parsed is None:
        return True, ""
    parsed = parsed.astimezone(timezone.utc)
    iso = parsed.strftime("%Y-%m-%d") if date_only else parsed.strftime("%Y-%m-%dT%H:%MZ")
    keep = parsed.date() >= start.date() if date_only else parsed >= start
    return keep, iso


# ---------------------------------------------------------------- http layer


class Fetcher:
    """Per-HOST pacing: the eight Getro networks are one host."""

    def __init__(self, user_agent: str):
        self.user_agent = user_agent
        self._locks: dict[str, threading.Lock] = {}
        self._last: dict[str, float] = {}
        self._guard = threading.Lock()

    def _pace(self, url: str, interval: float) -> threading.Lock:
        host = urllib.parse.urlsplit(url).hostname or url
        with self._guard:
            lock = self._locks.setdefault(host, threading.Lock())
        lock.acquire()
        wait = interval - (time.time() - self._last.get(host, 0.0))
        if wait > 0:
            time.sleep(min(wait, 60))
        self._last[host] = time.time()
        lock.release()
        return lock

    def get(self, url: str, *, interval: float = 0, method: str = "GET", body=None,
            headers: dict | None = None, opener=None) -> str:
        self._pace(url, interval)
        head = {"User-Agent": self.user_agent,
                "Accept": "application/json, application/rss+xml, text/xml, */*"}
        head.update(headers or {})
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            head.setdefault("Content-Type", "application/json")
        request = urllib.request.Request(url, data=data, headers=head, method=method)
        try:
            with (opener or urllib.request.build_opener()).open(request, timeout=30) as response:
                raw = response.read()
                charset = response.headers.get_content_charset() or "utf-8"
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise SourceError(f"{type(error).__name__}: {error}") from error
        return raw.decode(charset, errors="replace")

    def json(self, url: str, **kw):
        text = self.get(url, **kw)
        try:
            return json.loads(text)
        except json.JSONDecodeError as error:
            raise SourceError(f"not JSON ({error}); starts {text[:80]!r}") from error


def dig(payload, path: str):
    if not path:
        return payload
    current = payload
    for segment in path.split("."):
        if current is None:
            return None
        if segment.isdigit() and isinstance(current, list):
            index = int(segment)
            current = current[index] if index < len(current) else None
        elif isinstance(current, dict):
            current = current.get(segment)
        else:
            return None
    return current


def _demojibake(text: str) -> str:
    if not text or not any("À" <= c <= "ÿ" for c in text):
        return text
    try:
        repaired = text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text
    return repaired if "\ufffd" not in repaired else text


def text_of(value) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return _demojibake(html.unescape(value).strip())
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return ", ".join(t for t in (text_of(v) for v in value) if t)[:200]
    if isinstance(value, dict):
        for key in ("name", "label", "title", "text"):
            if key in value:
                return text_of(value[key])
    return ""


def row(src: dict, title, company, location, posted, url, **extra) -> dict:
    if src.get("sector"):  # e.g. web3: the autopilot's pay estimate treats the sector as a global-USD lane
        extra.setdefault("sector", src["sector"])
    return {"source": src["id"], "source_label": src["label"],
            "title": text_of(title)[:200], "company": text_of(company)[:120],
            "location": text_of(location)[:160], "posted": text_of(posted)[:60],
            "url": text_of(url), **{k: v for k, v in extra.items() if v}}


# ------------------------------------------------------------------ adapters


def fetch_json(f: Fetcher, src: dict, profile: dict, cat: dict) -> list[dict]:
    base = src["url"]
    params = {k: profile.get(v) for k, v in (src.get("profile_params") or {}).items()}
    params = {k: v for k, v in params.items() if v}
    if params:
        base += ("&" if "?" in base else "?") + urllib.parse.urlencode(params)
    pag = src.get("pagination") or {}
    kind, pages = pag.get("kind"), int(pag.get("pages", 1))
    cursor, page_no = None, int(pag.get("start", 1))
    fmap, out = src.get("map") or {}, []
    for index in range(max(1, pages)):
        url = base
        if kind == "cursor" and cursor:
            url += ("&" if "?" in url else "?") + f"{pag.get('param', 'cursor')}={urllib.parse.quote(str(cursor))}"
        elif kind == "page" and index:
            url += ("&" if "?" in url else "?") + f"{pag.get('param', 'page')}={page_no}"
        payload = f.json(url, interval=src.get("min_interval", 0))
        items = dig(payload, src.get("items", ""))
        if not isinstance(items, list):
            raise SourceError(f"no list at {src.get('items', '')!r}; feed shape changed")
        if index == 0 and src.get("skip_first"):
            items = items[int(src["skip_first"]):]
        for item in items:
            if isinstance(item, dict):
                out.append(row(src, dig(item, fmap.get("title", "title")),
                               dig(item, fmap.get("company", "company")),
                               dig(item, fmap.get("location", "location")),
                               dig(item, fmap.get("posted", "posted")),
                               dig(item, fmap.get("url", "url")),
                               compensation=text_of(item.get("salary") or item.get("salaryRange")
                                                    or _salary(item))))
        if kind == "cursor":
            cursor = dig(payload, pag.get("cursor_at", "nextCursor"))
            if not cursor:
                break
        elif kind == "page":
            page_no += 1
            if not items:
                break
        else:
            break
    if items and not any(r["title"] for r in out):
        raise SourceError("field map matched nothing; feed shape changed")
    return out


def _salary(item: dict) -> str:
    lo = item.get("minSalary") or item.get("salary_min")
    hi = item.get("maxSalary") or item.get("salary_max")
    if lo or hi:
        cur = item.get("currency") or item.get("salaryCurrency") or ""
        return f"{lo or '?'}-{hi or '?'} {cur}".strip()
    return ""


_XML_SAFE = {"amp", "lt", "gt", "quot", "apos"}


def _numeric_entities(body: str) -> str:
    def swap(m: re.Match) -> str:
        name = m.group(1)
        if name in _XML_SAFE:
            return m.group(0)
        cp = name2codepoint.get(name)
        return f"&#{cp};" if cp else m.group(0)
    return re.sub(r"&([a-zA-Z][a-zA-Z0-9]{1,31});", swap, body)


def fetch_rss(f: Fetcher, src: dict, profile: dict, cat: dict) -> list[dict]:
    url = src["url"]
    wanted = (profile.get("rss_category") or {}).get(src["id"])
    if wanted and wanted in (src.get("rss_by_category") or {}):
        url = src["rss_by_category"][wanted]
    body = f.get(url, interval=src.get("min_interval", 0))
    try:
        root = ET.fromstring(_numeric_entities(body))
    except ET.ParseError as error:
        raise SourceError(f"unparseable feed: {error}") from error
    nodes = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
    if not nodes:
        raise SourceError("feed parsed but held no items")
    fmap, out = src.get("map") or {}, []
    for node in nodes:
        item: dict[str, str] = {}
        for child in node:
            tag = child.tag.split("}")[-1]
            value = (child.text or "").strip() or (child.attrib.get("href", "") if tag == "link" else "")
            if value and tag not in item:
                item[tag] = value
        raw = text_of(item.get(fmap.get("title", "title")))
        title, company = raw, ""
        if src.get("title_pattern"):
            m = re.match(src["title_pattern"], raw)
            if m:
                title = (m.groupdict().get("title") or raw).strip()
                company = (m.groupdict().get("company") or "").strip()
        out.append(row(src, title, item.get(fmap.get("company", "company")) or company,
                       item.get("location") or item.get("region") or "",
                       item.get(fmap.get("posted", "pubDate")) or item.get("updated") or item.get("published"),
                       item.get(fmap.get("url", "link"))))
    return out


def fetch_getro(f: Fetcher, src: dict, profile: dict, cat: dict) -> list[dict]:
    """Getro filters server-side, so this is the one family that uses the role query."""
    cfg = cat["getro"]
    url = cfg["url"].format(network_id=src["network_id"])
    queries = profile.get("api_queries") or [profile["role"]]
    queries = queries[: profile.get("_api_query_limit") or len(queries)]
    seen, out = set(), []
    for query in queries:
        for page in range(int(profile.get("getro_pages", cfg["pages"]))):
            if time.time() > profile.get("_deadline", float("inf")):
                PARTIAL.add(src["id"])
                return out
            payload = f.json(url, method="POST", interval=cfg["min_interval"],
                             headers={"Accept": "application/json"},
                             body={"hitsPerPage": 20, "page": page, "filters": "", "query": query})
            items = dig(payload, "results.jobs")
            if not isinstance(items, list):
                raise SourceError("results.jobs was not a list")
            for item in items:
                link = text_of(item.get("url"))
                if link in seen:
                    continue
                seen.add(link)
                out.append(row(src, item.get("title"), dig(item, "organization.name"),
                               item.get("locations"), item.get("created_at"), link,
                               compensation=_salary(item)))
            if len(items) < 20:
                break
    return out


_W3_ROW = re.compile(r'<tr[^>]*class="[^"]*table_row[^"]*"(.*?)</tr>', re.S)
_W3_TITLE = re.compile(r'href="(/[^"]+/\d+)"[^>]*>\s*<h2[^>]*>\s*(.*?)\s*</h2>', re.S)
_W3_COMPANY = re.compile(r'<h3[^>]*>\s*(.*?)\s*</h3>', re.S)
_W3_LOC = re.compile(r'class=job-location-mobile>(.*?)</span>\s*</div>', re.S)
_W3_TIME = re.compile(r'<time datetime="([^"]+)"')
_W3_PAY = re.compile(r'class="[^"]*text-salary[^"]*"[^>]*>\s*(.*?)\s*</p>', re.S)


def fetch_web3career(f: Fetcher, src: dict, profile: dict, cat: dict) -> list[dict]:
    """web3.career category pages (server-rendered table rows). Estimated salaries (the 'estimated_star' marker) are
    left out: only pay the posting states is pay."""
    seen, out = set(), []
    for path in src.get("pages") or ["/ai-jobs?remote=true"]:
        for page in range(1, int(src.get("page_count", 1)) + 1):
            if time.time() > profile.get("_deadline", float("inf")):
                PARTIAL.add(src["id"])
                return out
            url = f"{src['url']}{path}{'&' if '?' in path else '?'}page={page}" if page > 1 else f"{src['url']}{path}"
            body = f.get(url, interval=src.get("min_interval", 0),
                         headers={"User-Agent": cat.get("browser_user_agent") or f.user_agent, "Accept": "text/html"})
            rows = _W3_ROW.findall(body)
            if not rows:
                if page == 1:
                    raise SourceError(f"no job rows on {url}")
                break
            for chunk in rows:
                m = _W3_TITLE.search(chunk)
                if not m:
                    continue
                link = src["url"] + m.group(1)
                if link in seen:
                    continue
                seen.add(link)
                company = _W3_COMPANY.search(chunk)
                loc = _W3_LOC.search(chunk)
                location = re.sub(r"<[^>]+>", " ", loc.group(1)) if loc else ""
                location = re.sub(r"\s*,\s*", ", ", re.sub(r"\s+", " ", location.replace("\U0001F4CD", ""))).strip(" ,")
                when = _W3_TIME.search(chunk)
                pay = _W3_PAY.search(chunk)
                comp = "" if (not pay or "estimated_star" in chunk) else re.sub(r"<[^>]+>", "", pay.group(1)).strip()
                out.append(row(src, m.group(2), company.group(1) if company else "", location,
                               when.group(1) if when else "", link, compensation=comp))
            if len(rows) < 15:
                break
    return out


def fetch_consider(f: Fetcher, src: dict, profile: dict, cat: dict) -> list[dict]:
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    page = f.get(src["url"], interval=src.get("min_interval", 0), opener=opener,
                 headers={"Accept": "text/html"})
    token = re.search(r'"csrfToken"\s*:\s*"([^"]+)"', page)
    if not token:
        raise SourceError("no csrfToken on the board page; the site changed")
    payload = f.json(src["api"], method="POST", interval=src.get("min_interval", 0), opener=opener,
                     headers={"X-CSRF-Token": token[1], "Accept": "application/json"},
                     body={"meta": {"size": int(src.get("size", 400))},
                           "board": {"id": src.get("board_id"), "isParent": True},
                           "query": {}, "grouped": False})
    items = payload.get("jobs") if isinstance(payload, dict) and "jobs" in payload else (payload or {}).get("results")
    if not isinstance(items, list):
        raise SourceError("no job list in the response")
    return [row(src, i.get("title") or i.get("name"),
                i.get("companyName") or i.get("company") or i.get("companyDomain"),
                i.get("locations") or i.get("normalizedLocations") or i.get("location"),
                i.get("timeStamp") or i.get("postedAt") or i.get("createdAt"),
                i.get("applyUrl") or i.get("url"),
                compensation=_consider_salary(i.get("salary")))
            for i in items if isinstance(i, dict)]


def _consider_salary(value) -> str:
    if not isinstance(value, dict):
        return ""
    lo, hi = value.get("minValue") or value.get("min"), value.get("maxValue") or value.get("max")
    if not (lo or hi):
        return ""
    cur = value.get("currency") or ""
    if isinstance(cur, dict):
        cur = cur.get("label") or cur.get("value") or ""
    period = value.get("period")
    if isinstance(period, dict):
        period = period.get("label")
    return f"{lo or '?'}-{hi or '?'} {cur}/{period or 'year'}".strip()


_HN_PLACE = re.compile(r"(?i)\b(remote|onsite|on-site|in-office|hybrid|relocat|worldwide|global|anywhere|"
                       r"us|usa|u\.s\.|uk|eu|europe|emea|apac|canada|latam|brazil|india|germany|berlin|"
                       r"london|nyc|new york|san francisco|sf|bay area|seattle|boston|austin|timezone|time zone|"
                       r"pst|est|cet|utc)\b")
_HN_NOISE = re.compile(r"(?i)^(full[- ]?time|part[- ]?time|contract(or)?|intern(ship)?|visa|no visa|"
                       r"sponsor\w*|salary.*|\$[\d,k]+.*|equity.*|https?://\S+)$")


def fetch_hn(f: Fetcher, src: dict, profile: dict, cat: dict) -> list[dict]:
    """Hacker News 'Who is hiring?' threads through the Algolia API. Each top-level comment is
    one posting; the first line is conventionally 'Company | Role | Location | REMOTE | ...'."""
    base = src["url"].rstrip("/")
    gap = src.get("min_interval", 1)
    stories = f.json(f"{base}/search_by_date?tags=story,author_whoishiring&hitsPerPage=8", interval=gap)
    threads = [h for h in stories.get("hits", []) if re.match(r"(?i)ask hn: who is hiring\?", h.get("title") or "")]
    threads = threads[: int(src.get("threads", 2))]
    if not threads:
        raise SourceError("no 'Who is hiring?' thread found; the Algolia feed changed")
    out = []
    for story in threads:
        sid = int(story["objectID"])
        month = story["title"].split("(")[-1].rstrip(")")
        for page in range(int(src.get("pages", 3))):
            payload = f.json(f"{base}/search_by_date?tags=comment,story_{sid}&hitsPerPage=1000&page={page}",
                             interval=gap)
            for c in payload.get("hits", []):
                if c.get("parent_id") != sid:
                    continue
                raw = c.get("comment_text") or ""
                text = html.unescape(re.sub(r"<[^>]+>", "\n", raw.replace("<p>", "\n")))
                lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
                if not lines:
                    continue
                parts = [x.strip(" -–") for x in re.split(r"\s*\|\s*", lines[0]) if x.strip(" -–")]
                if len(parts) < 2:
                    continue  # prose post without the Company | Role | ... header
                company, rest = parts[0], parts[1:]
                place = [x for x in rest if _HN_PLACE.search(x)
                         and not re.search(r"(?i)engineer|developer|scientist|architect|manager", x)]
                role = [x for x in rest if x not in place and not _HN_NOISE.match(x)]
                title = " / ".join(role).strip(" /")[:200] or lines[0][:200]
                link = re.search(r'href="(https?://[^"]+)"', raw)
                out.append(row(src, title, company, " | ".join(place), c.get("created_at"),
                               f"https://news.ycombinator.com/item?id={c['objectID']}",
                               apply_url=html.unescape(link.group(1)) if link else "",
                               eligibility=" | ".join(place), signal=f"HN {month}"))
            if page + 1 >= int(payload.get("nbPages") or 1):
                break
    return out


def _inertia_props(body: str) -> dict:
    m = re.search(r'data-page="([^"]+)"', body)
    if not m:
        raise SourceError("no data-page JSON on the page; the site changed")
    return json.loads(html.unescape(m.group(1))).get("props") or {}


def fetch_yc(f: Fetcher, src: dict, profile: dict, cat: dict) -> list[dict]:
    """Y Combinator postings from the public pages that embed Work at a Startup data as Inertia JSON.

    Checked 2026-10-15: ycombinator.com/jobs ignores its query, page and remote parameters and always embeds the
    same 20 featured jobs, each /jobs/role/<slug>[/remote] page a fixed sample of about 30, and the anonymous
    workatastartup.com/jobs page 30 more. So this reads a fixed list of pages (`pages`, relative to `url`, plus
    `waas`) and merges them; the full search of the ~3,000 engineering jobs needs the user's signed-in session,
    which is the browser task `yc_waas`. YC postings stay open for months, hence `window_days`."""
    base = src["url"].rstrip("/")
    gap = src.get("min_interval", 1)
    seen, out = set(), []
    for page in src.get("pages") or ["/jobs?role=eng&remote=yes"]:
        body = f.get(base + page, interval=gap, headers={"Accept": "text/html"})
        jobs = _inertia_props(body).get("jobPostings")
        if not isinstance(jobs, list):
            raise SourceError("props.jobPostings missing; the page shape changed")
        for j in jobs:
            path = j.get("url") or ""
            if not path or path in seen:
                continue
            seen.add(path)
            age = re.sub(r"(?i)^(about|over|almost|less than)\s+", "", (j.get("createdAt") or "").strip())
            posted = age if (not age or age.endswith("ago")) else f"{age} ago"
            batch = j.get("companyBatchName") or ""
            out.append(row(src, j.get("title"), j.get("companyName"), j.get("location"), posted,
                           "https://www.workatastartup.com" + path,
                           compensation=text_of(j.get("salaryRange")), signal=f"YC {batch}".strip()))
    if src.get("waas"):
        body = f.get(src["waas"], interval=gap, headers={"Accept": "text/html"})
        for j in _inertia_props(body).get("jobs") or []:
            jid = j.get("id")
            path = f"/jobs/{jid}" if jid else ""
            if not path or path in seen:
                continue
            seen.add(path)
            batch = j.get("companyBatch") or ""
            out.append(row(src, j.get("title"), j.get("companyName"), j.get("location"), "",
                           "https://www.workatastartup.com" + path,
                           compensation=text_of(j.get("salary")), signal=f"YC {batch}".strip()))
    return out


_A16Z_ARTICLE = re.compile(r"<article\b.*?</article>", re.S)
_A16Z_COMPANY = re.compile(r'href="/jobs/([^/"]+)"[^>]*>([^<]+)</a>')
_A16Z_TITLE = re.compile(r'<h2[^>]*>\s*<a[^>]+href="(/jobs/[^"]+)"[^>]*>(.*?)</a>', re.S)
_A16Z_TIME = re.compile(r'<time[^>]+dateTime="([^"]+)"')
_A16Z_PAY = re.compile(r"(\$[\d.,]+[KkMm]?\s*[–-]\s*\$[\d.,]+[KkMm]?|\$[\d.,]+[KkMm]?)")
_A16Z_META = re.compile(r'<div class="mt-1.5 flex flex-wrap[^"]*">(.*?)</div>', re.S)
_A16Z_APPLY = re.compile(r'<a href="(https?://[^"]+)"[^>]*target="_blank"[^>]*>.*?Apply', re.S)


def fetch_a16z(f: Fetcher, src: dict, profile: dict, cat: dict) -> list[dict]:
    """Andreessen Horowitz's portfolio job board (jobs.a16z.com), server-rendered: one search per api_query, 25
    results by relevance (checked 2026-10-15: no working page parameter; `start` and `p` return mostly the same
    25). Each card carries the posting's own date, location, pay and the company's ATS apply link."""
    queries = profile.get("api_queries") or [profile["role"]]
    queries = queries[: profile.get("_api_query_limit") or len(queries)]
    base = src["url"].rstrip("/")
    seen, out = set(), []
    for query in queries:
        body = f.get(f"{base}/jobs?" + urllib.parse.urlencode({"q": query}), interval=src.get("min_interval", 2),
                     headers={"Accept": "text/html"})
        articles = _A16Z_ARTICLE.findall(body)
        if not articles and "<article" not in body:
            raise SourceError("no job cards on the page; the site changed")
        for art in articles:
            t = _A16Z_TITLE.search(art)
            if not t:
                continue
            path, title = t.group(1), re.sub(r"<[^>]+>", "", t.group(2))
            if path in seen:
                continue
            seen.add(path)
            c = _A16Z_COMPANY.search(art)
            company = html.unescape(c.group(2)) if c else path.split("/")[2].replace("-", " ").title()
            when = _A16Z_TIME.search(art)
            # the first meta line is "pay • Offers Equity · location · Posted N days ago", any part optional; a long
            # location is cut with "…" and carried in full in the span's title attribute
            meta = _A16Z_META.search(art)
            parts = [re.sub(r"\s+", " ", x).strip() for x in re.sub(r"<[^>]+>", " ", meta.group(1)).split("·")] if meta else []
            pay = next((m.group(1) for m in map(_A16Z_PAY.search, parts) if m), "")
            loc = next((x for x in parts if x and not x.startswith("$") and not x.lower().startswith("posted")), "")
            full = re.search(r'<span title="([^"]+)">', meta.group(1)) if meta else None
            if full and "…" in loc:
                loc = full.group(1)
            apply_link = _A16Z_APPLY.search(art)
            out.append(row(src, html.unescape(title), company, html.unescape(loc),
                           when.group(1) if when else "", base + path,
                           compensation=pay,
                           apply_url=html.unescape(apply_link.group(1)) if apply_link else "",
                           signal="a16z portfolio"))
    return out


def fetch_company_boards(f: Fetcher, src: dict, profile: dict, cat: dict) -> list[dict]:
    """Postings like the target role on companies' own ATS boards (<home>/boards.json, see companies.py): boards found
    from earlier role links and from recruiter-outreach's funded-company registry, re-read through the ATS APIs when
    older than `max_age_hours`. Each row carries the board's own posted date, location and pay. Onsite postings are
    left out; region labels are kept as hints only."""
    import companies
    home = profile.get("_home") or userconfig.home()
    companies.discover_boards(home)
    companies.refresh_boards(home, float(src.get("max_age_hours", 20)), profile.get("_deadline"))
    out = []
    for e in companies.boards(home).values():
        for x in e.get("roles") or []:
            if not x.get("target") or x.get("region") == "onsite":
                continue
            pay = x.get("pay_usd_month")
            out.append(row(src, x.get("title"), e.get("name"), x.get("location"), x.get("posted"), x.get("url"),
                           compensation=f"~USD {pay:,}/month (board)" if pay else "",
                           region_hint=x.get("region"), board=f'{e["ats"]["kind"]}:{e["ats"]["slug"]}',
                           sector=e.get("sector", "")))
    return out


ADAPTERS = {"json": fetch_json, "rss": fetch_rss, "getro": fetch_getro, "consider": fetch_consider,
            "hn": fetch_hn, "yc": fetch_yc, "a16z": fetch_a16z, "boards": fetch_company_boards,
            "web3career": fetch_web3career}


def sweep(cat: dict, profile: dict, ids: list[str] | None):
    """Yield (source, rows, error) as each source FINISHES, not in catalogue order,
    so the fast feeds report in seconds while the serialised Getro calls continue."""
    fetcher = Fetcher(cat["user_agent"])
    skip = set(profile.get("skip_sources") or [])
    sources = [s for s in cat["http"] if (s["id"] in ids if ids else s["id"] not in skip)]

    def one(src):
        try:
            return src, ADAPTERS[src["kind"]](fetcher, src, profile, cat), ""
        except SourceError as error:
            return src, [], str(error)
        except Exception as error:  # a broken source must not sink the sweep
            return src, [], f"{type(error).__name__}: {error}"

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for future in concurrent.futures.as_completed([pool.submit(one, s) for s in sources]):
            yield future.result()


# ------------------------------------------------------------------ commands


def cmd_http(args) -> int:
    cat, profile = load_catalogue(), load_profile(args.profile, args.home)
    profile["_api_query_limit"] = args.api_queries
    if args.budget:
        profile["_deadline"] = time.time() + args.budget
    start, _ = window(args.days)
    out_dir = run_dir(args, profile)
    target = out_dir / "http.jsonl"
    stamp = NOW.strftime("%Y-%m-%dT%H:%MZ")
    total = 0
    print(f"{'source':<22} {'kept':>5} {'offered':>8} {'title-ok':>9}  note")
    print("-" * 70)
    with target.open("w", encoding="utf-8") as fh:
        for src, rows, error in sweep(cat, profile, args.sources):
            # one funnel line per source: what it offered and what each filter removed (read by merge and by
            # job-autopilot's run report)
            funnel = {"kind": "funnel", "source": src["id"], "label": src["label"], "lane": "http",
                      "query": src.get("url", ""), "fetched": len(rows), "title_ok": 0, "in_window": 0, "kept": 0,
                      "status": "ok", "error": ""}
            if error:
                print(f"{src['id']:<22} {'-':>5} {'-':>8} {'-':>9}  FAILED: {error[:60]}", flush=True)
                fh.write(json.dumps({"kind": "coverage", "source": src["id"], "complete": False,
                                     "error": error}) + "\n")
                fh.write(json.dumps({**funnel, "status": "failed", "error": error[:300]}) + "\n")
                continue
            # a source may look further back than the sweep (company boards: a target company's role posted
            # three weeks ago is still worth an application); merge honours the row's own window_start
            src_start = window(max(args.days, float(src.get("window_days") or 0)))[0]
            title_hits = in_win = kept = 0
            for r in rows:
                if not r["title"] or not title_ok(profile, r["title"]):
                    continue
                title_hits += 1
                ok, iso = in_window(r["posted"], src_start)
                if not ok:
                    continue
                in_win += 1
                if not location_ok(profile, r["location"]):
                    continue
                r.update(posted_iso=iso, captured=stamp)
                if src_start < start:
                    r["window_start"] = src_start.strftime("%Y-%m-%dT%H:%MZ")
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
                kept += 1
            total += kept
            note = src.get("attribution", "")
            funnel.update(title_ok=title_hits, in_window=in_win, kept=kept)
            if src["id"] in PARTIAL:
                note = "PARTIAL: stopped at --budget"
                funnel.update(status="partial", error="stopped at the time budget")
                fh.write(json.dumps({"kind": "coverage", "source": src["id"], "complete": False,
                                     "error": "stopped at the time budget"}) + "\n")
            fh.write(json.dumps(funnel) + "\n")
            fh.flush()
            print(f"{src['id']:<22} {kept:>5} {len(rows):>8} {title_hits:>9}  {note}", flush=True)
    print(f"\n{total} rows -> {target}")
    return 0


def google_url(query: str, hosts: list[dict], start: datetime, end: datetime) -> str:
    sites = " OR ".join(f"site:{h['site']}" for h in hosts)
    tbs = f"cdr:1,cd_min:{start:%m/%d/%Y},cd_max:{end:%m/%d/%Y}"
    return ("https://www.google.com/search?q=" + urllib.parse.quote_plus(f"{query} ({sites})")
            + "&tbs=" + urllib.parse.quote_plus(tbs))


def linkedin_jobs_url(profile: dict, keywords: str, days: float) -> str:
    params = {"keywords": keywords, "f_TPR": f"r{int((days) * 86400)}", "sortBy": "DD"}
    li = profile.get("linkedin") or {}
    if li.get("remote", True):
        params["f_WT"] = "2"
    if li.get("location"):
        params["location"] = li["location"]
    if li.get("geoId"):
        params["geoId"] = li["geoId"]
    return "https://www.linkedin.com/jobs/search/?" + urllib.parse.urlencode(params)


def linkedin_posts_url(keywords: str, days: float) -> str:
    bucket = "past-24h" if days <= 1 else "past-week" if days <= 7 else "past-month"
    return ("https://www.linkedin.com/search/results/content/?" + urllib.parse.urlencode(
        {"keywords": keywords, "origin": "FACETED_SEARCH",
         "sortBy": '["date_posted"]', "datePosted": f'["{bucket}"]'}))


def rocketship_url(profile: dict) -> str | None:
    rs = profile.get("rocketship") or {}
    if not rs.get("titles"):
        return None
    params = {"page": "1", "sort": "DateAdded",
              "jobTitle": ",".join(urllib.parse.quote(t) for t in rs["titles"])}
    if rs.get("locations"):
        params["locations"] = rs["locations"]
    return "https://www.remoterocketship.com/remote-jobs/?" + urllib.parse.urlencode(params)


def wellfound_roles(profile: dict) -> list[str]:
    """Wellfound role slugs: the profile's `wellfound.roles`, else the role title slugified.
    An explicit empty list turns the task off."""
    block = profile.get("wellfound")
    if block is not None and not block.get("roles", True):
        return []
    return (block or {}).get("roles") or [re.sub(r"[^a-z0-9]+", "-", profile["role"].lower()).strip("-")]


def yc_waas_queries(profile: dict) -> list[str]:
    """Search phrasings for the Work at a Startup browser task: the profile's `yc_waas.queries`, else the first
    two api_queries. An explicit empty list turns the task off."""
    block = profile.get("yc_waas")
    if block is not None and not block.get("queries", True):
        return []
    return (block or {}).get("queries") or (profile.get("api_queries") or [profile["role"]])[:2]


def cmd_plan(args) -> int:
    cat, profile = load_catalogue(), load_profile(args.profile, args.home)
    start, end = window(args.days)
    out_dir = run_dir(args, profile)
    li = profile.get("linkedin") or {}
    skip_hosts = set(profile.get("skip_hosts") or [])
    # skip_tasks holds browser task kinds (linkedin_jobs, ...), shard ids (ats_a, ...) or "google".
    skip_tasks = set(profile.get("skip_tasks") or [])
    remote = li.get("remote", True)
    tasks = []
    for kw in li.get("jobs_keywords") or [profile["role"]]:
        tasks.append({"task": "linkedin_jobs", "label": f"LinkedIn Jobs: {kw}",
                      "url": linkedin_jobs_url(profile, kw, args.days)})
    for kw in li.get("posts_keywords") or [f"{profile['role']} hiring"]:
        if not remote:
            kw = re.sub(r"\s*\bremote\b", "", kw).strip()
        tasks.append({"task": "linkedin_posts", "label": f"LinkedIn posts: {kw}",
                      "url": linkedin_posts_url(kw, args.days)})
    rs = rocketship_url(profile)
    if rs:
        tasks.append({"task": "rocketship", "label": "Remote Rocketship", "url": rs})
    for slug in wellfound_roles(profile):
        tasks.append({"task": "wellfound", "label": f"Wellfound: {slug}",
                      "url": f"https://wellfound.com/role/{'r/' if remote else ''}{slug}"})
    for query in yc_waas_queries(profile):
        # Work at a Startup's real search needs the user's signed-in session; the public pages the HTTP source
        # `yc_jobs` reads embed only fixed samples. Tested signed in on 2026-10-15: the /companies list honours
        # the query and the visa filter and scrolls through every match (353 startups for "ai engineer"), while
        # /jobs ignores the query and shows the same 30 cards. jobType=any keeps contract roles; there is no
        # remote filter, so the location text ("Remote (US)") carries it.
        params = {"role": "eng", "query": query, "sortBy": "created_desc", "layout": "list-compact", "jobType": "any",
                  "usVisaNotRequired": "true", "demographic": "any", "hasEquity": "any", "hasSalary": "any",
                  "industry": "any", "interviewProcess": "any", "tab": "any"}
        tasks.append({"task": "yc_waas", "label": f"YC Work at a Startup: {query}",
                      "url": "https://www.workatastartup.com/companies?" + urllib.parse.urlencode(params)})
    tasks = [t for t in tasks if t["task"] not in skip_tasks]
    for shard in cat["google_shards"]:
        hosts = [h for h in shard["hosts"] if h["site"] not in skip_hosts]
        if not hosts or skip_tasks & {"google", shard["id"]}:
            continue
        for query in profile.get("google_queries") or [f"{profile['role']} remote"]:
            if not remote:
                query = re.sub(r"\s*\bremote\b", "", query).strip()
            tasks.append({"task": "google_shard", "shard": shard["id"],
                          "label": f"{shard['label']} :: {query}",
                          "url": google_url(query, hosts, start, end),
                          "hosts": [h["host"] for h in hosts],
                          "date_fields": {h["host"]: h.get("field", "datePosted") for h in hosts}})
    plan = {"profile": profile["slug"], "role": profile["role"],
            "window_start": start.strftime("%Y-%m-%dT%H:%MZ"),
            "window_end": end.strftime("%Y-%m-%dT%H:%MZ"),
            "run_dir": str(out_dir), "browser_rows_file": str(out_dir / "browser.jsonl"),
            "tasks": tasks}
    (out_dir / "plan.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    print(json.dumps(plan, indent=2))
    return 0


def _norm_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url.strip())
    query = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query)
             if not k.lower().startswith(("utm_", "ref", "source", "gh_src", "trk"))]
    return urllib.parse.urlunsplit((parts.scheme.lower(), parts.netloc.lower().removeprefix("www."),
                                    parts.path.rstrip("/"), urllib.parse.urlencode(query), ""))


def _norm_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def cmd_merge(args) -> int:
    profile = load_profile(args.profile, args.home)
    start, _ = window(args.days)
    out_dir = run_dir(args, profile)
    history_path = fix_path(args.home) / "history" / f"{profile['slug']}.json"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history = json.loads(history_path.read_text(encoding="utf-8")) if history_path.exists() else {}
    today = NOW.strftime("%Y-%m-%d")

    merged: dict[str, dict] = {}
    by_pair: dict[tuple, str] = {}
    gaps, dropped = [], 0
    # per-source funnel for the run report: rows each source handed to merge and what merge did with them
    funnel: dict[str, dict] = {}
    tasks, fetched = [], {}

    def fun(label: str) -> dict:
        return funnel.setdefault(label, {"rows_in": 0, "skipped_source": 0, "location_excluded": 0, "title_dropped": 0,
                                         "outside_window": 0, "duplicate": 0, "unique": 0, "new": 0})
    for path in sorted(p for p in out_dir.glob("*.jsonl") if p.name != "verdicts.jsonl" and not p.name.startswith("people_out_")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                r = json.loads(line.lstrip("\ufeff"))
            except json.JSONDecodeError:
                print(f"skipping bad line in {path.name}: {line[:80]}", file=sys.stderr)
                continue
            if r.get("kind") == "funnel":
                fetched[r.get("label") or r.get("source")] = r
                continue
            if r.get("kind") == "coverage":
                tasks.append({**r, "file": path.name})
                if not r.get("complete", True):
                    gaps.append(r)
                continue
            if not r.get("title") or not r.get("url"):
                continue
            label = r.get("source_label") or r.get("source")
            f = fun(label)
            f["rows_in"] += 1
            # Settings changed after rows were collected still apply: skipped sources
            # and the profile's location filter are enforced here as well as at fetch time.
            if r.get("source") in profile["skip_sources"]:
                dropped += 1
                f["skipped_source"] += 1
                continue
            if not location_ok(profile, r.get("location", "")):
                dropped += 1
                f["location_excluded"] += 1
                continue
            # Post titles are paraphrased by whoever read the post; everything
            # else is a real job title and gets the same filter as the feeds.
            if r.get("source") != "linkedin_posts" and not title_ok(profile, r["title"]):
                dropped += 1
                f["title_dropped"] += 1
                continue
            row_start = start
            if r.get("window_start"):
                row_start = min(start, parse_date(r["window_start"])[0] or start)
            ok, iso = in_window(r.get("posted"), row_start)
            if not ok:
                dropped += 1
                f["outside_window"] += 1
                continue
            r["posted_iso"] = r.get("posted_iso") or iso
            key = _norm_url(r["url"])
            pair = (_norm_text(r.get("company")), _norm_text(r["title"]))
            existing = key if key in merged else (by_pair.get(pair) if pair[0] else None)
            if existing:
                keep = merged[existing]
                f["duplicate"] += 1
                if label not in keep["sources"]:
                    keep["sources"].append(label)
                for field in ("company", "location", "posted_iso", "compensation", "eligibility", "signal", "sector"):
                    if not keep.get(field) and r.get(field):
                        keep[field] = r[field]
                continue
            r["sources"] = [label]
            f["unique"] += 1
            merged[key] = r
            if pair[0]:
                by_pair[pair] = key

    rows = list(merged.values())
    for r in rows:
        hkey = _norm_url(r["url"])
        first = history.get(hkey, today)
        r["new"] = first == today
        r["first_seen"] = first
        history[hkey] = first
        if r["new"]:
            fun(r["sources"][0])["new"] += 1
    history_path.write_text(json.dumps(history, indent=0), encoding="utf-8")
    rows.sort(key=lambda r: r.get("posted_iso", ""), reverse=True)
    rows.sort(key=lambda r: not r["new"])

    # People check (companies.py): fold this run's people_out_*.jsonl into people_checks.json, then stamp every row
    # whose company has a verdict: brazil (the candidate's country) / latam / non_us / none.
    import companies
    pc = companies.ingest_people(args.home, sorted(out_dir.glob("people_out_*.jsonl")))
    people = companies.people_checks(args.home)
    for r in rows:
        v = people.get(companies.key_of(r.get("company", "")))
        if v:
            ev = (v.get("evidence") or [{}])[0]
            r.update(people=v["level"], people_checked=v.get("checked", ""), people_evidence=ev.get("url", ""))
    gaps += [{"task": f"people check: {x}", "reason": "no verdict", "complete": False} for x in pc["not_cached"]]
    (out_dir / "results.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    (out_dir / "funnel.json").write_text(json.dumps(
        {"date": today, "profile": profile["slug"], "window_start": start.strftime("%Y-%m-%dT%H:%MZ"),
         "fetched": fetched, "merge": funnel, "tasks": tasks}, indent=1, ensure_ascii=False), encoding="utf-8")

    cols = ["new", "posted_iso", "first_seen", "people", "title", "company", "location", "compensation", "eligibility",
            "signal", "sources", "url", "people_evidence", "people_checked"]
    with (out_dir / "results.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(cols)
        for r in rows:
            writer.writerow(["; ".join(r[c]) if c == "sources" else r.get(c, "") for c in cols])

    def cell(v) -> str:
        return str(v or "").replace("|", "\\|").replace("\n", " ")

    lines = [f"# {profile['role']} sweep - {today}", "",
             f"Window: posted since {start:%Y-%m-%d %H:%M} UTC (unknown dates kept). "
             f"{len(rows)} unique postings, {sum(r['new'] for r in rows)} new; {dropped} dropped by date/title.", ""]
    if gaps:
        lines += ["## Coverage gaps", ""] + [f"- {g.get('source') or g.get('task')}: {g.get('error') or g.get('reason') or 'incomplete'}" for g in gaps] + [""]
    lines += ["| New | Posted | Engineers outside the US | Title | Company | Location | Sources |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        flag = r.get("people")
        mark = {"brazil": f"🟢 {userconfig.country()}", "latam": f"🟢 {userconfig.region()}", "non_us": "🟡 elsewhere",
                "none": "🔴 none found"}.get(flag, "")
        brazil = f"[{mark}]({r['people_evidence']})" if flag and r.get("people_evidence") else mark
        lines.append(f"| {'★' if r['new'] else ''} | {cell(r.get('posted_iso') or '?')} | {brazil} | "
                     f"[{cell(r['title'])}]({r['url']}) | {cell(r.get('company'))} | "
                     f"{cell(r.get('location'))[:60]} | {cell(', '.join(r['sources']))} |")
    (out_dir / "results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    by_source: dict[str, int] = {}
    for r in rows:
        for s in r["sources"]:
            by_source[s] = by_source.get(s, 0) + 1
    people_counts = {f: sum(r.get("people") == f for r in rows) for f in companies.LEVELS}
    print(json.dumps({"unique": len(rows), "new": sum(r["new"] for r in rows), "dropped": dropped,
                      "people": people_counts, "funnel": str(out_dir / "funnel.json"), "gaps": gaps, "by_source": dict(sorted(by_source.items(), key=lambda kv: -kv[1])),
                      "csv": str(out_dir / "results.csv"), "markdown": str(out_dir / "results.md")},
                     indent=2, ensure_ascii=False))
    return 0


# -------------------------------------------------------- people check
#
# Whether engineers who work at a company live outside the US (companies.py, references/people-check.md). company_key
# is the shared company normalisation; the verdicts live in <home>/people_checks.json.

_SUFFIXES = re.compile(r"\b(inc|incorporated|llc|ltd|limited|gmbh|ag|sa|s\.a|sas|srl|bv|b\.v|plc|corp|"
                       r"corporation|co|company|ltda|oy|ab|as|pty|pvt|technologies|labs?)\b\.?", re.I)


def company_key(name: str) -> str:
    base = re.sub(r"\(.*?\)", " ", name or "")
    return _norm_text(_SUFFIXES.sub(" ", base))


def cmd_verify_queue(args) -> int:
    """The people check for this run's companies (companies.py, references/people-check.md): new rows' companies,
    busiest first, without a fresh verdict. The subagents write people_out_*.jsonl into the run folder, which the
    next merge folds into <home>/people_checks.json."""
    import companies
    profile = load_profile(args.profile, args.home)
    out_dir = run_dir(args, profile)
    results = out_dir / "results.json"
    if not results.exists():
        sys.exit("run merge first")
    rows = json.loads(results.read_text(encoding="utf-8"))
    counts: dict[str, int] = {}
    names: dict[str, str] = {}
    for r in rows:
        if not (r.get("new") or args.all):
            continue
        k = companies.key_of(r.get("company", ""))
        if k:
            counts[k] = counts.get(k, 0) + 1
            names[k] = r["company"]
    wanted = [{"company": names[k]} for k in sorted(counts, key=lambda k: -counts[k])]
    plan = companies.people_queue(args.home, wanted, args.limit, out_dir)
    (out_dir / "verify-queue.json").write_text(json.dumps(plan, indent=1), encoding="utf-8")
    print(json.dumps(plan, indent=1, ensure_ascii=False))
    return 0


def cmd_test_match(args) -> int:
    cat, profile = load_catalogue(), load_profile(args.profile, args.home)
    for src, rows, error in sweep(cat, profile, args.sources):
        if error:
            print(f"\n{src['id']}: FAILED {error}")
            continue
        kept = [r["title"] for r in rows if r["title"] and title_ok(profile, r["title"])]
        dropped = [r["title"] for r in rows if r["title"] and not title_ok(profile, r["title"])]
        print(f"\n{src['id']}: kept {len(kept)} / {len(rows)}")
        for t in kept[: args.limit]:
            print(f"   + {t[:90]}")
        if args.dropped:
            for t in dropped[: args.limit]:
                print(f"   - {t[:90]}")
    return 0


def cmd_config(args) -> int:
    """Show the effective config (config.yaml + state) plus every choice setup can offer. --set only
    stores state keys (explorer_url); preferences are edited in config.yaml."""
    path = config_path(args.home)
    if args.set is not None:
        updates = json.loads(args.set)
        unknown = set(updates) - set(STATE_KEYS)
        if unknown:
            sys.exit(f"{', '.join(sorted(unknown))}: edit the sweep section of {userconfig.CONFIG_PATH} instead "
                     f"(config --set only stores {', '.join(STATE_KEYS)})")
        saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({**saved, **updates}, indent=2), encoding="utf-8")
    cat = load_catalogue()
    names = sorted({p.stem for d in profile_dirs(args.home) for p in d.glob("*.json") if not p.stem.startswith("_")})
    print(json.dumps({
        "exists": userconfig.CONFIG_PATH.exists(), "config_yaml": str(userconfig.CONFIG_PATH), "state_path": str(path),
        "setup_done": userconfig.setup_done(),
        "config": load_config(args.home),
        "user_profiles_dir": str(profile_dirs(args.home)[0]), "profiles": names,
        "options": {
            "sources": {s["id"]: s["label"] for s in cat["http"]},
            "browser_tasks": {**BROWSER_TASKS, "google": "all Google site: sweeps",
                              **{g["id"]: f"Google: {g['label']}" for g in cat["google_shards"]}},
            "hosts": sorted(h["site"] for g in cat["google_shards"] for h in g["hosts"]),
        },
    }, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("config", help="show the effective config (config.yaml + <home>/config.json state)")
    p.add_argument("--home", default=os.environ.get("JOBSWEEP_HOME") or userconfig.home())
    p.add_argument("--set", help="JSON object of state keys to save, e.g. '{\"explorer_url\": \"...\"}'")
    p.set_defaults(fn=cmd_config)
    for name, fn in (("plan", cmd_plan), ("http", cmd_http), ("merge", cmd_merge),
                     ("test-match", cmd_test_match), ("verify-queue", cmd_verify_queue)):
        p = sub.add_parser(name)
        p.add_argument("--profile", required=True, help="profile name in profiles/ or a path")
        p.add_argument("--days", type=float, default=2, help="age tolerance in days (default 2)")
        p.add_argument("--home", default=os.environ.get("JOBSWEEP_HOME") or userconfig.home())
        if name in ("http", "test-match"):
            p.add_argument("sources", nargs="*", help="limit to these source ids")
        if name == "http":
            p.add_argument("--api-queries", type=int, default=0,
                           help="use only the first N api_queries for Getro (0 = all)")
            p.add_argument("--budget", type=float, default=0,
                           help="stop starting new Getro requests after this many seconds")
        if name == "verify-queue":
            p.add_argument("--limit", type=int, default=20, help="max companies to check this run")
            p.add_argument("--ttl", type=int, default=30, help="re-check verdicts older than N days")
            p.add_argument("--all", action="store_true", help="include rows seen in earlier runs")
        if name == "test-match":
            p.add_argument("--dropped", action="store_true")
            p.add_argument("--limit", type=int, default=25)
        p.set_defaults(fn=fn)
    import enrich  # jds, enrich-queue, explorer (step 8 of SKILL.md)
    enrich.add_commands(sub)
    import companies  # boards, people-queue, people-ingest
    companies.add_commands(sub)
    args = parser.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
