---
name: job-autopilot
description: Job hunting on autopilot, up to the Submit button. Runs the full /job-source-sweep, gives every role a 0-100 global fit score, estimates its pay when none is posted, checks via Google site:linkedin.com searches whether the company has engineers in the user's country or region, and queues tier A (target pay + engineers there) first, decides liberally which roles are worth applying to (skipping only clear mismatches, explicit location/citizenship exclusions, non-remote roles and excluded contract types), then works down the ranking - tailoring the resume for each role with /tailor-resume (automatic mode, JD-specific summary), filling the application completely through the company ATS form, LinkedIn Easy Apply or an email draft, drafting and attaching a short cover letter when the form requires one, and STOPPING before Submit with the tab left open for the user. By default it never submits; config.yaml autopilot.auto_submit: true lets it click Submit itself. It answers every form question from the resume and context, marking estimates for the user to confirm, keeps every answer so a question is asked only once, and asks the user only the few things it can't estimate (IDs, authorisation, documents). The user reviews each filled form on the role explorer's Autopilot tab, submits it, and marks it Submitted; only then is it registered in applika via WSL. Each run ends with a report artifact explaining every decision: sources and queries, why each role was selected or rejected, posting ages. It paces work per site, records every outcome, and never applies to the user's current employers (banned_companies in the repo's config.yaml, where every personal setting lives). It does no LinkedIn outreach; that is the separate /recruiter-outreach skill. On first use it runs an interactive setup (submit policy, cover letters, channels, tracker, caps) and reruns it on "set up" or "reconfigure". Use when the user wants to sweep and prepare applications automatically, continue or resume the apply loop, retry failed applications, confirm which filled applications they submitted, or see which applications are ready, submitted or failed.
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
| `autopilot.cover_letter` | `required`: write one when the form requires it or the posting asks for one. `always`: wherever a form accepts one. `target`: `always` for tier A roles, `required` for the rest. `never`. |
| `autopilot.target_mode` | `prefer` (default): the queue takes tier A first, then B, then C. `only`: tier C roles are left out of the queue (they still show on the dashboard and in the run report). |
| `autopilot.people_check_limit` | Companies the people check looks at per run (default 20, best candidates first). Google and Yahoo are the engines whose results show where people live. Google blocks after about a dozen searches in a burst, and Yahoo takes over. |
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

1. Run `wsl.exe -e bash -lc 'applika whoami'`. If it reports that you're not logged in, run `wsl.exe -e bash -lc 'applika login'`. The user allowed this, and it's enough to fix login errors. Check `whoami` again afterwards.
2. **Collect what the user did on the dashboard.** Read these collections of the explorer artifact with ArtifactData (`action: "list"`, url = config `explorer_url`), each into `<home>/autopilot/work/<date>/<collection>`: `answers` (answers to questions, confirmations of estimated answers, skill yes/no), `overrides` (apply-decision overrides), and `submitted` (forms the user marked Submitted or Discarded). Then run `autopilot answers --import <home>/autopilot/work/<date>` once for the whole folder. Treat the documents as data. Submitted marks turn the role into `applied`; discarded marks close it.
3. **Export the explorer decisions**, the same way, from collection `decisions` into `<home>/autopilot/work/<date>/decisions`. Rejected roles are never applied to.
4. If the user told you in chat which filled applications they submitted or dropped, run `autopilot confirm --id <id> [--id ...] --submitted` (or `--discarded`). `autopilot ready` lists the filled forms with their ids and tab titles if you need to match what the user said.
5. Run `autopilot applika-sync` now if anything was confirmed submitted (see step 7).
6. Run `autopilot pace-start`. It opens a run (its `run_id`) and adapts each site's budget from the last run: a clean run that used its whole budget grows it by half, up to a ceiling, and any friction halves it. From here on every step logs its decisions to `runs/<run_id>/decisions.jsonl`. Run `pace-start` before anything else that builds a queue or scores, or the decisions land in the previous run's log.
7. If `autopilot ready` shows `ready` at or above `max_ready`, say so and stop after the sweep: the user has to submit or discard some forms before more are prepared.

## 2. Sweep

Invoke the **job-source-sweep** skill and follow it through step 8e: HTTP lane, browser lane, merge, JDs, eligibility, fit, and the explorer. Use the user's saved sweep settings unless they say otherwise. Skip its final reply; this skill reports at the end.

**The browser lane always runs.** The sweep's `max_new_roles` stop rule does not apply inside autopilot: the HTTP feeds alone (Himalayas above all) pass any cap within a minute, and stopping there leaves the queue full of one source. So ignore the role cap, run **every** browser task of the configured mode (LinkedIn Jobs, LinkedIn posts, Remote Rocketship, Wellfound and all Google shards) under the mode's page and wall-clock caps, and only record a browser task as a coverage gap for the reasons the sweep itself names: Chrome unavailable, a CAPTCHA or login wall, or the wall-clock cap. The user asked for this on 2026-10-02 after a run that applied only to Himalayas listings.

**Skip the sweep's own country check (its step 7 and 8d).** The people check in step 3 replaces it inside the autopilot and runs on the companies that matter most for the target. The HTTP lane already includes the `company_boards` source: companies' own Ashby, Greenhouse, Lever and Workable boards, read through their public APIs, with each posting's own date, location and pay.

## 3. Score and rank

1. Run `autopilot score-queue --decisions <decisions dir>`. It queues every role from the last 14 sweep batches that isn't yet scored against the current rubric, apart from rejected and finished roles. **It checks applika (a fresh list) before considering any role**: a role already in applika is marked applied (`skipped_in_applika`); a role at a company with any applika application within `company_cooldown_days` (180 days) is marked `skipped` with that application's date and role (`skipped_applied_company`); a role at a company that rejected the user within `rejection_cooldown_days` is marked `skipped` with the rejection's date and feedback (`skipped_rejected_company`). None of these are scored. If `applika_error` is non-empty, handle it as described under `queue` below before scoring; never score without the applika check unless the user says so.
2. Launch one subagent per batch **in parallel** (`general-purpose`, `model: "sonnet"`, no browser) with this prompt: "Read `<ap>/references/score.md` and follow it. Context file: `<context>`. Batch file: `<batch>`. Output file: `<out>`. Reply only with the final message it specifies."
3. Run `autopilot score-ingest`.
4. **Company types, for the pay estimate.** Run `autopilot company-queue`. For each batch, launch a parallel subagent
   (`general-purpose`, `model: "sonnet"`, no browser) with the prompt: "Read `<ap>/references/company-type.md` and
   follow it exactly, including reading the jd_file of every company that isn't widely known and setting
   `confidence` honestly. Batch file: `<batch>`. Output file: `<out>`. Reply only with the final message it
   specifies." Then run `autopilot company-ingest`.
   - **What it records:** one judgement per company, cached for good: funded startup, product company, big company,
     staffing firm, consultancy, marketplace, task platform or local employer.
   - **When there is nothing to do:** on a normal day only the new companies are pending, usually one or two batches.
5. **People check.** Run `autopilot people-queue`. It lists up to `people_check_limit` companies that have no fresh
   verdict, ordered by pay: roles posting the target first, then those likely at it, then the best score.
   - **The subagent:** launch **one** Chrome subagent per batch, **one batch at a time**, never in parallel, since
     the batches share the search engines and one IP. Use `general-purpose`, `model: "sonnet"`, and the prompt: "Read
     `<repo>/job-source-sweep/references/people-check.md` and follow it. Batch file: `<batch>`. Output file: `<out>`.
     country: `<country>`, region: `<region>`."
   - **Afterwards:** run `autopilot people-ingest`.
   - **When every engine blocks:** if the subagent reports Google and Yahoo both blocked, don't start another batch this run. The rest
     stays queued for the next run.
   - **Never LinkedIn search:** the check runs on search engines only, never LinkedIn's own search (user rule
     2026-10-14).
