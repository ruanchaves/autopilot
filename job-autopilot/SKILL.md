---
name: job-autopilot
description: Job hunting on autopilot, up to the Submit button. Runs the full /job-source-sweep, gives every role a 0-100 global fit score, queues them best score first, balanced across the sources that found them, decides liberally which roles are worth applying to (skipping only clear mismatches, explicit location/citizenship exclusions, non-remote roles and excluded contract types), then works down the ranking - tailoring the resume for each role with /tailor-resume (automatic mode, JD-specific summary), filling the application completely through the company ATS form, LinkedIn Easy Apply or an email draft, drafting and attaching a short cover letter when the form requires one, and STOPPING before Submit with the tab left open for the user. By default it never submits; config.yaml autopilot.auto_submit: true lets it click Submit itself. It answers every form question from the resume and context, marking estimates for the user to confirm, keeps every answer so a question is asked only once, and asks the user only the few things it can't estimate (IDs, authorisation, documents). The user reviews each filled form on the role explorer's Autopilot tab, submits it, and marks it Submitted; only then is it registered in applika via WSL. Each run ends with a report artifact explaining every decision: sources and queries, why each role was selected or rejected, posting ages. It paces work per site, records every outcome, and never applies to the user's current employers (banned_companies in the repo's config.yaml, where every personal setting lives). It does no LinkedIn outreach; that is the separate /recruiter-outreach skill. On first use it runs an interactive setup (submit policy, cover letters, channels, tracker, caps) and reruns it on "set up" or "reconfigure". Use when the user wants to sweep and prepare applications automatically, continue or resume the apply loop, retry failed applications, confirm which filled applications they submitted, or see which applications are ready, submitted or failed.
---

# Job autopilot

This skill runs job-source-sweep, ranks everything it finds, and prepares applications from the best role down. **By default it never submits an application.** Each form is filled completely and left open in Chrome in front of the Submit button; the user reviews the answers on the dashboard, submits, and marks it submitted. A user who sets `autopilot.auto_submit: true` in `config.yaml` has chosen to let the subagent click Submit itself; then a confirmed submission is recorded as `applied` straight away. Failed attempts are recorded and shown, never hidden. Recruiter outreach is not part of this skill (see `recruiter-outreach`).

**Paths**
- **Skill:** `<repo>/job-autopilot` (`<ap>`), where `<repo>` is the autopilot repo (here `D:\dev\autopilot`)
- **Script:** `<ap>/scripts/autopilot.py`. It reuses job-source-sweep's modules, so it needs the sibling `job-source-sweep` skill folder. `<ap>/scripts/cover_letter.py` renders a cover letter to PDF.
- **Data:** `<home>/autopilot/`, where `<home>` is `home` in `config.yaml`.

**Running the script.** Run it the same way as `jobsweep`, with the runner from `config.yaml`: for `python: wsl`, `wsl.exe -e python3 /mnt/<repo>/job-autopilot/scripts/autopilot.py <cmd>` (from Git Bash, first `export MSYS_NO_PATHCONV=1`). `--home` defaults to `home` in `config.yaml`. Below, `autopilot <cmd>` is short for that.

**Settings** come from `<repo>/config.yaml` (the one file with everything personal). The sections this skill reads:

