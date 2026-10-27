"""What is left of the target tiers (removed 2026-10-27, the user's rule: the pay estimate and the A/B/C tiers that
guessed a company's pay from its type, headquarters, sibling postings and funding were too complex to trust).

Two cheap, evidence-based pieces stay:

* quote_gate - pay_ceilings.json (recruiter-outreach): a company whose highest quote to the user was under the floor
  is held back. That is a number the company itself gave, not an estimate.
* the location override - a JD's "US-based only" is written by automated systems and is often wrong; engineers who
  work at the company from the candidate's country or region (people_checks.json, the sweep's and
  recruiter-outreach's people check, read from the cache only) override it. hires_worldwide covers a JD whose body
  says it hires anywhere.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

SWEEP = Path(__file__).resolve().parents[2] / "job-source-sweep" / "scripts"
sys.path.insert(0, str(SWEEP))
import jobsweep as js  # noqa: E402
import enrich  # noqa: E402
import companies as co  # noqa: E402

REGION_OK = {"brazil", "latam"}
# A JD body that says the role is open worldwide. A region label alone is not evidence (user rule 2026-10-14);
# this needs the words in the eligibility evidence, which the classifier quotes from the JD.
WORLDWIDE = re.compile(r"worldwide|anywhere|globally|global(ly)? distributed|any (country|location|time ?zone)|"
                       r"no (geographic|location) restrictions|from any", re.I)


def norm(name: str) -> str:
    return co.key_of(name)


def squash(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def quotes(home: str) -> dict:
    """company key -> pay_ceilings.json entry (the highest monthly USD pay each company ever quoted the user)."""
    out = {}
    for e in enrich.read_json(js.fix_path(home) / "recruiters" / "pay_ceilings.json", {}).values():
        for k in {norm(e.get("name", "")), squash(e.get("name", ""))}:
            if k:
                out[k] = e
    return out


def quote_gate(company: str, ceil: dict, floor: float) -> str:
    """The reason the role is held back when the company's own quotes to the user top out under the floor, else ""."""
    q = ceil.get(norm(company)) or ceil.get(squash(company))
    mx = (q or {}).get("max_usd_month")
    if q and mx and floor and mx < floor:
        return f"{q.get('name')}'s highest quote to you was ${mx:,}/month, under your ${round(floor):,} floor"
    return ""


def hires_worldwide(elig: dict | None) -> bool:
    """True when the eligibility classifier found the JD explicitly open (category 1) and quoted worldwide wording."""
    e = elig or {}
    return e.get("category") == 1 and bool(WORLDWIDE.search(e.get("evidence") or ""))


def people_level(home: str) -> dict[str, str]:
    """company key -> brazil | latam | non_us | none, from the cached people-check verdicts (judged method only)."""
    out = {}
    for k, v in co.people_checks(home).items():
        if v.get("method") in co.METHODS and v.get("level"):
            out[k] = v["level"]
    return out