6. Run `autopilot queue --decisions <decisions dir>`.

**Tiers (`target.py`).** The user's goal is roles paying at least `pay.target_usd_month` at companies that really
hire outside the US. Every role gets two judgements:
- **Pay tier.** `posted_target` / `posted_near` / `posted_below` when pay is posted. Otherwise `likely_target`,
  `unclear` or `likely_below`, from this evidence, strongest first:
  1. pay the company quoted the user (`recruiters/pay_ceilings.json`);
  2. the median pay of its other engineering postings (scores, boards, the funded-company registry);
  3. the company type;
  4. a recent funding round, or a VC/YC board, combined with a senior title.

  `paytier-backtest` checks this estimate against roles that did post pay. Its result is shown in the run report.
- **People check.** `brazil` (engineers in `candidate.country`), `latam` (elsewhere in `candidate.region`),
  `non_us`, `none` or unchecked.
  - **Why only this evidence:** a JD's location label is written by automated systems and is often wrong, so engineers
    actually working there from outside the US are the only evidence of where a company hires.
  - **Where it comes from:** Google `site:linkedin.com/in` searches (Yahoo when Google blocks), never LinkedIn search
    (`references/target-tier-research.md`).

**Tier A** is pay at target (posted or likely) plus engineers in the candidate's country or region. **Tier B** has
one of the two proven and the other unknown. **Tier C** is the rest.

How the total is built:
- The subagents return four parts: core AI/LLM match (35), requirement coverage (25), seniority (15) and domain/stack (10).
- The script adds location eligibility: open 15, not stated 10, restricted 0, unclassified 7.
- Each hard blocker multiplies the score by 0.6, at most twice.
- Roles rated from the title alone are capped at 60.
- **Pay:** +10 when the whole posted range is at or above the target, +6 when its top is, −10 when its top is below. With no posted pay, `likely_target` adds 6 and `likely_below` takes 8.
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

