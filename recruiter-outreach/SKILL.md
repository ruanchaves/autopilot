---
name: recruiter-outreach
description: A standalone LinkedIn recruiter pipeline, separate from job applications. Probes LinkedIn people search and feed posts for recruiters who place people in the user's target role from their region (config.yaml), nearshore or worldwide, builds a database of recruiter profiles with evidence, and ranks each lead 0-100 on four questions - is the recruiter explicitly hiring in the user region and/or worldwide, active on LinkedIn, posting roles aligned with the target role, and posting roles aligned with the target pay (both from the repo's config.yaml). It then consumes the ranked pile: at most 20 connection requests a day with a custom note, and when those are used up, InMails while Premium credits last (1st-degree connections get a plain message). Every message is short, written for that recruiter from their recent posts and tone, introduces the user (target role, years of experience, what they bring), never states pay, and is written to convert into a screening call. Nobody is contacted twice; the user's current employers are never contacted. Leads, scores, evidence and every message sent are shown on the role explorer's Outreach view. Use when the user wants to find recruiters, build or refresh the recruiter database, rank recruiter leads, send today's connection requests or InMails to recruiters, or review recruiter outreach. It does not apply to jobs (job-autopilot) and does not answer replies (linkedin-inbox).
---

# Recruiter outreach

A pipeline of its own: discover recruiters, score them, contact the best ones within the daily limits. Nothing here is tied to an application. The user authorised sending connection requests and InMails when they set this up; discovery never sends anything.

**Paths**
- **Skill:** `<repo>/recruiter-outreach` (`<rc>`), where `<repo>` is the autopilot repo (here `D:\dev\autopilot`)
- **Script:** `<rc>/scripts/recruiters.py`. It reuses job-source-sweep's helpers, so it needs the sibling `job-source-sweep` folder.
- **Data:** `<home>/recruiters/`, where `<home>` is `home` in `config.yaml`.
- **Candidate snapshot:** `candidate` in `config.yaml` (pitch, stories, location), plus `<home>/fit-rubric.md` and `resume.base` for the facts.

**Running the script.** With the runner from `config.yaml`: for `python: wsl`, `wsl.exe -e python3 /mnt/<repo>/recruiter-outreach/scripts/recruiters.py <cmd>` (from Git Bash, first `export MSYS_NO_PATHCONV=1`). `--home` defaults to `home` in `config.yaml`. Below, `recruiters <cmd>` is short for that.

**Settings** come from `<repo>/config.yaml`. The sections this skill reads:

| Section / key | Meaning |
|---|---|
| `role.title` | The role the lead's posted jobs are matched against. |
| `pay.target_usd_month` | The pay the lead's posted jobs are matched against. |
| `candidate` | Country, region and aliases (what "hiring in the candidate's region" means), pitch, location pitch, stories, stack, employment types, native language. Handed to both subagents as `candidate`. |
| `banned_companies` | The user's current employers and clients. Their recruiters are never contacted. |
| `recruiter_outreach.connect_per_day` | Connection requests per UTC day. The user's hard limit. |
| `recruiter_outreach.inmail_per_day` | A second guard on InMails; the real bound is the credits LinkedIn shows. |
| `recruiter_outreach.message_per_day` | Plain messages to 1st-degree connections. |
| `recruiter_outreach.min_score` | Leads below this are not contacted. |
| `recruiter_outreach.stale_days` | A lead's signals are probed again after this long. |
| `recruiter_outreach.people_queries`, `post_queries` | LinkedIn search phrasings, written for the candidate's role and region. Each is rerun at most once a week. |

## 0. First run

If `<home>/recruiters/contacts.json` doesn't exist, seed it from the old autopilot contacts so nobody is written to twice: `recruiters import-contacts --file <home>/autopilot/contacts.json` (skip it if that file doesn't exist).

## 1. Discover

Run this when the user asks to find or refresh recruiters, and at the start of any outreach run when `recruiters status` shows fewer than about 20 `contactable_now` leads.

