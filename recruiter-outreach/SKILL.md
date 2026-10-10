---
name: recruiter-outreach
description: A standalone LinkedIn recruiter pipeline, separate from job applications. Probes LinkedIn people search and feed posts for recruiters who place AI engineers from LATAM, nearshore or worldwide, builds a database of recruiter profiles with evidence, and ranks each lead 0-100 on four questions - is the recruiter explicitly hiring in LATAM and/or worldwide, active on LinkedIn, posting roles aligned with the target role (AI Engineer), and posting roles aligned with the target pay (USD 10k/month). It then consumes the ranked pile: at most 20 connection requests a day with a custom note, and when those are used up, InMails while Premium credits last (1st-degree connections get a plain message). Every message is short, written for that recruiter from their recent posts and tone, introduces the user (target role, years of experience, what they bring), never states pay, and is written to convert into a screening call. Nobody is contacted twice; the user's current employers are never contacted. Leads, scores, evidence and every message sent are shown on the role explorer's Outreach view. Use when the user wants to find recruiters, build or refresh the recruiter database, rank recruiter leads, send today's connection requests or InMails to recruiters, or review recruiter outreach. It does not apply to jobs (job-autopilot) and does not answer replies (linkedin-inbox).
---

# Recruiter outreach

A pipeline of its own: discover recruiters, score them, contact the best ones within the daily limits. Nothing here is tied to an application. The user authorised sending connection requests and InMails when they set this up; discovery never sends anything.

**Paths**
- **Skill:** `C:\Users\<you>\.claude\skills\recruiter-outreach` (`<rc>`)
- **Script:** `<rc>/scripts/recruiters.py`. It reuses job-source-sweep's helpers, so it needs the sibling `job-source-sweep` folder.
- **Data:** `<home>/recruiters/`, where `<home>` is job-source-sweep's home (`~/jobsweep`).
- **Candidate snapshot:** `<home>/fit-rubric.md` and the resume in the sweep config (`D:/dev/resumes/jane-doe-ai-engineer-cv.yaml`).

**Running the script.** `wsl.exe -e python3 /mnt/d/dev/autopilot/recruiter-outreach/scripts/recruiters.py --home /mnt/c/Users/<you>/jobsweep <cmd>`. From Git Bash, first `export MSYS_NO_PATHCONV=1`. Below, `recruiters <cmd>` is short for that.

**Settings** live in `<home>/recruiters/settings.json`:

| Key | Default | Meaning |
|---|---|---|
| `target_role` | AI Engineer | The role the lead's posted jobs are matched against. |
| `pay_target_usd_month` | 10000 | The pay the lead's posted jobs are matched against. |
| `banned_companies` | ExampleEmployer, Example Labs, example-client.io, Example Client Co | The user's current employers and clients. Their recruiters are never contacted. |
| `connect_per_day` | 20 | Connection requests per UTC day. The user's hard limit. |
| `inmail_per_day` | 15 | A second guard on InMails; the real bound is the credits LinkedIn shows. |
| `message_per_day` | 20 | Plain messages to 1st-degree connections. |
| `min_score` | 45 | Leads below this are not contacted. |
| `stale_days` | 60 | A lead's signals are probed again after this long. |
| `people_queries`, `post_queries` | see the script | LinkedIn search phrasings. Each is rerun at most once a week. |

## 0. First run

If `<home>/recruiters/contacts.json` doesn't exist, seed it from the old autopilot contacts so nobody is written to twice: `recruiters import-contacts --file /mnt/c/Users/<you>/jobsweep/autopilot/contacts.json`.

## 1. Discover

Run this when the user asks to find or refresh recruiters, and at the start of any outreach run when `recruiters status` shows fewer than about 20 `contactable_now` leads.