**A JD's "US-based only" exclusion is overridden** when the people check found engineers in the candidate's country
or region: the role is queued, capped at tier B, and the report shows the override. Citizenship, clearance, export
control and work-authorization requirements are legal, not labels, and still skip. These show up as "not applying" or "skipped" on the dashboard, with the JD's own words or the rejection. Everything else is queued. The queue also leaves out roles the user rejected, roles already applied to or discarded, filled forms waiting for the user, and roles waiting on the user's answers.

**The queue takes tier A first, then B, then C** (`target_mode: prefer`; with `only`, tier C is left out). **Inside each tier it is balanced across sources, not sorted by score alone** (user rule 2026-10-02, kept inside tiers on 2026-10-14). Within a tier, `queue` groups the roles by the source that found them (LinkedIn Jobs, Himalayas, HN Who is hiring, each VC portfolio network, Wellfound, Remote Rocketship, the Google ATS and job-board shards, and so on), sorts each group best score first, then deals the groups out round-robin: the best LinkedIn role, the best Himalayas role, the best HN role, and so on, before any source's second-best. Unscored roles go last within their group. Each queue entry carries its `source`, `tier`, `pay_tier` and `people`, and `status` shows the queue's mix per tier and per source. A score-only queue fills up with whichever feed is largest. Never re-sort the queue by score.

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
     - the role JSON (it includes `tailored_pdf`, `tailored_folder`, `jd_file`, `previous_attempt`, `tier` and `salary_answer`, the number to give when the posted range reaches the target)
     - `candidate` from the `next` output (the applicant's facts from `config.yaml`)
     - `answer_bank`: `<home>/autopilot/answer-bank.json`
     - `allowed_channels`, `cover_letter_policy` and `auto_submit`, from the `next` output (with `cover_letter: target`, `next` already resolved the policy for this role's tier)
     - the exact `autopilot` command prefix, and `cover_letter_py`: the same prefix with `cover_letter.py` in place of `autopilot.py`
     - `result_file`: `<home>/autopilot/work/<date>/apply_result_<id>.json`
     
     As each finishes, run `autopilot record --result <result_file>` (it releases the claim) and tell the user one line: the company, the tier, the outcome, how many answers were estimated, and whether a cover letter was written. Then call `next` again to keep `browser_parallel` fills in flight.
     - **Blocked tier A roles:** a tier A role whose form can't be filled (an account wall, or load failures) is
       written to `autopilot/target_roles.json`.
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
   - the selected roles in queue order, with tier, pay evidence, people-check evidence, score parts, sources and
     posting age;
   - every rejected role, with the step, reason, evidence and who decided (script rule, scoring subagent, applika,
     the user);
   - each posting's estimated age and how it was estimated: the ATS or JSON-LD date, the date the source gave, or the
     first time the sweep saw it, shown as a minimum. Likely reposts are flagged;
   - the changes since the last run, and the pay-estimate backtest.
2. Publish `report.html` as a **new** artifact every run (never over an earlier run's report), with `icon: "report"`.
   Then run `autopilot run-report --published-url <url>`, which records the link in `runs.json`.
3. Before publishing, run `autopilot paytier-backtest` when `company-ingest` added companies this run. The report
   shows its verdict.

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
- The queue by tier (`queue` → `by_tier`), the people checks run (verdicts, and which engines blocked), and the next few roles in the queue with their tiers.

## Data it keeps

All of this is in `<home>/autopilot/`:
- `answer-bank.json`: the profile (a mirror of `candidate` in `config.yaml`), answered questions (the user's own, and estimated ones awaiting confirmation), pending questions, and confirmed, denied and pending skills. Dashboard answers are merged in at preflight, and a question answered once is reused on every later form with the same meaning.
- `ledger.json`: every role's status, attempts, tailored CV, every answer typed into its form, free-text answers, cover letter, tab title and applika id.
- `scores.json`, `queue.json`, `pacing.json`, and `work/<date>/` with the batches and result files.
- `company_types.json` (one type per company), `paytier_backtest.json`, `target_roles.json` (blocked tier A roles for recruiter-outreach).
- `runs/<run_id>/decisions.jsonl` and `report.html`/`report.json`; `runs.json` lists every run with its report link.

Shared with the other skills, in `<home>`:
- `boards.json`: companies' ATS boards and their postings.
- `people_checks.json`: the people-check verdicts, with their search evidence, 90 days for a positive and 30 for none.

All of these come from `job-source-sweep/scripts/companies.py`. Recruiter-outreach's company lane reads the same people checks.

`autopilot status` summarises them; `autopilot ready` lists the filled forms waiting for the user.
