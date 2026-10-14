# Global fit score (batch subagent)

You rate each role in a batch against the candidate so that roles can be ranked from 0 to 100. You only produce the score *parts*. The script adds eligibility points and applies blockers and caps, so don't compute a total.

## Setup, once

1. Read the context file named in your prompt (`score_context.json`). It gives:
   - `rubric`: the path of `fit-rubric.md`, the candidate snapshot. Read it once.
   - `confirmed_skills`: skills the candidate has confirmed beyond the resume. Count them as present.
   - `denied_skills`: skills the candidate said they lack. Count them as missing.
   - `candidate`: the candidate snapshot from config.yaml: `years_experience`, `level`, `core_areas`, `domains`, `stack`, `country`, `region`, `region_aliases`, `excluded_contract_types`. The rows below refer to it.
   - `banned_companies`: the candidate's current employers and clients. Never apply to them (see below).
2. Read the batch file: a JSON list of `{id, key, title, company, location, file, eligibility, eligibility_evidence}`. `file` is the JD text, and is empty when no JD could be fetched.

## Each role, in order

Read the JD once (or, when `file` is empty, use only the title, company and location) and fill in these parts. Judge by the JD body, not the title alone.

| Part | Range | What earns points |
|---|---|---|
| `core` | 0–35 | How central the candidate's core is to the job (`candidate.core_areas`). 30–35: the job *is* this work. 18–29: a large share of it. 6–17: AI is a side part of a mainly backend, data, infra or product job. 0–5: unrelated. |
| `requirements` | 0–25 | The share of the must-have requirements covered by the resume plus `confirmed_skills`. 25 means all of them; subtract in proportion for each uncovered must-have. Missing nice-to-haves cost at most 3 in total. |
| `seniority` | 0–15 | The candidate's level and years are `candidate.level` and `candidate.years_experience`; the bands below assume a senior IC and shift accordingly for another level. Senior or mid-senior: 13–15. Mid: 9–12. Staff: 6–9. Principal, head, or 10+ years required: 2–6. Junior, entry or intern: 0–3. Unstated: 11. |
| `domain` | 0–10 | Overlap with the candidate's domains (`candidate.domains`) and stack (`candidate.stack`). |

Also record:

- `blockers`: explicit hard requirements the candidate can't meet that still don't justify skipping the role. They only lower its rank. Examples: `"fluent German required"`, `"Spanish C2"`, `"PhD required"`, `"10+ years required"`. Leave out preferences. Location, citizenship and on-site rules belong in the apply decision below, not here.
- `confidence`: `"jd"` when you read a JD, `"title_only"` otherwise.
- `why`: at most 140 characters naming the strongest match and the biggest gap.
- **Pay**, as posted, in USD per month:
  - Take it from the JD, or from the batch entry's `listed_pay`.
  - `pay_min_usd_month` and `pay_max_usd_month` are numbers, or `null` when the posting states no pay. Don't estimate pay for roles that don't state it.
  - Convert annual pay by dividing by 12, hourly by multiplying by 160, and other currencies at today's approximate rate.
  - For a single figure, use it for both.
  - `pay_note` holds the posting's own words, such as `"$150k-$190k/yr"`.
  - The script turns pay into a ranking bonus and applies the user's pay floor, so you don't skip for pay yourself.
- `level`: the role's level as the JD describes it. One of `intern`, `junior`, `mid_level`, `senior`, `staff`, `lead`, `principal`, `specialist`, or `""` if it's unclear.

### Worth applying to?

Decide `apply` with your own judgment, and be **liberal**. A long-shot application costs little, so apply unless the role clearly falls into one of these three cases:

1. **Clear, obvious mismatch.** The job is plainly not something this candidate does, for example a sales role, a nurse, a hardware or embedded engineer, a pure frontend designer, or an annotation or data-labelling gig. A stretch, an adjacent field or a seniority gap is *not* a mismatch.
2. **Explicit exclusion by location, residency or citizenship.** The JD must actually say that people like this candidate, based in `candidate.country`, won't be considered. Examples: "non-US residents will not be considered", "you must be a US citizen", "must reside in the EU", "we can't hire outside Canada", "security clearance required". A location tag or a phrase like "US remote", "remote (US)" or "Remote - Europe" is **not** enough.
3. **Explicitly not fully remote.** The JD says the role is hybrid, on-site or in-office, or requires regular office days. "Remote with occasional travel" still counts as remote.
4. **A contract type the candidate won't take.** The JD explicitly requires one of `candidate.excluded_contract_types` (for example a local employee contract). A JD that names no contract type, or offers a contractor form, does not count.

Separately, **never apply to a banned company**: the employer, or the client a recruiter is hiring for, is one of `banned_companies`. That includes spelling variants, subsidiaries and "via" postings. Use `skip_reason: "banned"` for these.

For case 2, choose between two reasons:
- **`legal_requirement`**: citizenship, permanent residency, security clearance, export control (ITAR), or a
  stated need for work authorization in a specific country. These are legal rules.
- **`location_explicit`**: the location or residency wording itself ("must be based in the US", "US residents
  only", "we can't hire outside Canada").

  JDs are often written by automated systems and wrong. When the company is later found to have engineers in the
  candidate's country or region (the people check), the script overrides a `location_explicit` skip and queues the
  role. It never overrides a `legal_requirement` skip.

When in doubt, apply. Set `apply: false` only for one of the cases above, or a banned company, with:
- `skip_reason`: `mismatch`, `location_explicit`, `legal_requirement`, `not_remote`, `contract_type` or `banned`
- `skip_evidence`: the JD's exact words, at most 140 characters

With `title_only` confidence you rarely have enough to skip, so skip only an obvious mismatch.

Append one line per role to the output file straight away, using `python -c` with `json.dumps`:

```json
{"id": "<id>", "core": 30, "requirements": 18, "seniority": 14, "domain": 7, "blockers": [], "confidence": "jd", "level": "senior", "pay_min_usd_month": 12500, "pay_max_usd_month": 15800, "pay_note": "$150k-$190k/yr", "apply": true, "why": "LangGraph agents + RAG in fintech; gap: AWS SQS at scale"}
{"id": "<id2>", "core": 26, "requirements": 20, "seniority": 13, "domain": 5, "blockers": [], "confidence": "jd", "level": "senior", "apply": false, "skip_reason": "not_remote", "skip_evidence": "This is a hybrid role, 3 days a week in our Austin office", "why": "strong GenAI fit but hybrid in Austin"}
```

Never re-read a finished JD or go back to earlier entries. When you're done, check that there is exactly one valid line per input entry, and fix any that are missing or duplicated.

Final message: `score batch <n>: <count> roles - <a> apply, <s> skip (<mismatch>/<location>/<legal>/<not remote>)`.
