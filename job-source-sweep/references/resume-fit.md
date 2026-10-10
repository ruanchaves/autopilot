# Resume fit

This reference has two parts: the rubric file the main conversation writes once per resume, and the loop each batch subagent follows.

## A. Writing `<home>/fit-rubric.md` (main conversation, once per resume)

`enrich-queue --stage fit` prints `rubric_needed: true` when the rubric is missing, or when it was written for a different version of the resume. It also prints `resume_sha`. When that happens:

1. Read the resume at `config.resume`.
2. Write `rubric_path` with the structure below. The first line must be exactly `<!-- resume_sha: <resume_sha> (generated from <file name>) -->`, because the script uses it to tell whether cached ratings are still valid.

```
<!-- resume_sha: ... -->
# Resume-fit rubric

Resume: <resume path> (read it once at the start).

Candidate snapshot (from the resume):
- <headline role>, <years> years, <location / timezone>, remote or not.
- Core: <the 4-6 capability areas the resume proves, with named tools>.
- Stack: <languages, frameworks, clouds, data tools>.
- Delivery: <scope, e.g. owns pilots end-to-end, mentors N, people management yes/no>.
- Domains: <industries>.
- Languages: <spoken languages and level>.
- Not shown: <adjacent skills JDs often require that the resume lacks: other clouds, infra, other
  main languages, CV/robotics, pre-training, data engineering, management, PhD...>.

Verdicts:
- green: the role's core is <the candidate's core area> at <their seniority band>, and the must-have
  requirements are mostly covered. Missing items are nice-to-haves or easily transferable.
- yellow: a role in the candidate's field that they mostly qualify for, with real gaps in some REQUIRED
  items: <tailor: other main language, heavy infra/MLOps, a different sub-field, more years or seniority
  than shown, a domain they lack, a customer-facing/forward-deployed slant>.
- red: a clear mismatch. Either the role's core is outside the field (<list neighbouring fields>),
  OR it centres on skills from "Not shown", OR it is entry-level, OR it requires a spoken language
  they lack, OR it is not an engineering role.

Judge by the JD body, not only the title. Be decisive: use yellow for genuine partial fits, not as a default.
```

Keep it specific to this resume. The "Not shown" list and the yellow and red examples are what keep verdicts consistent across batches.

## B. Batch loop (subagent)

1. Read `<home>/fit-rubric.md` and the resume it names, once.
2. Read the batch file, a JSON list of `{id, key, file, title, company}`. Each `file` is the JD text.
3. Go through the entries in order. For each one:
   - Read the JD once and decide.
   - Append the line to `fit_out_<n>.jsonl` right away, with `python -c` and `json.dumps`.
   - Never re-read a finished file or revisit earlier entries.

```json
{"id": "<id>", "fit": "green", "matches": "<max 120 chars: strongest overlaps>", "gaps": "<max 160 chars: missing requirements, or 'none significant'>", "reason": "<max 160 chars: one-sentence verdict>"}
```

`fit` is green, yellow or red.

4. At the end, check that there is exactly one valid line per input entry, and fix any missing or duplicate lines.

Final message: `fit batch <n>: <count> - green:<g> yellow:<y> red:<r>`.
