"""The run report: one page per /job-autopilot run that explains every decision the run made.

Built by `autopilot run-report` from the run's decision log (runs/<run_id>/decisions.jsonl), the sweep's per-source
funnels (runs/<date>-<profile>/funnel.json, plan.json), the ledger, the queue and the caches. Written to
<home>/autopilot/runs/<run_id>/report.html (+ report.json, which the next run's report compares against), and
published by the skill as a new artifact each run.

Sections: the search breakdown (every source and browser task, the query it ran, its status and what each filter
removed), the decision funnel, the selected roles in the order they were picked, every rejected role with the step,
reason, evidence and who decided, each posting's estimated age with the method used, and the changes since the last
run.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

SWEEP = Path(__file__).resolve().parents[2] / "job-source-sweep" / "scripts"
sys.path.insert(0, str(SWEEP))
import jobsweep as js  # noqa: E402
import enrich  # noqa: E402
import userconfig  # noqa: E402

import autopilot as apm  # noqa: E402  (this folder)

NOW = datetime.now(timezone.utc)
ASSET = Path(__file__).resolve().parents[1] / "assets" / "run-report.html"

REASON_LABEL = {
    "banned": "Banned company (a current employer or client)",
    "in_applika": "Already applied (in applika)",
    "applied_company": "Applied to this company within the cooldown",
    "rejected_company": "Company rejected you recently",
    "your_override": "You chose not to apply",
    "rejected_in_explorer": "You rejected it in the explorer",
    "mismatch": "Clear mismatch with your profile",
    "location_explicit": "JD excludes your location",
    "legal_requirement": "Legal requirement (citizenship, clearance...)",
    "not_remote": "Not remote",
    "contract_type": "Excluded contract type",
    "pay_below_target": "Posted pay under your floor",
    "pay_gate": "Company's quotes to you under your floor",
    "banned_client": "Banned client",
    "finished": "Already finished",
    "waiting_for_you": "Filled, waiting for you",
    "waiting_answers": "Waiting for your answers",
    "load_retries": "Failed to load too many times",
    "too_old": "Posting too old for this run",
    "tier_c": "Tier C (target_mode: only)",
    "not_scored": "Not scored yet",
}


def first_seen_map(home: str) -> dict[str, str]:
    """normalised URL -> the first date any sweep saw it."""
    out: dict[str, str] = {}
    for f in (js.fix_path(home) / "history").glob("*.json"):
        for k, d in enrich.read_json(f, {}).items():
            if k not in out or d < out[k]:
                out[k] = d
    return out


def age_of(role: dict, jd: dict, first: str) -> dict:
    """Estimated posting age, strongest method first: a date the ATS API or JSON-LD states, the date the source gave
    (a relative "3 days ago" was converted when read), or the first time our sweeps saw it (a minimum)."""
    cands = []
    if jd.get("posted_src"):
        cands.append((jd["posted_src"], jd.get("posted_method") or "job page"))
    if role.get("posted_iso"):
        src = (role.get("sources") or ["source"])[0]
        cands.append((role["posted_iso"], f"date given by {src}"))
    out = {"days": None, "date": "", "method": "unknown", "minimum": False, "first_seen": first, "reposted": ""}
    if cands:
        date, method = cands[0]
        parsed, _ = js.parse_date(date)
        if parsed:
            out.update(days=max(0, (NOW - parsed.astimezone(timezone.utc)).days), date=parsed.strftime("%Y-%m-%d"), method=method)
            other = [c for c in cands[1:] if (js.parse_date(c[0])[0] or parsed).date() != parsed.date()]
            if other:
                out["other_date"] = f"{other[0][0][:10]} ({other[0][1]})"
    if out["days"] is None and first:
        parsed, _ = js.parse_date(first)
        if parsed:
            out.update(days=max(0, (NOW - parsed).days), date=first, method="first seen by the sweep", minimum=True)
    if first and out["date"] and first < out["date"]:
        gap = (js.parse_date(out["date"])[0] - js.parse_date(first)[0]).days
        if gap >= 7:
            out["reposted"] = f"seen by the sweep on {first}, {gap} days before its current date: probably reposted"
    return out


def sweep_runs(home: str, dates: list[str]) -> list[dict]:
    """The sweep's per-source funnels and browser tasks for the run's dates."""
    out = []
    for d in dates:
        for run in sorted((js.fix_path(home) / "runs").glob(f"{d}-*")):
            if run.name.endswith("-explore"):
                continue
            funnel = enrich.read_json(run / "funnel.json", {})
            plan = enrich.read_json(run / "plan.json", {})
            out.append({"run": run.name, "funnel": funnel, "plan_tasks": [t.get("label") for t in plan.get("tasks") or []]})
    return out


def source_rows(sweeps: list[dict], role_stats: dict) -> list[dict]:
    rows = []
    for s in sweeps:
        f = s["funnel"]
        fetched, merge = f.get("fetched") or {}, f.get("merge") or {}
        tasks = {t.get("task") or t.get("source"): t for t in f.get("tasks") or []}
        labels = list(dict.fromkeys(list(fetched) + list(merge)))
        for label in labels:
            fe, me = fetched.get(label) or {}, merge.get(label) or {}
            lane = "http" if fe else "browser"
            status = fe.get("status") or ("ok" if me else "")
            cov = tasks.get(label) or {}
            if cov and not cov.get("complete", True):
                status = "partial"
            rows.append({"run": s["run"], "source": label, "lane": lane, "query": fe.get("query") or "",
                         "status": status, "error": fe.get("error") or cov.get("reason") or cov.get("error") or "",
                         "fetched": fe.get("fetched"), "title_ok": fe.get("title_ok"), "in_window": fe.get("in_window"),
                         "kept": fe.get("kept"), **{k: me.get(k, 0) for k in ("rows_in", "location_excluded", "title_dropped",
                                                                                 "outside_window", "duplicate", "unique", "new")},
                         **role_stats.get((label, s["run"][:10]), {"selected": 0, "rejected": 0, "considered": 0})})
        # browser tasks that never wrote a row: planned but not run, or run and empty
        seen = {r["source"] for r in rows if r["run"] == s["run"]}
        for label, t in tasks.items():
            if label not in seen and t.get("task"):
                rows.append({"run": s["run"], "source": label, "lane": "browser", "query": "", "status":
                             "ok (no rows)" if t.get("complete", True) else "partial",
                             "error": t.get("reason") or t.get("error") or "", "rows_in": 0, "unique": 0, "new": 0,
                             "selected": 0, "rejected": 0, "considered": 0})
                seen.add(label)
        for label in s["plan_tasks"]:
            if label not in seen and label not in tasks:
                rows.append({"run": s["run"], "source": label, "lane": "browser", "query": "", "status": "not run",
                             "error": "no coverage line: the task was not run (time cap, CAPTCHA or skipped)",
                             "rows_in": 0, "unique": 0, "new": 0, "selected": 0, "rejected": 0, "considered": 0})
        # HTTP sources in the catalogue that this sweep never fetched: a partial `jobsweep http <ids>` run leaves
        # HN, YC and the VC boards out silently otherwise (the 2026-10-14 run fetched three sources of 29)
        if fetched:
            for label, sid in http_catalogue().items():
                if label not in seen and sid not in skipped_sources():
                    rows.append({"run": s["run"], "source": label, "lane": "http", "query": "", "status": "not run",
                                 "error": "not fetched by this sweep: the HTTP lane was run for a subset of sources",
                                 "rows_in": 0, "unique": 0, "new": 0, "selected": 0, "rejected": 0, "considered": 0})
                    seen.add(label)
    return rows


def http_catalogue() -> dict[str, str]:
    """label -> id of every HTTP source in sources.json."""
    return {src["label"]: src["id"] for src in js.load_catalogue().get("http") or []}


def skipped_sources() -> set[str]:
    return set((userconfig.load().get("sweep") or {}).get("skip_sources") or [])


def build(home: str, run: str, batches: int = 14) -> dict:
    hp = js.fix_path(home)
    ap = hp / "autopilot"
    st = apm.settings(home)
    roles = apm.all_roles(home, batches)
    ev = apm.evaluate(home, roles, st)
    ledger = apm.load(home, "ledger.json", {})
    queue = apm.load(home, "queue.json", [])
    qpos = {x["id"]: i for i, x in enumerate(queue, 1)}
    index = enrich.read_json(hp / "jds" / "index.json", {})
    first = first_seen_map(home)
    runs = apm.load(home, "runs.json", [])
    meta = next((r for r in runs if r.get("id") == run), {"id": run, "started": NOW.isoformat()})
    started = meta.get("started") or NOW.isoformat()
    decisions = enrich.read_jsonl(ap / "runs" / run / "decisions.jsonl") if (ap / "runs" / run / "decisions.jsonl").exists() else []
    trail: dict[str, list] = {}
    people_log, search_log = [], []
    for d in decisions:
        if d.get("step") == "people check":
            people_log.append(d)
        elif d.get("step") == "search":
            try:
                search_log.append({"at": d.get("at", ""), **json.loads(d.get("evidence") or "{}")})
            except json.JSONDecodeError:
                pass
        elif d.get("role"):
            trail.setdefault(d["role"], []).append({k: d.get(k, "") for k in ("at", "step", "outcome", "reason", "evidence", "by")})

    out_roles = []
    for r in roles:
        v = ev[r["id"]]
        e = ledger.get(r["id"], {})
        t = trail.get(r["id"], [])
        last = next((x for x in reversed(t) if x["step"] in ("fill", "you")), None) or \
            next((x for x in reversed(t) if x["step"] == "queue"), None) or \
            next((x for x in reversed(t) if x["step"] in ("pre-score", "score")), None)
        status = e.get("status") or ("queued" if r["id"] in qpos else "")
        if last:
            step, outcome, reason, evidence, by = last["step"], last["outcome"], last["reason"], last["evidence"], last["by"]
        elif v["parts"] is None:
            step, outcome, reason, evidence, by = "score", "not_scored", "not_scored", "no score yet (no JD, or scored later)", "script"
        else:
            step, outcome, reason, evidence, by = "earlier run", status or "unknown", "", e.get("detail", ""), "earlier run"
        selected = step in ("fill", "you") and outcome not in ("skipped",) or (step == "queue" and outcome == "queued")
        sp = v["parts"] or {}
        age = age_of(r, index.get(r["key"], {}), first.get(r["key"], ""))
        out_roles.append({
            "id": r["id"], "title": r["title"], "company": r["company"], "url": r["url"], "location": r.get("location", ""),
            "sources": r.get("sources") or [], "batches": r.get("batches") or [],
            "age": age, "tier": v["tier"], "tierWhy": v["tier_reason"],
            "pay": v["pay"]["tier"], "payWhy": v["pay"]["detail"], "paySource": v["pay"].get("source", ""),
            "posted_pay": sp.get("pay_note", ""),
            "people": v["people"] or "unchecked", "peopleEv": v["people_evidence"], "peopleChecked": v["people_checked"],
            "score": v["score"], "parts": {k: sp.get(k) for k in ("core", "requirements", "seniority", "domain")} if sp else {},
            "blockers": sp.get("blockers") or [], "why": sp.get("why", ""), "level": sp.get("level", ""),
            "elig": {1: "open", 2: "not stated", 3: "restricted"}.get(v["elig"], "unclassified"),
            "step": step, "outcome": outcome, "reason": reason, "reasonLabel": REASON_LABEL.get(reason, reason),
            "evidence": evidence, "by": by, "status": status, "queuePos": qpos.get(r["id"]),
            "selected": bool(selected), "trail": t[-8:],
        })

    # downstream counts per (source, sweep date): of the roles a source listed on that date, how many this run
    # selected and how many it rejected
    role_stats: dict[tuple, dict] = {}
    for x in out_roles:
        for s in x["sources"]:
            for b in x["batches"]:
                d = role_stats.setdefault((s, b), {"selected": 0, "rejected": 0, "considered": 0})
                d["considered"] += 1
                d["selected" if x["selected"] else "rejected"] += 1
    dates = sorted({started[:10], NOW.strftime("%Y-%m-%d")})
    sweeps = sweep_runs(home, dates)

    # changes since the previous run's report
    prev_meta = next((r for r in reversed(runs) if r.get("id") < run and (ap / "runs" / r["id"] / "report.json").exists()), None)
    changes = {"previous_run": prev_meta.get("id") if prev_meta else "", "new": [], "gone": [], "tier_changed": [], "decision_changed": []}
    if prev_meta:
        prev = {x["id"]: x for x in enrich.read_json(ap / "runs" / prev_meta["id"] / "report.json", {}).get("roles", [])}
        cur = {x["id"]: x for x in out_roles}
        changes["new"] = [{"id": k, "title": x["title"], "company": x["company"]} for k, x in cur.items() if k not in prev][:300]
        changes["gone"] = [{"id": k, "title": x["title"], "company": x["company"],
                            "why": "no longer in the sweep window (the posting closed, or no source listed it again)"}
                           for k, x in prev.items() if k not in cur][:300]
        changes["tier_changed"] = [{"id": k, "title": x["title"], "company": x["company"], "from": prev[k]["tier"],
                                    "to": x["tier"], "why": x["tierWhy"]} for k, x in cur.items() if k in prev and prev[k]["tier"] != x["tier"]][:300]
        changes["decision_changed"] = [{"id": k, "title": x["title"], "company": x["company"],
                                        "from": f'{prev[k]["outcome"]} ({prev[k]["reasonLabel"]})', "to": f'{x["outcome"]} ({x["reasonLabel"]})'}
                                       for k, x in cur.items() if k in prev and prev[k]["outcome"] != x["outcome"]][:300]

    funnel = Counter()
    for x in out_roles:
        funnel[(x["step"], x["outcome"], x["reasonLabel"])] += 1
    data = {
        "run": run, "started": started, "built": NOW.strftime("%Y-%m-%d %H:%M UTC"),
        "candidate": {"country": userconfig.country(), "region": userconfig.region()},
        "target": st["pay_target_usd_month"], "floor": st.get("pay_skip_below_usd_month"), "target_mode": st.get("target_mode"),
        "sources": source_rows(sweeps, role_stats),
        "funnel": [{"step": k[0], "outcome": k[1], "reason": k[2], "n": n} for k, n in funnel.most_common()],
        "people_checks": [{k: d.get(k, "") for k in ("company", "outcome", "reason", "evidence")} for d in people_log],
        "search": search_log,
        "backtest": apm.load(home, "paytier_backtest.json", {}),
        "changes": changes,
        "roles": out_roles,
    }
    rd = apm.run_dir(home, run)
    enrich.write_json(rd / "report.json", data, indent=0)
    safe = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    title = f"Autopilot run {run[:4]}-{run[4:6]}-{run[6:8]} {run[9:11]}:{run[11:13]}" if len(run) >= 13 else f"Autopilot run {run}"
    page = ASSET.read_text(encoding="utf-8").replace("__TITLE__", title).replace("__DATA__", safe)
    (rd / "report.html").write_text(page, encoding="utf-8")
    sel = [x for x in out_roles if x["selected"]]
    return {"run": run, "roles": len(out_roles), "selected": len(sel), "rejected": len(out_roles) - len(sel),
            "by_tier_selected": dict(Counter(x["tier"] for x in sel)), "sources": len(data["sources"]),
            "decisions_logged": len(decisions), "report": enrich.host_path(rd / "report.html"),
            "next": "publish report.html as a NEW artifact (one per run), then `autopilot run-report --published-url <url>`"}
