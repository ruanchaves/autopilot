"""Enrichment and the role explorer - the stage that runs after `merge`.

    jobsweep.py jds          [--profiles a,b]                 # fetch JD text for today's roles
    jobsweep.py enrich-queue --stage eligibility|fit|country  # batch files for the subagents
    jobsweep.py explorer     [--profiles a,b] [--batches N]   # roles.json/.csv + explorer.html

All three work on the union of today's runs (every <home>/runs/<date>-<profile>/results.json,
or only the --profiles given) and write to <home>/runs/<date>-explore/. `explorer` also reads the
last --batches sweep dates and tags each role with the batches (run dates) that found it. What is
learned about a role is cached in <home> so it carries across days and profiles:

    <home>/jds/index.json + *.txt    JD text, keyed by normalised URL
    <home>/classifications.json      eligibility class (for the candidate's country/region) and resume fit per URL
    <home>/people_checks.json        engineers outside the US per company (companies.py, shared with verify-queue)
    <home>/fit-rubric.md             candidate snapshot + rubric, tied to the resume's hash
"""

from __future__ import annotations

import concurrent.futures
import csv
import hashlib
import html
import json
import os
import re
import sys
import threading
import urllib.parse
from collections import Counter
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

import jobsweep as js
import userconfig

MIN_CHARS = 400
HOST_INTERVAL = 2.0
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0 Safari/537.36")
# Pages that show a teaser instead of the JD (DailyRemote renders lorem-ipsum placeholders
# behind a sign-up wall). They count as failures; the teaser is kept as *_PARTIAL.txt.
PAYWALL = re.compile(r"Pellentesque habitant|Unlock this job")
ELIG_LABEL = {1: "open", 2: "none", 3: "restricted"}


# ------------------------------------------------------------------ helpers

RUN_DATE = js.NOW.strftime("%Y-%m-%d")  # overridden by --date


def today() -> str:
    return RUN_DATE


def explore_dir(home: str) -> Path:
    path = js.fix_path(home) / "runs" / f"{today()}-explore"
    path.mkdir(parents=True, exist_ok=True)
    return path


def host_path(path: Path) -> str:
    """Paths handed to subagents must be the Windows form when Python runs inside WSL."""
    text = str(path)
    m = re.match(r"^/mnt/([a-z])/(.*)$", text)
    if m and os.name != "nt" and "microsoft" in Path("/proc/version").read_text(errors="ignore").lower():
        return f"{m[1].upper()}:\\" + m[2].replace("/", "\\")
    return text


def rid(key: str) -> str:
    return hashlib.sha1(key.encode()).hexdigest()[:10]


def slug(text: str, n: int = 40) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", text or "").strip("-")[:n] or "x"


def read_json(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def write_json(path: Path, data, indent=1) -> None:
    # Write to a private temp file and rename it over the target: parallel `record` calls from the fill subagents
    # otherwise interleave their writes and leave a tail of the longer file behind ("Extra data" on the next read).
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=indent), encoding="utf-8")
    os.replace(tmp, path)


def read_jsonl(path: Path) -> list[dict]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip().lstrip("\ufeff")
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                print(f"skipping bad line in {path.name}: {line[:80]}", file=sys.stderr)
    return out


def run_dates(home: str, batches: int) -> list[str]:
    """The most recent `batches` sweep dates up to today(), oldest first."""
    runs = js.fix_path(home) / "runs"
    dates = {d.name[:10] for d in runs.glob("????-??-??-*") if d.is_dir() and not d.name.endswith("-explore")
             and (d / "results.json").exists() and d.name[:10] <= today()}
    return sorted(dates)[-max(batches, 1):]


def collect_roles(home: str, profiles: list[str] | None, dates: list[str] | None = None) -> list[dict]:
    """Union of the merged runs of `dates` (default: today), keyed by normalised URL.

    Each role records the sweep batches (run dates) that found it in `batches`."""
    runs = js.fix_path(home) / "runs"
    roles: dict[str, dict] = {}
    for date in dates or [today()]:
        for results in sorted(runs.glob(f"{date}-*/results.json")):
            slug_ = results.parent.name[len(date) + 1:]
            if slug_ == "explore" or (profiles and slug_ not in profiles):
                continue
            for r in json.loads(results.read_text(encoding="utf-8")):
                key = js._norm_url(r["url"])
                rec = roles.setdefault(key, {"key": key, "id": rid(key), "url": r["url"], "title": r["title"],
                                             "company": "", "location": "", "posted_iso": "", "compensation": "",
                                             "sources": [], "found": [], "batches": []})
                for f in ("company", "location", "posted_iso", "compensation"):
                    if not rec[f] and r.get(f):
                        rec[f] = r[f]
                for s in r.get("sources", []):
                    if s not in rec["sources"]:
                        rec["sources"].append(s)
                if slug_ not in rec["found"]:
                    rec["found"].append(slug_)
                if date not in rec["batches"]:
                    rec["batches"].append(date)
    if not roles:
        sys.exit(f"no merged runs for {', '.join(dates or [today()])} in {runs} - run merge first")
    return list(roles.values())