1. Run `recruiters discover-plan --max-leads 30`. It lists the people and post queries not run in the last week, the known profiles to skip, stale leads to probe again, and the `result` path.
2. Split the queries into two or three groups and run them as **sequential** Chrome subagents (`general-purpose`, `model: "sonnet"`; one Chrome). Each prompt: "Read `<rc>/references/discover.md` and follow it." followed by its share of `people_queries` and `post_queries`, `max_leads` (about 15 per subagent), `known_profiles`, `reprobe`, `target_role`, `pay_target_usd_month`, `banned_companies`, and its own `result` file (add a suffix per subagent). The subagent probes both profiles and feed posts: a recruiter's recent hiring posts are what answer the four questions.
3. After each one, run `recruiters ingest --result <file>` and tell the user one line: leads added, and the top few. If a subagent reports `blocked`, stop discovering for today.

## 2. Score

Scores are computed on ingest. Each lead gets up to 100 points from four signals, and the user's question is the rubric:

| Question | Signal | Points |
|---|---|---|
| Explicitly hiring in LATAM and/or worldwide? | `hiring_scope`: latam / worldwide 35, nearshore 32, remote with region unstated 15, unknown 5, local-only 0 | 35 |
| Active on LinkedIn? | `last_activity_days`: ≤7 → 20, ≤30 → 14, ≤90 → 7, older 0, no activity 3 | 20 |
| Posting roles aligned with the target role? | `role_posts_90d`: 2+ → 25, 1 → 16, only adjacent engineering roles → 6, none 0 | 25 |
| Posting roles aligned with the target pay? | `pay_signal`: at or above target 20, within 20% below 8, no pay posted 5, below 0 | 20 |
| | `local_rate_shop` (LATAM staffing at local rates): −15 | |

A recruiter who answers yes to all four scores near 100; one who answers none scores under 20. `recruiters score` recomputes everything after a settings change. Leads marked irrelevant by the discovery subagent score 0 and are kept only so they aren't probed again.

## 3. Contact

1. Run `recruiters plan --n 30`. It returns today's quotas and the ranked pile of uncontacted leads at or above `min_score`, cut to what today's quotas can take, plus the `result` path. Within a score band, 1st-degree connections come first because messaging them costs no quota.
2. Launch **one** Chrome subagent (`general-purpose`, `model: "sonnet"`) with the prompt: "Read `<rc>/references/message.md` and follow it." followed by the plan's `tasks`, `quotas`, `banned_companies`, `contacts_file`, the exact `recruiters` command prefix, the fit-rubric path and the resume path, and `result`. It works down the pile: connection requests with a custom note until the 20 are used, then InMails while the compose window shows credits, and plain messages to 1st-degree leads. It runs `recruiters check` before every send, writes each message for that recruiter from their evidence and tone, introduces the candidate in two lines, never states pay, and asks for a call.
3. Run `recruiters record --result <file>`. It stores every contact with the exact message, updates the counters and the InMail credits hint, and marks the leads contacted.

Stop when the connection and InMail quotas are both used, when the subagent reports a block, or when the user says stop.

**Replies** are handled by the linkedin-inbox skill. When the user mentions that a recruiter answered, run `recruiters mark --profile <url> --status replied`.

## 4. Publish and report

1. Run `recruiters dashboard`. It writes `<home>/recruiters/dashboard.json`, which `jobsweep explorer` embeds in the role explorer's Outreach view (ranked leads with their score parts, evidence, recent roles, status, and the message sent).
2. Run the sweep's `jobsweep explorer` and republish the page to the same `explorer_url`, following job-source-sweep step 8e (read the artifact first, omit `capabilities`).
3. Report: leads discovered and the top of the pile; messages sent by channel; connection requests and InMail credits left; leads skipped and why; the dashboard link.

## Data it keeps

All in `<home>/recruiters/`: `leads.json` (every recruiter, signals, evidence, score, status, message sent), `contacts.json` (everyone ever contacted, including the old autopilot follow-ups), `pacing.json` (today's counters and credits), `discovery-runs.json` (when each query last ran), `work/<date>/` (result files). `recruiters status` summarises them.
