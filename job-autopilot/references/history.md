# Why the autopilot works the way it does

The record behind the rules in `SKILL.md`. Nothing here is an instruction; it explains the ones that look arbitrary
so they aren't "simplified" away.

## Sources and lanes

- **The whole HTTP lane runs, every run.** The 2026-10-14 run fetched three sources of 29 and silently lost every
  direct-hire source (HN Who is hiring, YC, the a16z board, the Getro and Consider VC boards, the companies' own
  boards). The run report now marks unfetched HTTP sources "not run", and a run that shows any must say so. The
  user's goal (2026-10-15) is roles at the companies that pay, found where they post themselves, not through
  staffing or nearshore middlemen.
- **The browser lane always runs.** On 2026-10-02 a run applied only to Himalayas listings because the HTTP feeds
  passed the sweep's `max_new_roles` cap within a minute and the browser lane never started. Inside the autopilot
  the role cap is ignored and every browser task of the configured mode runs under its page and wall-clock caps;
  a task is a coverage gap only for Chrome being unavailable, a CAPTCHA or login wall, or the wall-clock cap. A
  Work at a Startup login wall means the extension is on a browser that isn't signed in (the user's Windows Chrome
  is, since 2026-10-15) or the session expired; that account unlocks about 2,900 YC engineering jobs.
- **Two lanes get extra depth (2026-10-27).** Both interviews so far came from a Lever Google-shard posting (25
  hours old at fill time) and a LinkedIn Jobs posting (3 hours old). `jobsweep plan` therefore runs LinkedIn Jobs
  with every keyword and the direct-hire shards (`ats_lever`, `ats_greenhouse`, `ats_ashby`) with the top
  phrasings, each lane with its own minutes budget (`sweep.lane_caps`), and the HTTP lane's `ats_search` repeats
  the direct-host searches without Chrome, with real posting dates. The run report's outcomes table decides where
  the next depth goes.
- **No people check of its own.** The autopilot stopped running a country check on 2026-10-27 when the tier
  system that needed it was removed. It still reads the cached verdicts in `people_checks.json` (written by the
  sweep's country check and by recruiter-outreach) for the location override.

## Ranking

- **No tiers (2026-10-27).** The target tiers of 2026-10-14 (a pay estimate from the company's type,
  headquarters, sibling postings and funding, a people check per run, and an A/B/C rank built from them) were
  removed at the user's request: too complex to trust. What is left is evidence the role or the company gave
  itself: posted pay (score and floor), the company's own quotes to the user (`recruiters/pay_ceilings.json`,
  the `pay_gate`), and the location override. `target-tier-research.md` keeps the study for the record.
- **Nothing is skipped for its company type (2026-10-27).** The 2026-10-15 rule skipped staffing firms,
  marketplaces and task platforms; then a task platform led to an interview, and the rule and the company types
  behind it are gone. `company_types.json` and `paytier_backtest.json` are leftovers and no longer read.
- **Freshest first, not best score first (2026-10-27).** The interviews came from roles that were hours to a day
  old when the form was filled, so `queue` sorts by posting day, newest first; within a day, roles the user
  accepted in the explorer come first, then score, with unscored roles last. A role whose posting has no date is
  dated by the first sweep that saw it. `max_minutes: 0` means the loop runs the queue down. The 2026-10-02
  round-robin across sources (`queue_order: balanced`, 25-point gap guard) is kept as an option.
- **Pacing.** Only LinkedIn is paced by count and gap (one account on one site that throttles bursts). ATS
  vendors, email and companies' own sites have no count or gap since 2026-10-27 (user rule), only the friction
  stop: a CAPTCHA or block halts that host for the rest of the run.
- **Outcomes (2026-10-27).** `autopilot outcome` records what became of an application on its ledger entry, with
  the source lane, so the run report ranks lanes by interviews rather than rows. A company with an `interview` or
  `offer` outcome is exempt from `company_cooldown_days` and `per_company_per_run` for its other target-title
  roles: a fintech's Forward Deployed Engineer roles for two LATAM cities were the first case.

## Submitting

- `auto_submit` has been `true` since 2026-10-27. Before that (2026-10-10 to 10-27) the skill never submitted.
- Emailed verification codes (Greenhouse's 8-character code) are read from the user's Gmail by the fill subagent
  itself since 2026-10-28 (user rule). SMS codes, CAPTCHAs, assessments and account walls stay human-only.
- Enter inside a Greenhouse, Lever or Ashby field submits the form; it happened on 2026-10-01.

## Token cost (2026-10-28 measurement)

A full run (185 subagents) cost about $330 at list prices. The main conversation alone was ~$150: 552 turns at a
300k+ token context on Fable, doing nothing but running scripts and relaying one-line results. The fill
subagents were ~$90 (48 agents, 43 turns each, a 186 KB answer bank read truncated, screenshots after every
scroll), the browser sweep ~$30 (97k output tokens per agent, mostly re-sent JS snippets and screenshot-returning
waits), tailoring ~$26 (331 `previous_tailored` paths per prompt), and the three JD passes ~$35. The changes of
2026-10-28 (packets instead of pasted JSON, subagents recording their own results, `next --n`, the compact answer
bank, inline summaries, Haiku for eligibility and fit, snippet-once in the browser lane, `plan --brief`) target
each of these; the main-model note at the top of `SKILL.md` targets the largest.