1. Run `recruiters discover-plan --max-leads 30`. It lists the people and post queries not run in the last week, the known profiles to skip, stale leads to probe again, and the `result` path.
2. Split the queries into `browser_parallel` groups (config.yaml, default 3) and launch them as Chrome subagents **in parallel, in one message** (`general-purpose`, `model: "sonnet"`). Chrome is not a serial resource: every Chrome tool takes a `tabId`, so each subagent creates its own tab in the shared tab group and works through the DOM tools (navigate, get_page_text, read_page, find, form_input, javascript_tool). Only `computer` screenshots see the window's visible tab, which is why the reference forbids them when running alongside others. Verified on 2026-10-10: two discovery subagents ran side by side with no cross-talk. Each prompt: "Read `<rc>/references/discover.md` and follow it. Other subagents are using Chrome at the same time: use only your own tab." followed by its share of `people_queries` and `post_queries`, `max_leads` (about 12 per subagent), `known_profiles`, `reprobe`, `target_role`, `pay_target_usd_month`, `candidate`, `banned_companies`, and its own `result` file (add a suffix per subagent). The subagent probes both profiles and feed posts: a recruiter's recent hiring posts are what answer the four questions.
3. As each one finishes, run `recruiters ingest --result <file>` and tell the user one line: leads added, and the top few. If a subagent reports `blocked`, stop discovering for today. LinkedIn's own rate limits are per account, so keep the groups to `browser_parallel` and don't add more.

## 2. Score

Scores are computed on ingest. Each lead gets up to 100 points from four signals, and the user's question is the rubric:

| Question | Signal | Points |
|---|---|---|
| Explicitly hiring in the candidate's region and/or worldwide? | `hiring_scope`: the candidate's region (`latam` is the value's historical name) / worldwide 35, nearshore 32, remote with region unstated 15, unknown 5, local-only 0 | 35 |
| Active on LinkedIn? | `last_activity_days`: ≤7 → 20, ≤30 → 14, ≤90 → 7, older 0, no activity 3 | 20 |
| Posting roles aligned with the target role? | `role_posts_90d`: 2+ → 25, 1 → 16, only adjacent engineering roles → 6, none 0 | 25 |
| Posting roles aligned with the target pay? | `pay_signal`: at or above target 20, within 20% below 8, no pay posted 5, below 0 | 20 |
| | `local_rate_shop` (regional staffing at local rates): −15 | |

A recruiter who answers yes to all four scores near 100; one who answers none scores under 20. `recruiters score` recomputes everything after a settings change. Leads marked irrelevant by the discovery subagent score 0 and are kept only so they aren't probed again.

## 3. Contact

1. Run `recruiters plan --n 30`. It returns today's quotas and the ranked pile of uncontacted leads at or above `min_score`, cut to what today's quotas can take, plus the `result` path. Within a score band, 1st-degree connections come first because messaging them costs no quota.
2. Launch **one** Chrome subagent (`general-purpose`, `model: "sonnet"`) with the prompt: "Read `<rc>/references/message.md` and follow it." Sending stays single on purpose: the daily counters in `pacing.json` are plain files without locking, so two senders could both pass `check` and overshoot the 20-request limit, and a sent message can't be taken back. Discovery may keep running in parallel with it. followed by the plan's `tasks`, `quotas`, `candidate`, `banned_companies`, `contacts_file`, the exact `recruiters` command prefix, the fit-rubric path and the resume path, and `result`. It works down the pile: connection requests with a custom note until the 20 are used, then InMails while the compose window shows credits, and plain messages to 1st-degree leads. It runs `recruiters check` before every send, writes each message for that recruiter from their evidence and tone, introduces the candidate in two lines, never states pay, and asks for a call.
3. Run `recruiters record --result <file>`. It stores every contact with the exact message, updates the counters and the InMail credits hint, and marks the leads contacted.

Stop when the connection and InMail quotas are both used, when the subagent reports a block, or when the user says stop.

**Replies** are handled by the linkedin-inbox skill. When the user mentions that a recruiter answered, run `recruiters mark --profile <url> --status replied`.

## 4. Publish and report

1. Run `recruiters dashboard`. It writes `<home>/recruiters/dashboard.json`, which `jobsweep explorer` embeds in the role explorer's Outreach view (ranked leads with their score parts, evidence, recent roles, status, and the message sent).
2. Run the sweep's `jobsweep explorer` and republish the page to the same `explorer_url`, following job-source-sweep step 8e (read the artifact first, omit `capabilities`).
3. Report: leads discovered and the top of the pile; messages sent by channel; connection requests and InMail credits left; leads skipped and why; the dashboard link.

## Data it keeps

All in `<home>/recruiters/`: `leads.json` (every recruiter, signals, evidence, score, status, message sent), `contacts.json` (everyone ever contacted, including the old autopilot follow-ups), `pacing.json` (today's counters and credits), `discovery-runs.json` (when each query last ran), `work/<date>/` (result files). `recruiters status` summarises them.