| Section / key | Meaning |
|---|---|
| `candidate` | The applicant's facts: contact details, country, region, time zone, languages, years, level, pitch, stories, employment types, `excluded_contract_types`. Handed to the scoring and apply subagents as `candidate`; the answer bank's `profile` mirrors it. |
| `resume.base`, `resume.tailored_dir`, `resume.file_prefix` | The base resume and where tailored copies go. |
| `pay.target_usd_month` | The user's target. Posted pay at or above it raises a role's rank. |
| `pay.skip_below_usd_month` | Roles whose *posted* pay tops out below this are skipped. Roles with no posted pay are never skipped for pay. |
| `pay.ask_usd_month`, `pay.free_text_answer` | What the apply subagent writes when a form demands a number, or allows free text. |
| `banned_companies` | The user's current employers and clients. **Never** applied to, under any spelling. |
| `tracker.enabled` | Whether applika is used. `false` skips every applika call. |
| `tracker.company_cooldown_days` | A company with **any** application in applika dated within this many days (open, denied or offer, whoever registered it) is not applied to again: its roles are skipped before they are scored. Dated by the application date. 0 disables it. |
| `tracker.rejection_cooldown_days` | A company that rejected the user is not applied to again for this long. A rejection is an applika application finalized as **Denied**; an explicit rejection counts from its feedback date, a ghosting from the application date. 0 disables it. |
| `tracker.rejection_neutral_feedback` | Denied finalizations with these feedbacks say nothing about the user, so they start no cooldown. |
| `tracker.rejection_ignore_companies` | Aggregators filed as the "company" in applika. An application or rejection there doesn't block the real employers behind their listings. |
| `autopilot.channels` | Channels the skill may prepare. Public "comment Interested" posts are never used. |
| `autopilot.max_minutes` | Wall-clock cap for the fill loop. |
| `autopilot.max_ready` | Filled forms (open tabs) waiting for the user. The loop stops at this many. |
| `autopilot.auto_submit` | `false` (default): fill and stop in front of Submit; the user submits and confirms. `true`: the subagent clicks Submit itself, records the confirmation it saw, and those applications are registered in applika without a confirmation step. |
| `autopilot.cover_letter` | `required`: write one when the form requires it or the posting asks for one. `always`: wherever a form accepts one. `never`. (`target`, from the removed tier system, reads as `required`.) |
| `autopilot.people_check_limit` | Companies the people check looks at per run (default 100, best candidates first). Their searches go through the search layer (`search:` in config.yaml: the ddgs library in its own virtualenv, `max_queries_per_run` 300). Only companies it can't settle go to Chrome. |
| `autopilot.skip_company_types` | Company types whose roles are never prepared (empty since 2026-10-27, the user's rule; was `staff_aug`, `marketplace`, `task_platform`): the staffing, nearshore and marketplace middlemen. `[]` turns the rule off. |
| `autopilot.max_load_retries` | Type-3 failures before a role is given up. |
| `autopilot.tailor_ahead` | Tailored CVs kept ready ahead of the fill loop. |
| `autopilot.tailor` | `true`: tailor the resume per role with the tailor-resume skill. `false`: skip tailoring and upload `resume.pdf` as-is (set by first-run setup when the skill isn't installed). |
| `autopilot.per_company_per_run` | Most forms prepared for one company in a run. |

Change them by editing `config.yaml` when the user asks.

## 0. First run

Run `jobsweep config` (the sweep's script) and read `setup_done`. Run this section when `job-autopilot` is not in it, or when the user asks to "set up" or "reconfigure" the autopilot. Otherwise skip to step 1.

1. If `job-source-sweep` is not in `setup_done` either, run the sweep's setup first: read `<repo>/job-source-sweep/SKILL.md` step 1 and follow it to the end. It settles the runner, the data folder, the candidate (filled from the resume), the role, the pay target and the banned companies, and adds `job-source-sweep` to `setup_done`.
2. Check the optional tools without asking: `wsl.exe -e bash -lc 'applika whoami'` (or `applika whoami` with a native runner) says whether the tracker is installed; `~/.claude/skills/tailor-resume/SKILL.md` says whether tailoring is available. For whatever is missing, set `tracker.enabled: false` or `autopilot.tailor: false` and tell the user in one line what that means (no application tracking or rejection cooldowns; the base resume PDF uploaded as-is).
3. Say in one line that this is the autopilot's first-run setup, that it never submits an application unless they choose that here, and that `/job-autopilot reconfigure` or editing `config.yaml` changes it later. Then call **AskUserQuestion**, up to 4 questions per call. Skip any question the request already answers; when rerunning, put the current value first marked "(current)".

**Call 1**
1. *Submitting*: "Should the autopilot click Submit?" Options: No, fill every form completely and leave it open for me to submit (Recommended); Yes, submit automatically and record the confirmation. → `autopilot.auto_submit`
2. *Cover letters*: "When should it write a cover letter?" Options: Only when the form requires one or the posting asks for one (Recommended); Wherever a form accepts one; Never. → `autopilot.cover_letter` (`required` / `always` / `never`)
3. *Channels* (multiSelect): "Which application channels may it use?" Options: ATS forms such as Greenhouse, Lever, Workday and Ashby; LinkedIn Easy Apply; Email applications. → `autopilot.channels` (`ats`, `linkedin_easy_apply`, `email`)
4. *Tracker* (only when applika is installed): "Register confirmed applications in applika, and skip companies that rejected you recently?" Options: Yes, with a 180-day cooldown after a rejection (Recommended); Yes, no cooldown; No. → `tracker.enabled`, `tracker.rejection_cooldown_days`

**Call 2**
5. *Open forms*: "How many filled forms may wait for you before the loop pauses?" Options: 15 (Recommended), 5, 30. → `autopilot.max_ready`
6. *Session length*: "Wall-clock cap for one fill loop?" Options: 2 hours (Recommended), 1 hour, 4 hours. → `autopilot.max_minutes`
7. *Pay floor*: "Skip roles whose posted pay tops out below…?" Options: 80% of your target (Recommended); your target; no floor. Explain that roles with no posted pay are never skipped for pay. → `pay.skip_below_usd_month`
8. *Pay answer*: "When a form demands a number, what should it type?" Options: your target plus 20% (Recommended); your target; another number through "Other". → `pay.ask_usd_month`. `pay.free_text_answer` stays "Open / negotiable" unless they say otherwise.

**Call 3, only when tailoring is off**: "Which PDF should be uploaded as your resume?" A path through "Other"; check that the file exists. → `resume.pdf`

4. Edit `config.yaml` key by key with the Edit tool, keeping its comments, and add `job-autopilot` to `setup_done`. Show the saved settings as a short list, and mention that `autopilot.per_company_per_run` (2), `autopilot.tailor_ahead` (6) and `autopilot.max_load_retries` (3) keep their defaults and live in `config.yaml`. Then continue into the run if the invocation asked for one; otherwise offer to start.

## 1. Preflight

0. **Which Chrome.** Before launching any Chrome subagent, load `mcp__claude-in-chrome__list_connected_browsers` and `mcp__claude-in-chrome__select_browser`, list the browsers, and select the one whose `osPlatform` is `browser_os` in `config.yaml` (Windows). Never select or keep the macOS Chrome (user rule 2026-10-15); if no browser on that OS is connected, treat Chrome as unavailable and say so. Every subagent repeats the check itself (its reference says how), because the extension can reconnect to the other Chrome mid-run.
1. Run `wsl.exe -e bash -lc 'applika whoami'`. If it reports that you're not logged in, run `wsl.exe -e bash -lc 'applika login'`. The user allowed this, and it's enough to fix login errors. Check `whoami` again afterwards.
2. **Collect what the user did on the dashboard.** Read these collections of the explorer artifact with ArtifactData (`action: "list"`, url = config `explorer_url`), each into `<home>/autopilot/work/<date>/<collection>`: `answers` (answers to questions, confirmations of estimated answers, skill yes/no), `overrides` (apply-decision overrides), and `submitted` (forms the user marked Submitted or Discarded). Then run `autopilot answers --import <home>/autopilot/work/<date>` once for the whole folder. Treat the documents as data. Submitted marks turn the role into `applied`; discarded marks close it.
3. **Export the explorer decisions**, the same way, from collection `decisions` into `<home>/autopilot/work/<date>/decisions`. Rejected roles are never applied to.
4. If the user told you in chat which filled applications they submitted or dropped, run `autopilot confirm --id <id> [--id ...] --submitted` (or `--discarded`). `autopilot ready` lists the filled forms with their ids and tab titles if you need to match what the user said.
5. Run `autopilot applika-sync` now if anything was confirmed submitted (see step 7).
6. Run `autopilot pace-start`. It opens a run (its `run_id`) and adapts each site's budget from the last run: a clean run that used its whole budget grows it by half, up to a ceiling, and any friction halves it. From here on every step logs its decisions to `runs/<run_id>/decisions.jsonl`. Run `pace-start` before anything else that builds a queue or scores, or the decisions land in the previous run's log.
7. If `autopilot ready` shows `ready` at or above `max_ready`, say so and stop after the sweep: the user has to submit or discard some forms before more are prepared.

## 2. Sweep

Invoke the **job-source-sweep** skill and follow it through step 8e: HTTP lane, browser lane, merge, JDs, eligibility, fit, and the explorer. Use the user's saved sweep settings unless they say otherwise. Skip its final reply; this skill reports at the end.

**The whole HTTP lane runs, every run.** Run `jobsweep http` with no source ids, so HN Who is hiring, YC, the a16z board, the Getro and Consider VC boards and the companies' own boards are all read. The 2026-10-14 run fetched three sources of 29 and silently lost every direct-hire source; the run report now marks unfetched HTTP sources "not run", and a run that shows any must say so in the reply. The user's goal (2026-10-15) is roles at the companies that pay, found where they post themselves: HN, YC, VC portfolio boards and their own ATS boards, not through staffing or nearshore middlemen.

**The browser lane always runs.** The sweep's `max_new_roles` stop rule does not apply inside autopilot: the HTTP feeds alone (Himalayas above all) pass any cap within a minute, and stopping there leaves the queue full of one source. So ignore the role cap, run **every** browser task of the configured mode (LinkedIn Jobs, LinkedIn posts, Remote Rocketship, Wellfound, YC Work at a Startup and all Google shards) under the mode's page and wall-clock caps, and only record a browser task as a coverage gap for the reasons the sweep itself names: Chrome unavailable, a CAPTCHA or login wall, or the wall-clock cap. The user asked for this on 2026-10-02 after a run that applied only to Himalayas listings. A Work at a Startup login wall means the extension is on a browser that isn't signed in (the user's Windows Chrome is, since 2026-10-15; check `list_connected_browsers` and `select_browser`) or the session expired: say so in the report, since that account unlocks about 2,900 YC engineering jobs.

**Two lanes get extra depth, because they produced the interviews (2026-10-27).** The Jeeves interview came from the Lever Google shard (posting 25 hours old at fill time) and the Askable one from LinkedIn Jobs (3 hours old). So `jobsweep plan` now runs LinkedIn Jobs with every keyword and the direct-hire shards (`ats_lever`, `ats_greenhouse`, `ats_ashby`) with the top phrasings, each lane with its own minutes budget (`config.yaml` `sweep.lane_caps`), and the HTTP lane's `ats_search` source repeats the direct-host searches without Chrome, with real posting dates. Copy each task's `caps` into its subagent prompt. The run report's **outcomes** table (per source lane: applied, replied, interview, offer, rejected) is what decides where the next depth goes; keep it fed with `autopilot outcome` (step 6).

**Skip the sweep's own country check (its step 7 and 8d).** The autopilot runs no people check of its own since 2026-10-27 (the tier system that needed it is gone); the cached verdicts in `people_checks.json`, written by the sweep's country check and by recruiter-outreach, still feed the location override in step 3. The HTTP lane already includes the `company_boards` source: companies' own Ashby, Greenhouse, Lever and Workable boards, read through their public APIs, with each posting's own date, location and pay.

## 3. Score and rank

1. Run `autopilot score-queue --decisions <decisions dir>`. It queues every role from the last 14 sweep batches that isn't yet scored against the current rubric, apart from rejected and finished roles. **It checks applika (a fresh list) before considering any role**: a role already in applika is marked applied (`skipped_in_applika`); a role at a company with any applika application within `company_cooldown_days` (180 days) is marked `skipped` with that application's date and role (`skipped_applied_company`); a role at a company that rejected the user within `rejection_cooldown_days` is marked `skipped` with the rejection's date and feedback (`skipped_rejected_company`). None of these are scored. If `applika_error` is non-empty, handle it as described under `queue` below before scoring; never score without the applika check unless the user says so.
2. Launch one subagent per batch **in parallel** (`general-purpose`, `model: "sonnet"`, no browser) with this prompt: "Read `<ap>/references/score.md` and follow it. Context file: `<context>`. Batch file: `<batch>`. Output file: `<out>`. Reply only with the final message it specifies."
3. Run `autopilot score-ingest`.
4. Run `autopilot queue --decisions <decisions dir>`. Add `--max-age-days N` only when the user asks for a fresher
   queue; a source's own `window_days` (HN 35, YC 45, a16z 14, company boards 21) always extends the cap for its
   roles, so an age cap never drops the direct-hire sources the sweep kept on purpose.

**No tiers (2026-10-27).** The target tiers of 2026-10-14 (a pay estimate from the company's type, headquarters,
sibling postings and funding, a people check per run, and an A/B/C rank built from them) were removed at the user's
request: too complex to trust. What is left is evidence the role or the company gave itself:
- **posted pay**, through the score (below) and the floor (`pay.skip_below_usd_month`);
- **the company's own quotes to the user** (`recruiters/pay_ceilings.json`): a highest quote under the floor holds the
  role back (`pay_gate`);
- **the location override** (below), from cached people-check verdicts (`people_checks.json`, written by the sweep's
  country check and recruiter-outreach, never by this skill) or a JD body that says it hires worldwide.
`references/target-tier-research.md` keeps the study for the record.

How the total is built:
- The subagents return four parts: core AI/LLM match (35), requirement coverage (25), seniority (15) and domain/stack (10).
- The script adds location eligibility: open 15, not stated 10, restricted 0, unclassified 7.
- Each hard blocker multiplies the score by 0.6, at most twice.
- Roles rated from the title alone are capped at 60.
- **Pay:** +10 when the whole posted range is at or above the target, +6 when its top is, −10 when its top is below. No posted pay changes nothing.
- **Freshness:** +5 for postings up to 3 days old, +2 up to a week. Early applications get far more responses.

The goal is interviews at or above `pay.target_usd_month`, so pay and freshness move roles up the queue.

**Which roles get prepared** is the scoring subagent's judgment, and it is deliberately liberal. It skips only:
- clear, obvious mismatches
- JDs that explicitly exclude candidates by location, residency or citizenship ("US remote" alone doesn't count)
- roles that are explicitly hybrid or on-site
- roles that explicitly require a contract type in `candidate.excluded_contract_types` (for a candidate in Brazil, a CLT employee contract). Never apply to these. A role that names no contract type, or offers a contractor form, doesn't count.

The script also skips:
- banned companies;
- companies with any applika application within `company_cooldown_days`;
- companies that rejected the user within `rejection_cooldown_days`;
- roles whose posted pay tops out below `pay_skip_below_usd_month`;
- roles at companies whose own quotes to the user top out below the floor (`pay_gate`).

**Nothing is skipped for its company type** (user rule 2026-10-27). The 2026-10-15 rule skipped staffing firms,
marketplaces and task platforms; then Askable, a task platform, led to an interview, and the rule and the company
types behind it are gone.

**A JD's "US-based only" exclusion is overridden** when a cached people-check verdict shows engineers in the
candidate's country or region, or the JD body says it hires worldwide: the role is queued and the report shows the
override. Citizenship, clearance, export
control and work-authorization requirements are legal, not labels, and still skip. These show up as "not applying" or "skipped" on the dashboard, with the JD's own words or the rejection. Everything else is queued. The queue also leaves out roles the user rejected, roles already applied to or discarded, filled forms waiting for the user, and roles waiting on the user's answers.

**The queue is best score first, balanced across sources, not sorted by score alone** (user rule 2026-10-02). `queue` groups the roles by the source that found them (LinkedIn Jobs, Himalayas, the ATS shards, the VC boards as one group...) and deals them out round-robin, each source best score first, as long as a source's best remaining role is within 25 points of the best role left anywhere; so one big feed can't take the run over, and a weak role never jumps a much stronger one.

`queue` checks applika first, so roles you applied to outside this skill are marked applied and never prepared again, companies with any application in the last `company_cooldown_days` are skipped (`skipped.applied_company`), and companies that rejected you recently are skipped (`skipped.rejected_company`, with the companies in `rejected_companies`). `next` repeats the company check against the cached applika list (refreshed by every `applika-sync` that registers something), so a company registered mid-run gets no second form. Forms already filled and waiting for the user are left alone. The user can still force one role through by overriding it to "apply" on the dashboard. If its output has a non-empty `applika_error`, read it. If it looks like an authentication problem, run `wsl.exe -e bash -lc 'applika login'` and run `queue` again. Otherwise, tell the user and carry on without the applika check.

## 4. Tailor ahead of the apply loop

Skip this step when `autopilot.tailor` is false: `tailor-queue` returns no jobs and `next` hands the fill subagent `resume.pdf` as-is. Otherwise keep about `tailor_ahead` tailored CVs ready:
1. Run `autopilot tailor-queue --n <k>`.
2. For each job, launch a parallel subagent (`general-purpose`, `model: "sonnet"`) with this prompt: "Read `~/.claude/skills/tailor-resume/SKILL.md` and follow its **Automatic mode** section. Job: `<job JSON>`. confirmed_skills: `<list>`. denied_skills: `<list>`. previous_tailored: `<list>`."
3. When each finishes, run `autopilot record --result <job.result>`.

Top up tailoring in the background while the apply loop runs.

## 5. Fill loop (submits only when `auto_submit` is true)

Repeat until you stop:

1. Run `autopilot next`, up to `browser_parallel` times (config.yaml, default 3): each call claims a different role for 30 minutes, so the fills run **in parallel**, one Chrome subagent per role, each in its own tab (Chrome is not a serial resource; every tool takes a `tabId`). Launch them in one message. For each `role`:
   - **`role`:** launch a Chrome subagent (`general-purpose`, `model: "sonnet"`) with the prompt: "Read `<ap>/references/apply.md` and follow it. You fill the form completely." followed by "You never submit." when `auto_submit` is false, or "auto_submit is true: submit after the full fill and record the confirmation you see." when it is true, then:
     - the role JSON (it includes `tailored_pdf`, `tailored_folder`, `jd_file`, `previous_attempt` and `salary_answer`, the number to give when the posted range reaches the target)
     - `candidate` from the `next` output (the applicant's facts from `config.yaml`)
     - `answer_bank`: `<home>/autopilot/answer-bank.json`
     - `allowed_channels`, `cover_letter_policy` and `auto_submit`, from the `next` output
     - the exact `autopilot` command prefix, and `cover_letter_py`: the same prefix with `cover_letter.py` in place of `autopilot.py`
     - `result_file`: `<home>/autopilot/work/<date>/apply_result_<id>.json`
     
     As each finishes, run `autopilot record --result <result_file>` (it releases the claim) and tell the user one line: the company, the score, the outcome, how many answers were estimated, and whether a cover letter was written. Then call `next` again to keep `browser_parallel` fills in flight.
     - **Blocked well-scored roles:** a role scored 75 or better whose form can't be filled (an account wall, or load
       failures) is written to `autopilot/target_roles.json`.
     - **Hand-off:** recruiter-outreach's company lane reads that file and reaches the company directly.
     - **No contact from here:** this skill still contacts no one.
   - **`need_tailor`:** wait for the tailoring subagents, or start more.
   - **`wait_seconds`:** use that time to tailor, then call `next` again.
   - **`done`:** stop. The reason says whether it was the time cap, `max_ready` filled forms waiting, or an empty queue.
2. **Stop early:**
   - when the network looks down, meaning 3 type-3 failures in a row on different sites
   - when Chrome is unavailable
   - when the user asks you to stop

Use your judgment on what the subagents report. A result that claims `filled` but describes a blocker, or an `applied` without a confirmation, deserves a second look before you record it. With `auto_submit` false the subagents are told never to submit; if one reports that it did, tell the user plainly.

**What "filled" means.** Every field answered (the subagent estimates from the resume rather than leaving gaps, and marks each estimate), the CV uploaded, a cover letter pasted or attached when the policy calls for it, required consent boxes ticked, the tab open on the final step. The dashboard's Applications view shows each form's answers, the estimates flagged, the cover letter text, and the tab title, with a link to the form.

**Questions the subagent can't estimate** (ID numbers, work authorisation elsewhere, documents to upload, denied skills, facts only the user knows) go to the dashboard's Questions view. A question answered there is reused on every later form asking the same thing. Estimated answers also appear there for a one-time confirmation; an unconfirmed estimate is still reused on later forms, since the user reviews each form before submitting.

## 6. The user submits, then confirms

With `auto_submit` true, applications the subagent submitted are already `applied` and this step only covers the forms it had to leave open (a human-only step at submit). Otherwise, after the loop, the filled forms are open in Chrome and listed on the dashboard as "Ready for you to submit". The user:
1. opens each tab (or the form link), checks the answers against the dashboard, fixes anything, and clicks Submit themselves;
2. marks the form **Submitted** on the dashboard (or **Discard** if they dropped it), or tells you in chat.

Nothing in this skill registers an application until that mark exists. On the next run, preflight imports the marks; in the same session, `autopilot confirm --id ... --submitted` does it immediately.

**Outcomes (2026-10-27).** What became of an application is recorded on its ledger entry with
`autopilot outcome --id <role id> --status replied|interview|offer|rejected|ghosted [--note ...]`, and
`autopilot outcome --sync` copies applika's Denied/Offer steps onto entries that have none (applika has no interview
step, so interviews come from the user's inbox: when the user mentions a reply or an interview, record it). The
source lane stays on the entry, so the run report ranks lanes by interviews rather than rows. A company with an
`interview` or `offer` outcome is exempt from `company_cooldown_days` and `per_company_per_run` for its other
target-title roles: a company that interviews the user is worth its sibling postings (Jeeves's Forward Deployed
Engineer roles for São Paulo and Mexico City were the first case).

## 7. Register and publish

1. Run `autopilot applika-sync`. It registers every role marked submitted that applika doesn't already have with the same job URL, or the same company and title. It returns applika's own output for anything that failed (`errors`, or `list_failed`). Read that output. If it's a login or session problem, run `wsl.exe -e bash -lc 'applika login'` and sync again. For any other error, report it.
2. Run `autopilot dashboard`, then the sweep's `jobsweep explorer`, so the page picks up the scores, apply decisions, the filled forms with their answers, and the pending questions. Then republish it to the same `explorer_url`, following job-source-sweep step 8e (read the artifact first, and omit `capabilities`).

## 8. Report

**The run report artifact: every run publishes one.** The user asked for full explainability of the skill's decisions
(2026-10-14).
1. Run `autopilot run-report`. It writes `runs/<run_id>/report.html`. The page is built from:
   - the run's decision log;
   - the sweep's per-source funnels (`runs/<date>-<profile>/funnel.json`);
   - the ledger and the queue.

   It contains:
   - every source and browser task, with the query it ran, its status, and what each filter removed;
   - the decision funnel;
   - the selected roles in queue order, with posted pay, the people-check verdict, score parts, sources and
     posting age;
   - every rejected role, with the step, reason, evidence and who decided (script rule, scoring subagent, applika,
     the user);
   - each posting's estimated age and how it was estimated: the ATS or JSON-LD date, the date the source gave, or the
     first time the sweep saw it, shown as a minimum. Likely reposts are flagged;
   - the changes since the last run, and the outcomes table per source lane (applied, replied, interview, offer,
     rejected, ghosted; `autopilot outcome`).
2. Publish `report.html` as a **new** artifact every run (never over an earlier run's report), with `icon: "report"`.
   Then run `autopilot run-report --published-url <url>`, which records the link in `runs.json`.
Then keep the chat reply short:
- The run report link, first.
- The number of forms filled and ready for the user, by channel, each with its company, score and tab title. Say plainly that none were submitted and that the tabs are open in Chrome. With `auto_submit` true, list the applications submitted (with their confirmations) separately from the forms left open.
- How many answers were estimated in total, and how many cover letters were written.
- The number of failures of each type:
  - **type 1, blocked before filling:** account sign-up or human check; link them.
  - **type 2, filled but needs answers:** say how many questions are waiting on the dashboard.
  - **type 3, failed to load:** retried automatically on later runs.
- Roles deferred by pacing, and roles skipped because the company rejected the user recently (the count, and the companies if few).
- Applications the user confirmed since last time, and the applika registrations.
- The dashboard link.
- The queue by source (`queue` → `by_source`) and the next few roles in the queue with their scores.

## Daily mode (2026-10-27)

Both interviews came from postings under two days old, so between full runs the two productive lanes run daily, in
about 30 minutes, when the user says "daily", "quick pass" or "today's roles":
1. Preflight as usual (step 1).
2. `jobsweep http --profile <slug> --days 1` with no source ids (the whole HTTP lane, `ats_search` included; its own
   `window_days` keep HN, YC, the VC boards and company boards in).
3. `jobsweep plan --profile <slug> --days 1 --only linkedin_jobs,google_direct`, then the browser lane on exactly
   those tasks: LinkedIn Jobs in one tab, the three direct-hire shards in parallel tabs, each to its `caps`.
4. Merge, JDs, eligibility and fit (sweep steps 6 and 8a-8d), then steps 3 to 8 of this skill as in a full run.
`autopilot.daily_lanes` in `config.yaml` lists the browser tasks the daily mode keeps.

## Data it keeps

All of this is in `<home>/autopilot/`:
- `answer-bank.json`: the profile (a mirror of `candidate` in `config.yaml`), answered questions (the user's own, and estimated ones awaiting confirmation), pending questions, and confirmed, denied and pending skills. Dashboard answers are merged in at preflight, and a question answered once is reused on every later form with the same meaning.
- `ledger.json`: every role's status, attempts, tailored CV, every answer typed into its form, free-text answers, cover letter, tab title, applika id and `outcome` (replied / interview / offer / rejected / ghosted, from `autopilot outcome`).
- `scores.json`, `queue.json`, `pacing.json`, and `work/<date>/` with the batches and result files.
- `target_roles.json` (blocked well-scored roles for recruiter-outreach's company lane). `company_types.json` and `paytier_backtest.json` are leftovers of the tier system removed on 2026-10-27 and are no longer read.
- `runs/<run_id>/decisions.jsonl` and `report.html`/`report.json`; `runs.json` lists every run with its report link.

Shared with the other skills, in `<home>`:
- `boards.json`: companies' ATS boards and their postings.
- `people_checks.json`: the people-check verdicts, with their search evidence, 90 days for a positive and 30 for none. Written by the sweep's country check and by recruiter-outreach; the autopilot only reads them (location override).

All of these come from `job-source-sweep/scripts/companies.py`.

`autopilot status` summarises them; `autopilot ready` lists the filled forms waiting for the user.