def _set_date(args) -> None:
    global RUN_DATE
    if getattr(args, "date", None):
        RUN_DATE = args.date


# ------------------------------------------------------------ JD extraction

class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "head", "nav", "footer", "header", "form"}
    BLOCK = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "tr", "section", "article"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCK:
            self.out.append("\n")
        if tag == "li":
            self.out.append("- ")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        elif tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def to_text(fragment: str) -> str:
    p = _Text()
    p.feed(html.unescape(fragment) if "&lt;" in fragment else fragment)
    t = re.sub(r"[ \t\r\f\v]+", " ", "".join(p.out))
    t = re.sub(r"\n\s*\n\s*\n+", "\n\n", t)
    return "\n".join(line.strip() for line in t.splitlines()).strip()


def jsonld_description(page: str) -> str | None:
    for m in re.finditer(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page, re.S | re.I):
        try:
            data = json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            continue
        stack = [data]
        while stack:
            d = stack.pop()
            if isinstance(d, list):
                stack.extend(d)
            elif isinstance(d, dict):
                if "@graph" in d:
                    stack.append(d["@graph"])
                t = d.get("@type")
                if (t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t)) and d.get("description"):
                    return to_text(d["description"])
    return None


class JDFetcher:
    def __init__(self):
        self.f = js.Fetcher(UA)
        self._himalayas_feed: dict | None = None
        self._himalayas_lock = threading.Lock()
        # url -> (date text, where it came from): the posting date the platform itself states, read while fetching
        # the JD. job-autopilot's run report uses it to estimate each posting's age (and to spot reposts).
        self.dates: dict[str, tuple[str, str]] = {}

    def date(self, url: str, value, method: str) -> None:
        if value not in (None, "", 0):
            self.dates[url] = (str(value), method)

    def get(self, url: str, accept: str = "text/html,application/xhtml+xml,*/*;q=0.8") -> str:
        return self.f.get(url, interval=HOST_INTERVAL, headers={"Accept": accept, "Accept-Language": "en-US,en;q=0.9"})

    def api(self, url: str):
        return json.loads(self.get(url, "application/json"))

    # Platform APIs return the full description without rendering JavaScript.
    def greenhouse(self, url):
        m = re.search(r"greenhouse\.io/(?:embed/job_app\?for=)?([^/?#]+)/jobs/(\d+)", url)
        if not m:
            return None
        d = self.api(f"https://boards-api.greenhouse.io/v1/boards/{m[1]}/jobs/{m[2]}")
        self.date(url, d.get("first_published") or d.get("updated_at"), "Greenhouse API")
        return to_text(d.get("content", ""))

    def lever(self, url):
        m = re.search(r"jobs\.lever\.co/([^/]+)/([0-9a-f-]{36})", url)
        if not m:
            return None
        d = self.api(f"https://api.lever.co/v0/postings/{m[1]}/{m[2]}")
        self.date(url, d.get("createdAt"), "Lever API")
        parts = [d.get("descriptionPlain") or to_text(d.get("description", ""))]
        parts += [l.get("text", "") + "\n" + to_text(l.get("content", "")) for l in d.get("lists", [])]
        parts.append(d.get("additionalPlain") or "")
        return "\n\n".join(p for p in parts if p)

    def ashby(self, url):
        m = re.search(r"jobs\.ashbyhq\.com/([^/]+)/([0-9a-f-]{36})", url)
        if not m:
            return None
        for j in self.api(f"https://api.ashbyhq.com/posting-api/job-board/{m[1]}").get("jobs", []):
            if m[2] in (j.get("id"), (j.get("jobUrl") or "").rsplit("/", 1)[-1]):
                self.date(url, j.get("publishedAt"), "Ashby API")
                return j.get("descriptionPlain") or to_text(j.get("descriptionHtml", ""))
        return None

    def workday(self, url):
        m = re.search(r"https://([^.]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([^/]+)/(job/.+?)(?:\?|$)", url)
        if not m:
            return None
        tenant, wd, site, path = m.groups()
        path = re.sub(r"/apply(/.*)?$", "", path)
        d = self.api(f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/{path}")
        info = d.get("jobPostingInfo", {})
        self.date(url, info.get("startDate") or info.get("postedOn"), "Workday API")
        return to_text(d.get("jobPostingInfo", {}).get("jobDescription", ""))

    def smartrecruiters(self, url):
        m = re.search(r"jobs\.smartrecruiters\.com/([^/]+)/(\d+)", url)
        if not m:
            return None
        d = self.api(f"https://api.smartrecruiters.com/v1/companies/{m[1]}/postings/{m[2]}")
        self.date(url, d.get("releasedDate"), "SmartRecruiters API")
        secs = d.get("jobAd", {}).get("sections", {})
        return "\n\n".join(f"{v.get('title', '')}\n{to_text(v.get('text', ''))}" for v in secs.values() if v.get("text"))

    def workable(self, url):
        m = re.search(r"apply\.workable\.com/([^/]+)/j/([0-9A-Za-z]+)", url)
        if not m:
            return None
        d = self.api(f"https://apply.workable.com/api/v2/accounts/{m[1]}/jobs/{m[2]}")
        self.date(url, d.get("published"), "Workable API")
        return "\n\n".join(to_text(d[k]) for k in ("description", "requirements", "benefits") if d.get(k))

    def oracle(self, url):
        m = re.search(r"https://([^/]+)/hcmUI/CandidateExperience/[^/]+/sites/([^/]+)/job/(\d+)", url)
        if not m:
            return None
        host, site, jid = m.groups()
        d = (self.api(f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails"
                      f"?expand=all&onlyData=true&finder=ById;Id=%22{jid}%22,siteNumber={site}").get("items") or [{}])[0]
        return "\n\n".join(to_text(d[k]) for k in ("ExternalDescriptionStr", "ExternalResponsibilitiesStr",
                                                    "ExternalQualificationsStr") if d.get(k))

    def linkedin(self, url):
        m = re.search(r"linkedin\.com/jobs/view/(?:[^/]*-)?(\d{6,})", url)
        if not m:
            return None
        page = self.get(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{m[1]}")
        ago = re.search(r'class="posted-time-ago__text[^"]*"[^>]*>\s*([^<]+?)\s*<', page)
        if ago:
            self.date(url, ago.group(1), "LinkedIn page")
        d = re.search(r'class="show-more-less-html__markup[^"]*"[^>]*>(.*?)</div>', page, re.S)
        return to_text(d.group(1)) if d else None

    # Himalayas serves its job pages with HTTP 403 to scripts (seen 2026-10-11), but its public feed
    # API carries each job's full description. Load the newest HIMALAYAS_PAGES pages once (the same
    # depth the sweep reads, ~1 s apart) and look the role up by its application link.
    HIMALAYAS_PAGES = 80

    def himalayas(self, url):
        if "himalayas.app/companies/" not in url:
            return None
        with self._himalayas_lock:
            if self._himalayas_feed is None:
                feed, cursor = {}, ""
                for _ in range(self.HIMALAYAS_PAGES):
                    api = "https://himalayas.app/jobs/api?limit=20"
                    if cursor:
                        api += "&cursor=" + urllib.parse.quote(cursor, safe="")
                    d = json.loads(self.f.get(api, interval=1.0, headers={"Accept": "application/json"}))
                    jobs = d.get("jobs") or []
                    for j in jobs:
                        for k in ("applicationLink", "guid"):
                            if j.get(k):
                                feed[js._norm_url(j[k])] = j
                    cursor = d.get("nextCursor") or ""
                    if not jobs or not cursor:
                        break
                self._himalayas_feed = feed
        j = self._himalayas_feed.get(js._norm_url(url))
        if not j:
            raise RuntimeError(f"not in the newest {self.HIMALAYAS_PAGES} feed pages")
        return to_text(j.get("description") or "")

    HANDLERS = [("himalayas.app", "himalayas"), ("greenhouse.io", "greenhouse"), ("lever.co", "lever"), ("ashbyhq.com", "ashby"),
                ("myworkdayjobs.com", "workday"), ("smartrecruiters.com", "smartrecruiters"),
                ("apply.workable.com", "workable"), ("oraclecloud.com", "oracle"), ("linkedin.com", "linkedin")]

    def fetch(self, url: str) -> tuple[str, str]:
        """-> (text, method). Raises RuntimeError with every attempt's reason."""
        errors = []
        for host, name in self.HANDLERS:
            if host in url:
                try:
                    t = getattr(self, name)(url)
                    if t and len(t) >= MIN_CHARS:
                        return t, f"api:{name}"
                    errors.append(f"{name}: short/empty")
                except Exception as e:  # noqa: BLE001 - every failure is recorded, none is fatal
                    errors.append(f"{name}: {type(e).__name__}: {e}"[:160])
        try:
            page = self.get(url)
        except Exception as e:  # noqa: BLE001
            errors.append(f"page: {e}"[:160])
            raise RuntimeError(" | ".join(errors))
        posted = re.search(r'"datePosted"\s*:\s*"([^"]+)"', page)
        if posted:
            self.date(url, posted.group(1), "JSON-LD datePosted")
        t = jsonld_description(page)
        if t and len(t) >= MIN_CHARS:
            return t, "json-ld"
        t = to_text(re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", page))
        if len(t) >= MIN_CHARS and re.search(r"(?i)responsibilit|requirement|qualification|experience|about the role|what you", t):
            return t, "page-text"
        errors.append(f"page: no JD text ({len(t)} chars)")
        raise RuntimeError(" | ".join(errors))


def cmd_jds(args) -> int:
    _set_date(args)
    home = js.fix_path(args.home)
    roles = collect_roles(args.home, args.profiles)
    jd_dir = home / "jds"
    jd_dir.mkdir(parents=True, exist_ok=True)
    index_path = jd_dir / "index.json"
    index = read_json(index_path, {})
    # Fetch what's new; retry plain failures once a day (a sign-up wall won't change).
    todo = [r for r in roles if r["key"] not in index
            or (index[r["key"]]["status"] == "failed" and index[r["key"]].get("fetched", "") < today())]
    lock = threading.Lock()
    fetcher = JDFetcher()
    done = Counter()

    def work(r):
        name = f"{r['id']}_{slug(r['company'], 25)}_{slug(r['title'])}"
        entry = {"url": r["url"], "title": r["title"], "company": r["company"], "fetched": today(),
                 "file": "", "method": "", "chars": 0, "error": ""}
        try:
            text, method = fetcher.fetch(r["url"])
            if PAYWALL.search(text):
                (jd_dir / f"{name}_PARTIAL.txt").write_text(f"URL: {r['url']}\n\n{text}\n", encoding="utf-8")
                entry.update(status="signup", error=f"sign-up wall: only a teaser is visible; kept as {name}_PARTIAL.txt")
            else:
                head = (f"Title: {r['title']}\nCompany: {r['company']}\nLocation: {r['location']}\n"
                        f"Posted: {r['posted_iso']}\nURL: {r['url']}\nFetched via: {method}\n\n")
                (jd_dir / f"{name}.txt").write_text(head + text + "\n", encoding="utf-8")
                entry.update(status="ok", method=method, chars=len(text), file=f"{name}.txt")
            if r["url"] in fetcher.dates:
                value, how = fetcher.dates[r["url"]]
                parsed, _ = js.parse_date(value)
                if parsed:
                    entry.update(posted_src=parsed.strftime("%Y-%m-%dT%H:%MZ"), posted_method=how)
        except Exception as e:  # noqa: BLE001
            entry.update(status="failed", error=str(e)[:400])
        with lock:
            index[r["key"]] = entry
            done[entry["status"]] += 1
            if sum(done.values()) % 25 == 0:
                write_json(index_path, index)
                print(f"  {sum(done.values())}/{len(todo)} {dict(done)}", flush=True)

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(work, todo))
    write_json(index_path, index)

    out = explore_dir(args.home)
    failures = [{"url": r["url"], "title": r["title"], "company": r["company"],
                 "status": index[r["key"]]["status"], "error": index[r["key"]]["error"]}
                for r in roles if index.get(r["key"], {}).get("status") != "ok"]
    with (out / "jd_fetch_failures.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, ["url", "title", "company", "status", "error"])
        w.writeheader()
        w.writerows(failures)
    status = Counter(index.get(r["key"], {}).get("status", "missing") for r in roles)
    print(json.dumps({"roles": len(roles), "fetched_now": len(todo), "status": dict(status),
                      "failures_csv": host_path(out / "jd_fetch_failures.csv"), "jd_dir": host_path(jd_dir)}, indent=1))
    return 0


# -------------------------------------------------------- classifications

def ingest_outputs(home: Path, out: Path) -> dict:
    """Fold subagent output (elig_out_*.jsonl, fit_out_*.jsonl) into <home>/classifications.json."""
    path = home / "classifications.json"
    cache = read_json(path, {})
    by_id = {v.get("id"): k for k, v in cache.items() if v.get("id")}
    batches = {}
    # only the eligibility and fit batches map ids to keys; people-check and harvest batches share the folder
    for b in [*out.glob("elig_batch_*.json"), *out.glob("fit_batch_*.json")]:
        for e in json.loads(b.read_text(encoding="utf-8")):
            if isinstance(e, dict) and "id" in e and "key" in e:
                batches[e["id"]] = e["key"]
    rubric_sha = current_rubric_sha(home)
    changed = False
    for kind in ("elig", "fit"):
        for f in sorted(out.glob(f"{kind}_out_*.jsonl")):
            for d in read_jsonl(f):
                key = batches.get(str(d.get("id"))) or by_id.get(str(d.get("id")))
                if not key:
                    continue
                entry = cache.setdefault(key, {"id": str(d["id"])})
                if kind == "elig":
                    cat = int(d.get("category", 2))
                    if cat == 2 and d.get("residency_required"):
                        cat = 3
                    entry["elig"] = {"category": cat, "region": d.get("region", ""),
                                     "residency": bool(d.get("residency_required")),
                                     "evidence": d.get("evidence", ""), "date": today()}
                else:
                    entry["fit"] = {"fit": d.get("fit"), "matches": d.get("matches", ""), "gaps": d.get("gaps", ""),
                                    "reason": d.get("reason", ""), "rubric": rubric_sha, "date": today()}
                changed = True
    if changed:
        write_json(path, cache, indent=0)
    return cache


def resume_sha(home: Path) -> str:
    resume = js.load_config(str(home)).get("resume") or ""
    path = js.fix_path(resume) if resume else None
    return hashlib.sha1(path.read_bytes()).hexdigest()[:12] if path and path.exists() else ""


def current_rubric_sha(home: Path) -> str:
    rubric = home / "fit-rubric.md"
    if not rubric.exists():
        return ""
    m = re.search(r"resume_sha:\s*([0-9a-f]+)", rubric.read_text(encoding="utf-8"))
    return m[1] if m else ""


def write_batches(out: Path, stage: str, entries: list[dict], size: int) -> list[dict]:
    for old in out.glob(f"{stage}_batch_*.json"):
        old.unlink()
    batches = []
    k = -(-len(entries) // size)  # batch count; entries are spread evenly across them
    for n in range(k):
        part = entries[n::k]
        path = out / f"{stage}_batch_{n}.json"
        write_json(path, part, indent=0)
        batches.append({"batch": host_path(path), "out": host_path(out / f"{stage}_out_{n}.jsonl"),
                        "count": len(part)})
    return batches


def cmd_enrich_queue(args) -> int:
    _set_date(args)
    home = js.fix_path(args.home)
    out = explore_dir(args.home)
    roles = collect_roles(args.home, args.profiles)
    cache = ingest_outputs(home, out)
    index = read_json(home / "jds" / "index.json", {})
    config = js.load_config(args.home)
    result: dict = {"stage": args.stage}

    if args.stage in ("eligibility", "fit"):
        stage = "elig" if args.stage == "eligibility" else "fit"
        rsha = resume_sha(home)
        if stage == "fit":
            if not rsha:
                sys.exit("no resume configured (config key 'resume') or the file is missing - fit stage skipped")
            if current_rubric_sha(home) != rsha:
                print(json.dumps({"stage": "fit", "rubric_needed": True, "resume": config["resume"],
                                  "resume_sha": rsha, "rubric_path": host_path(home / "fit-rubric.md")}, indent=1))
                return 0
        entries = []
        for r in roles:
            jd = index.get(r["key"], {})
            if jd.get("status") != "ok":
                continue
            c = cache.get(r["key"], {})
            if stage == "elig" and "elig" in c:
                continue
            if stage == "fit":
                if c.get("elig", {}).get("category") not in (1, 2):
                    continue
                if c.get("fit", {}).get("rubric") == rsha:
                    continue
            cache.setdefault(r["key"], {})["id"] = r["id"]
            entries.append({"id": r["id"], "key": r["key"], "file": host_path(home / "jds" / jd["file"]),
                            "title": r["title"], "company": r["company"]})
        write_json(home / "classifications.json", cache, indent=0)
        c = userconfig.candidate()
        result.update(pending=len(entries), batches=write_batches(out, stage, entries, args.batch_size),
                      context={"country": c.get("country", ""), "region": c.get("region", ""),
                               "region_aliases": c.get("region_aliases") or []})
        if stage == "fit":
            result["rubric"] = host_path(home / "fit-rubric.md")
    else:  # country check = the people check (companies.py, references/people-check.md)
        import companies
        companies.ingest_people(args.home, sorted(out.glob("people_out_*.jsonl")))
        wanted: dict[str, dict] = {}
        rank = {"green": 0, "yellow": 1}
        for r in roles:
            c = cache.get(r["key"], {})
            fit = c.get("fit", {}).get("fit")
            # every role worth a look, whatever its JD says about location: the JD's label is not evidence
            if c.get("elig", {}).get("category") not in (1, 2, 3) or fit not in args.fits:
                continue
            k = companies.key_of(r["company"])
            if not k:
                continue
            e = wanted.setdefault(k, {"company": r["company"], "roles": 0, "rank": 9})
            e["roles"] += 1
            e["rank"] = min(e["rank"], rank.get(fit, 9))
        ordered = sorted(wanted.values(), key=lambda e: (e["rank"], -e["roles"]))
        limit = args.limit or int(config.get("country_check_limit") or 20)
        result.update(companies.people_queue(args.home, ordered, limit, out))
    print(json.dumps(result, indent=1, ensure_ascii=False))
    return 0


# ----------------------------------------------------------------- explorer

REGIONS = [
    ("Brazil", r"brazil|brasil|s[aã]o paulo|rio de janeiro|campinas|goi[aâ]nia|curitiba|belo horizonte|porto alegre|recife|florian"),
    ("LATAM", r"latam|latin america|south america|argentina|mexico|m[eé]xico|colombia|chile|peru|uruguay|panama|costa rica|"
              r"guatemala|dominican|caribbean|buenos aires|bogot|americas"),
    ("Worldwide", r"worldwide|anywhere|global"),
    ("Canada", r"canada|toronto|vancouver|montreal"),
    ("US", r"\bus\b|\busa\b|u\.s\.|united states|\b(CA|NY|WA|TX|VA|MA|GA|TN|PA|CO|FL|IL|NJ|NC|MI|OR|AZ|UT|MN|OH)\b|"
           r"new york|san francisco|seattle|bellevue|denver|boston|chicago|austin|menlo park|virginia|california"),
    ("Europe", r"\beu\b|europe|\buk\b|united kingdom|germany|deutschland|\bdeu\b|france|spain|ireland|poland|netherlands|"
               r"sweden|portugal|italy|berlin|munich|m[uü]nchen|orl[eé]ans|london|paris|croatia|emea"),
    ("India", r"india|bangalore|bengaluru|hyderabad|pune"),
]


def regions() -> list[tuple[str, str]]:
    """REGIONS with the candidate's own country (config.yaml) in front when it isn't already listed."""
    c = userconfig.country()
    if c and c not in {label for label, _ in REGIONS}:
        return [(c, re.escape(c.lower()))] + REGIONS
    return REGIONS


def region_of(*texts: str) -> str:
    t = re.sub(r"(?i)\bremote\b", "", " ".join(x for x in texts if x)).strip(" ,-()")
    if not t or t.lower() == "unclear":
        return "Unclear"
    return next((name for name, pat in regions() if re.search(pat, t, re.I)), "Other")


def seniority(title: str) -> str:
    t = title.lower()
    for name, pat in (("Junior/Entry", r"\b(intern|junior|jr\.?|entry|graduate|new grad|associate)\b"),
                      ("Principal/Lead", r"\b(principal|lead|head|architect|distinguished|founding)\b"),
                      ("Staff", r"\bstaff\b"),
                      ("Senior", r"\b(senior|sr\.?|s[eê]nior|iii|iv)\b"),
                      ("Mid", r"\b(mid|ii|intermediate)\b|mid-level")):
        if re.search(pat, t):
            return name
    return "Not stated"


def source_buckets(labels: list[str]) -> list[str]:
    cat = js.load_catalogue()
    feeds = {s["label"].lower() for s in cat["http"] if not s["id"].startswith(("getro_", "consider_"))}
    out = []
    for label in labels:
        low = label.lower()
        if "linkedin" in low:
            name = "LinkedIn Jobs"
        elif "rocketship" in low:
            name = "Remote Rocketship"
        elif "portfolio" in low:
            name = "VC portfolio boards"
        elif low in feeds:
            name = "Remote-job feeds"
        elif re.search(r"ats|workday|ashby|lever|greenhouse|workable|smartrecruiters|icims|oracle|taleo|breezy|recruitee|"
                       r"jobvite|pinpoint|teamtailor|personio|rippling|bamboo|\bgem\b|successfactors", low):
            name = "Company job pages (ATS) via Google"
        else:
            name = "Job boards via Google"
        if name not in out:
            out.append(name)
    return out or ["Job boards via Google"]


def posted_bucket(iso: str) -> str:
    parsed, _ = js.parse_date(iso)
    if not parsed:
        return "unknown"
    age = js.NOW - parsed.astimezone(timezone.utc)
    return next((b for b, h in (("24h", 24), ("48h", 48), ("7d", 24 * 7)) if age <= timedelta(hours=h)), "older")


def cmd_explorer(args) -> int:
    _set_date(args)
    home = js.fix_path(args.home)
    out = explore_dir(args.home)
    dates = run_dates(args.home, args.batches)
    roles = collect_roles(args.home, args.profiles, dates)
    cache = ingest_outputs(home, out)
    import companies
    verify_gaps = [{"task": f"people check: {x}", "reason": "no verdict"}
                   for x in companies.ingest_people(args.home, sorted(out.glob("people_out_*.jsonl")))["not_cached"]]
    people = companies.people_checks(args.home)
    index = read_json(home / "jds" / "index.json", {})
    config = js.load_config(args.home)
    term = js.load_profile(config["profile"], args.home).get("country_check_term", "engineer")
    rsha = resume_sha(home)
    # job-autopilot (optional) writes scores and application statuses for this page;
    # recruiter-outreach (optional) writes the ranked recruiter leads for its Outreach view
    auto = read_json(home / "autopilot" / "dashboard.json", {})
    auto_scores = auto.get("scores", {})
    recruiters = read_json(home / "recruiters" / "dashboard.json", {})

    records = []
    for r in roles:
        jd, c = index.get(r["key"]), cache.get(r["key"], {})
        el, ft = c.get("elig"), c.get("fit")
        if ft and ft.get("rubric") != rsha:
            ft = None  # rated against a different resume
        v = people.get(companies.key_of(r["company"])) if r["company"] else None
        # good: engineers in the candidate's country or region; weak: only elsewhere outside the US; bad: none found
        bz = {"brazil": "good", "latam": "good", "non_us": "weak", "none": "bad"}.get((v or {}).get("level"), "none")
        ev = ((v or {}).get("evidence") or [{}])[0]
        region_hint = el["region"] if el and el.get("region", "").lower() != "unclear" else ""
        records.append({
            "id": r["id"], "title": r["title"], "company": r["company"], "url": r["url"],
            "location": r["location"], "posted": r["posted_iso"], "postedBucket": posted_bucket(r["posted_iso"]),
            "comp": r["compensation"], "sources": source_buckets(r["sources"]), "found": r["found"],
            "batches": r["batches"],
            "jd": {"ok": "fetched", "signup": "signup", "failed": "failed"}.get((jd or {}).get("status"), "not_fetched"),
            "jdFile": host_path(home / "jds" / jd["file"]) if jd and jd.get("file") else "",
            "elig": ELIG_LABEL.get(el["category"], "none") if el else "unclassified",
            "eligEvidence": el.get("evidence", "") if el else "",
            "region": region_of(region_hint, r["location"]),
            "fit": ft["fit"] if ft and ft.get("fit") in ("green", "yellow", "red") else "unrated",
            "fitReason": ft.get("reason", "") if ft else "", "fitMatches": ft.get("matches", "") if ft else "",
            "fitGaps": ft.get("gaps", "") if ft else "",
            "bz": bz, "bzLevel": (v or {}).get("level", ""),
            "bzEvidence": " | ".join(x for x in [ev.get("title", ""), ev.get("location", ""), ev.get("snippet", "")] if x)[:300]
            if v else "",
            "bzUrl": ev.get("url", "") if v else "", "bzChecked": v.get("checked", "") if v else "",
            "seniority": seniority(r["title"]),
        })
        a = auto_scores.get(r["id"])
        if a:
            records[-1].update(score=a.get("score"), scoreWhy=a.get("why", ""), applyOk=a.get("apply", True),
                               skipReason=a.get("skipReason", ""), skipEvidence=a.get("skipEvidence", ""),
                               scoreParts=a.get("parts") or {}, blockers=a.get("blockers") or [],
                               scoreConfidence=a.get("confidence", ""), override=a.get("override", ""),
                               apStatus=a.get("status", ""), payNote=a.get("pay", ""),
                               tier=a.get("tier", ""), payTier=a.get("payTier", ""), payTierWhy=a.get("payTierWhy", ""))

    write_json(out / "roles.json", records, indent=0)
    cols = ["title", "company", "location", "posted", "elig", "eligEvidence", "region", "fit", "fitReason", "fitGaps",
            "bz", "bzEvidence", "seniority", "sources", "found", "batches", "jd", "url", "jdFile"]
    with (out / "roles.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for rec in records:
            w.writerow(["; ".join(rec[c]) if isinstance(rec[c], list) else rec[c] for c in cols])

    meta = {"built": js.NOW.strftime("%Y-%m-%d %H:%M UTC"), "date": today(),
            "profiles": sorted({p for r in records for p in r["found"]}),
            "batches": sorted({b for r in records for b in r["batches"]}, reverse=True),
            "resume": Path(config.get("resume") or "").name, "term": term,
            "country": userconfig.country(), "region": userconfig.region(), "role": config.get("role_title", ""),
            "autopilot": {**{k: auto.get(k) for k in ("built", "apps", "questions", "estimates", "skills", "ready", "max_ready")},
                          "reports": [r for r in read_json(home / "autopilot" / "runs.json", []) if r.get("report_url")][-8:][::-1]}
            if auto else None,
            "recruiters": {k: recruiters.get(k) for k in ("built", "leads", "quotas", "min_score", "queries")} if recruiters else None}
    template = (js.SKILL / "assets" / "explorer.html").read_text(encoding="utf-8")
    safe = lambda obj: json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")  # noqa: E731
    page = template.replace("__DATA__", safe(records)).replace("__META__", safe(meta))
    (out / "explorer.html").write_text(page, encoding="utf-8")

    count = lambda k: dict(Counter(x[k] for x in records))  # noqa: E731
    print(json.dumps({"roles": len(records), "batches": dict(Counter(b for x in records for b in x["batches"])),
                      "elig": count("elig"), "fit": count("fit"), "bz": count("bz"),
                      "jd": count("jd"), "region": count("region"), "gaps": verify_gaps,
                      "explorer": host_path(out / "explorer.html"), "csv": host_path(out / "roles.csv"),
                      "artifact_url": config.get("explorer_url", "")}, indent=1, ensure_ascii=False))
    return 0


def add_commands(sub) -> None:
    home_default = os.environ.get("JOBSWEEP_HOME") or userconfig.home()

    def common(p):
        p.add_argument("--home", default=home_default)
        p.add_argument("--profiles", type=lambda s: [x.strip() for x in s.split(",") if x.strip()],
                       help="comma-separated profile slugs (default: every profile run today)")
        p.add_argument("--date", help="YYYY-MM-DD: work on that day's runs instead of today's")

    p = sub.add_parser("jds", help="fetch JD text for today's roles into <home>/jds")
    common(p)
    p.add_argument("--workers", type=int, default=6)
    p.set_defaults(fn=cmd_jds)

    p = sub.add_parser("enrich-queue", help="batch files for the eligibility / fit / country-check subagents")
    common(p)
    p.add_argument("--stage", required=True, choices=["eligibility", "fit", "country", "brazil"])  # brazil = old name
    p.add_argument("--batch-size", type=int, default=25)
    p.add_argument("--fits", type=lambda s: s.split(","), default=["green", "yellow"],
                   help="country stage: which resume-fit ratings to check (default green,yellow)")
    p.add_argument("--limit", type=int, default=0, help="country stage: max companies (default sweep.country_check_limit)")
    p.add_argument("--ttl", type=int, default=30, help="country stage: re-check verdicts older than N days")
    p.set_defaults(fn=cmd_enrich_queue)

    p = sub.add_parser("explorer", help="join everything into roles.json/.csv and render explorer.html")
    common(p)
    p.add_argument("--batches", type=int, default=14,
                   help="include the last N sweep dates up to --date, filterable by batch on the page (default 14)")
    p.set_defaults(fn=cmd_explorer)
