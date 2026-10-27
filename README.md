# autopilot

Three [Claude Code](https://claude.com/claude-code) skills that run a remote job hunt end to end: sweep ~80 job sources every day, rate every role against your resume, fill the application forms up to the Submit button, and build a scored database of LinkedIn recruiters to contact within LinkedIn's daily limits. Everything personal lives in one file, `config.yaml`, so anyone can fork the repo, edit that file, and run the same pipelines for their own role, country and pay target.

The skills are Markdown instructions (`SKILL.md`, plus `references/` for the subagents) around small Python scripts that keep the state. Claude Code reads the instructions, drives Chrome through the [Claude in Chrome](https://claude.com/chrome) extension, launches subagents in parallel, and calls the scripts for everything that must be exact: deduplication, scoring arithmetic, daily counters, ledgers.

| Skill | What it does | Sends anything? |
|---|---|---|
| `/job-source-sweep` | Sweeps feeds, APIs, VC portfolio boards, LinkedIn, Wellfound, Remote Rocketship and Google `site:` searches over 19 ATS platforms and 15 job boards. Fetches each job description, classifies location eligibility, rates resume fit, and publishes the **role explorer**, a filterable page where you accept or reject roles. | No |
| `/job-autopilot` | Runs the sweep, gives every role a 0-100 score, estimates pay when none is posted, checks whether each company has engineers in your country or region, and queues tier A roles (target pay plus engineers there) first. It tailors the resume per role and fills each application form completely in Chrome. **By default it stops in front of Submit** and leaves the tab open; you review, submit, and mark it submitted on the dashboard. Every run publishes a report explaining each decision. Optional `auto_submit`. | Only with `auto_submit: true` |
| `/recruiter-outreach` | Finds recruiters on LinkedIn who place people in your role from your region, scores each on four questions, and contacts the best ones: 20 new connection requests a day with a custom note, then InMails while credits last, plus a plain message to every recruiter who is already a 1st-degree connection (those never count toward the 20). Keeps prospecting with invented search phrasings until the day's budget is spent. | Yes, within the daily caps |
| `/hiring-posts-outreach` | The quick lane: LinkedIn post searches for "<role> hiring" run inside LinkedIn search, the authors ranked by the pay their post states, then by explicit eligibility for your region, and, when the post states neither, by the poster's likely nationality guessed from their name (nearest to your country first), and contacted best-first within recruiter-outreach's caps and contact ledger. No profile visits: the post is the evidence. | Yes, within the same daily caps |

## Contents

- [How the pieces fit](#how-the-pieces-fit)
- [Quick start](#quick-start)
- [config.yaml](#configyaml)
- [job-source-sweep](#job-source-sweep)
- [job-autopilot](#job-autopilot)
- [recruiter-outreach](#recruiter-outreach)
- [The role explorer](#the-role-explorer)
- [Parallelism and Chrome](#parallelism-and-chrome)
- [Guard rails](#guard-rails)
- [Data layout](#data-layout)
- [Running the scripts by hand](#running-the-scripts-by-hand)
- [Extending](#extending)
- [Privacy](#privacy)

## How the pieces fit

```mermaid
flowchart LR
    subgraph repo["autopilot repo (this repo)"]
        cfg[("config.yaml<br/>everything personal")]
        sweep["job-source-sweep<br/>SKILL.md · references/ · scripts/"]
        ap["job-autopilot<br/>SKILL.md · references/ · scripts/"]
        rc["recruiter-outreach<br/>SKILL.md · references/ · scripts/"]
    end
    cfg --> sweep
    cfg --> ap
    cfg --> rc
    sweep -. "imports jobsweep.py, enrich.py, userconfig.py" .-> ap
    sweep -. "imports helpers" .-> rc

    subgraph cc["Claude Code"]
        main["main conversation<br/>reads SKILL.md, runs scripts"]
        subs["subagents, in parallel<br/>one Chrome tab each"]
    end
    sweep --> main
    ap --> main
    rc --> main
    main --> subs

    subgraph world["Outside"]
        web["Job feeds, APIs,<br/>Google, LinkedIn, ATS forms"]
        art["Role explorer<br/>(claude.ai Artifact with a database)"]
        applika["applika<br/>application tracker (optional)"]
    end
    subs <--> web
    main --> art
    art -- "decisions, answers,<br/>submitted marks" --> main
    main <--> applika

    home[("<home> data folder<br/>runs, caches, ledgers,<br/>answer bank, recruiter leads")]
    main <--> home
```

Three principles run through everything:

1. **One source of settings.** `config.yaml` holds the candidate, the resume paths, the role, the pay targets, the employers never to contact, the data folder, and each skill's tunables. Nothing personal is hard-coded in the skills.
2. **The scripts own the state; Claude owns the judgment.** A script decides what is due, enforces every cap, and records what happened. Subagents read pages, write messages and fill forms. That split is what makes the caps real: the daily counters, the per-company cap, and the "never twice" rules live in files, not in a prompt.
3. **Humans submit.** The autopilot prepares applications; you send them. The outreach skill is the one place that sends on your behalf, and only within limits you set.

## Quick start

```bash
git clone https://github.com/ruanchaves/autopilot.git
cd autopilot
claude
```

Then paste this into Claude Code:

> Read SETUP.md in this folder and follow it to install the autopilot skills.

Claude links the three skill folders into `~/.claude/skills`, finds a Python 3.11+ with PyYAML (native, or inside WSL on Windows), sets the runner and the data folder in `config.yaml`, and starts the first-run setup, which asks about your role, resume, country, pay and sweep preferences and fills the candidate section from your resume. After a restart of Claude Code, `/job-source-sweep`, `/job-autopilot` and `/recruiter-outreach` are available. The last two ask their own few questions the first time they run (submit policy, cover letters, channels, tracker; daily caps, per-company cap, permission to send, search phrasings). `config.yaml` keeps a `setup_done` list of the skills that have been set up; empty it, or say "reconfigure", to go through the questions again.

**Requirements:** Claude Code with the [Claude in Chrome](https://claude.com/chrome) extension connected to a Chrome profile logged in to LinkedIn; Python 3.11+ with PyYAML. For the people check, a second virtualenv with [ddgs](https://github.com/deedy5/ddgs) (the commands are in `config.yaml`, section `search`). Optional: [applika](https://github.com/ruanchaves/applika) for application tracking, and a `tailor-resume` skill in `~/.claude/skills` for per-role CVs (without it the autopilot uploads your resume PDF as-is).

## config.yaml

| Section | Keys | Used by |
|---|---|---|
| top level | `home` (data folder), `python` (`python3`, `python`, `py -3` or `wsl`), `browser_parallel` (Chrome subagents at once, default 3), `setup_done` (skills whose first-run setup has run) | all |
| `candidate` | name and contact details, `country`, `region`, `region_aliases`, `timezone`, `languages`, `native_language`, `years_experience`, `level`, `headline`, `pitch`, `core_areas`, `domains`, `stack`, `stories`, `location_pitch`, `work_authorization`, `sponsorship`, `employment_types`, `excluded_contract_types`, `working_hours`, `availability`, `relocation` | eligibility, scoring, form answers, cover letters, recruiter messages |
| `resume` | `base` (RenderCV YAML, Markdown, text or PDF), `tailored_dir`, `file_prefix`, `pdf` (uploaded as-is when tailoring is off) | fit rating, tailoring, uploads |
| `role` | `title`, `profile` (a `profiles/<slug>.json` with the search patterns) | sweep, recruiter matching |
| `pay` | `target_usd_month`, `skip_below_usd_month`, `ask_usd_month`, `free_text_answer` | ranking, skips, form answers, recruiter pay signal |
| `banned_companies` | current employers and clients | never applied to, never contacted |
| `tracker` | `enabled`, `company_cooldown_days`, `rejection_cooldown_days`, `rejection_neutral_feedback`, `rejection_ignore_companies` | applika registration, the one-application-per-company cooldown and rejection cooldowns |
| `sweep` | `days`, `mode` (`quick`/`standard`/`full`), `explorer` (`full`/`table`/`off`), `max_new_roles`, `remote_only`, `report_rows`, `country_check`, `country_check_limit`, `skip_tasks`, `skip_sources`, `skip_hosts` | job-source-sweep |
| `autopilot` | `channels`, `max_minutes`, `max_ready`, `auto_submit`, `cover_letter` (`required`/`always`/`target`/`never`), `target_mode` (`prefer`/`only`), `people_check_limit`, `max_load_retries`, `tailor`, `tailor_ahead`, `per_company_per_run` | job-autopilot |
| `recruiter_outreach` | `connect_per_day`, `inmail_per_day`, `message_per_day`, `min_score`, `max_per_company`, `stale_days`, `gap_seconds`, `people_queries`, `post_queries`, `required_terms`, `cta_styles` | recruiter-outreach |

Each skill has an interactive first-run setup that writes its section: the sweep asks about the role, resume, country, pay and sweep preferences and fills `candidate` from the resume; the autopilot asks about submitting, cover letters, channels, the tracker and caps; recruiter outreach asks for permission to send, the daily and per-company caps, strictness, and proposes the search phrasings. `job-source-sweep/scripts/userconfig.py` is the loader all three skills share. Set `AUTOPILOT_CONFIG` to point at a different file. The only state the scripts write back is `<home>/config.json` with the explorer's artifact URL; preferences are always edited in `config.yaml`.

## job-source-sweep

The sweep runs in two lanes. The HTTP lane needs no browser and answers in about a minute; the browser lane follows, through subagents in Chrome, under a page cap and a wall-clock cap set by `sweep.mode`. After the final merge the enrichment stages fetch every job description, classify eligibility, rate fit, and publish the explorer.

```mermaid
flowchart TD
    cfg["jobsweep config<br/>settings, profiles, skippable sources"] --> setup{"first run or<br/>'reconfigure'?"}
    setup -- yes --> ask["interactive setup<br/>→ edits config.yaml"] --> http
    setup -- no --> http

    http["HTTP lane · jobsweep http<br/>Himalayas, Remote OK, Jobgether, Working Nomads, Remotive,<br/>We Work Remotely, NoDesk, Cryptocurrency Jobs, web3.career, HN Who is hiring (whole thread), YC public job pages,<br/>a16z board, 15 Getro VC networks (incl. Solana, Electric Capital, Dragonfly), 5 Consider boards, companies' own ATS boards (30 web3 employers seeded)"] --> merge1["jobsweep merge<br/>dedupe by URL then company+title,<br/>title filter, date window, new-since-last-run"]
    merge1 --> table1["first table shown to the user"]
    table1 --> plan["jobsweep plan<br/>browser tasks for the mode"]
    plan --> browser["Browser lane · parallel Chrome subagents<br/>LinkedIn Jobs · LinkedIn posts · Remote Rocketship ·<br/>Wellfound · YC Work at a Startup (signed in) · Google site: shards (19 ATS + 15 boards)"]
    browser --> merge2["jobsweep merge after each subagent"]
    merge2 --> stop{"wall-clock cap or<br/>max_new_roles reached?"}
    stop -- no --> browser
    stop -- yes --> country{"sweep.country_check?"}
    country -- "always / on request" --> verify["jobsweep verify-queue · people check<br/>Google: 'engineer &quot;&lt;company&gt;&quot; &lt;country&gt; site:linkedin.com/in'<br/>engineers located outside the US · never LinkedIn search<br/>cached 90 days (found) / 30 days (none)"] --> enrich
    country -- never --> enrich

    subgraph enrich["Enrich (cached, resumable)"]
        jds["8a · jobsweep jds<br/>JD text via ATS APIs, JSON-LD, page text"]
        elig["8b · eligibility<br/>parallel subagents classify each JD:<br/>open / nothing stated / restricted"]
        fit["8c · resume fit<br/>parallel subagents rate each open JD<br/>against the resume"]
        jds --> elig --> fit
    end
    enrich --> explorer["8e · jobsweep explorer<br/>roles.json, roles.csv, explorer.html<br/>→ republished to the same artifact URL"]
```

**Modes**

| Mode | Browser tasks | Caps | Time |
|---|---|---|---|
| `quick` | none | | ~1 min |
| `standard` | LinkedIn Jobs (first keyword, every location) and posts (first keyword), Rocketship, Wellfound, Work at a Startup (first query), each Google shard with its first query | 2 pages per task, 2 SERPs per shard | ~20 min |
| `full` | every task in `plan.json` | 5 pages, 5 SERPs | ~60 min |

**Role profiles.** `profiles/<slug>.json` holds the include and exclude regexes, the API and Google phrasings, and per-source options for one role. `_template.json` documents every field; `ai-engineer.json` is a worked example. Profiles created for you go to `<home>/profiles/` and take precedence. `jobsweep test-match` shows which titles a profile keeps and drops.

**People check.** It answers one question: do engineers who work at the company live in your country, elsewhere in your region, or elsewhere outside the US?
- **Why:** a posting's location label is written by automated systems and is often wrong, so this is the only evidence accepted that a company hires outside the US.
- **How:** ordinary search engines aimed at LinkedIn profiles, never LinkedIn's own search.
  - **Search:** a script runs the searches through the search layer (`scripts/companies.py`: the
    [ddgs](https://github.com/deedy5/ddgs) library in its own virtualenv, its Yahoo engine (Bing's index), paced and cached).
  - **Queries:** profile-country subdomains like `site:br.linkedin.com/in engineer "Company"`, where the subdomain proves
    the country.
  - **Judging:** a subagent judges the results against what we know about the company, which rejects namesakes such as
    AZX Lab for AZX.
  - **Fallback:** only what it can't settle goes to Google and Yahoo in Chrome.
  - **What counts:** a result with an engineering title, the company as current employer, and a stated location or a
    country-subdomain profile URL.
- **Caching:** verdicts are cached per company, 90 days when engineers were found and 30 days when none were. "None found" isn't proof of anything.
- **Pace:** up to 100 companies a run (`search.max_queries_per_run` 300), with 4–6 seconds between queries and a canary query that stops the run if the engines start blocking (`references/people-judge.md`, `references/people-check.md`).

**Company boards.** The `company_boards` source reads companies' own Ashby, Greenhouse, Lever and Workable boards through their public APIs.
- **Which companies:** every company whose role link pointed at one, plus recruiter-outreach's funded-company registry.
- **What comes back:** each posting's own date, location and pay, 21 days back (`scripts/companies.py`, `<home>/boards.json`).

**Funnel.** Every merge writes `funnel.json`: per source, the rows fetched, then what the title filter, the date window, the location filter and dedupe each removed. job-autopilot's run report shows it.

**Coverage, not silence.** Every task the caps cut, every failed feed, every CAPTCHA or login wall is reported as a gap. The sweep never presents a skipped source as "no results".

## job-autopilot

The autopilot wraps the sweep, ranks what it found, and prepares applications from the best role down.
- **Tiers:** roles are worked in tiers.
  - **Tier A:** pay at your target and engineers working there from your country or region.
  - **Tier B:** one of the two.
  - **Tier C:** neither.
- **Pay without a posted number:** it is estimated from what the company quoted you, what it posts for its other engineering roles, and what kind of company it is. A backtest against roles that did post pay checks the estimate (`job-autopilot/references/target-tier-research.md`).
- **Run report:** every run publishes a report artifact with every source and query, why each role was selected or rejected and who decided, and each posting's estimated age. Chrome subagents fill forms in parallel, each in its own tab. The forms stay open in front of Submit; the dashboard shows every answer typed, with estimates flagged, so you can review and send.

```mermaid
flowchart TD
    pre["1 · Preflight<br/>applika login · import dashboard answers, overrides,<br/>submitted marks · pace-start · ready ≥ max_ready?"] --> sw["2 · Sweep<br/>job-source-sweep through 8e,<br/>browser lane always runs"]
    sw --> sq["3 · score-queue<br/>roles from the last 14 batches not yet scored"]
    sq --> score["parallel scoring subagents (no browser)<br/>core match 35 · requirements 25 · seniority 15 · domain 10<br/>+ eligibility, pay, freshness · blockers ×0.6"]
    score --> queue["queue<br/>skips banned, rejected-recently, pay below floor,<br/>explicit location / on-site / excluded contract types;<br/>round-robin across sources, best first"]
    queue --> tailor["4 · tailor-queue<br/>parallel /tailor-resume subagents keep<br/>tailor_ahead CVs ready"]
    tailor --> next["5 · next (claims a role for 30 min)<br/>up to browser_parallel at once"]
    next --> fill["Chrome subagent · references/apply.md<br/>upload CV · answer every question from the resume<br/>(estimates flagged) · cover letter if required ·<br/>stop in front of Submit, tab left open"]
    fill --> rec["record --result<br/>ledger: answers, cover letter, tab title"]
    rec --> more{"time cap, max_ready,<br/>or empty queue?"}
    more -- no --> next
    more -- yes --> user["6 · You review each open tab,<br/>submit, mark Submitted or Discard<br/>on the dashboard (or in chat)"]
    user --> sync["7 · applika-sync<br/>registers confirmed submissions"]
    sync --> pub["dashboard + jobsweep explorer<br/>→ republished artifact"]
```

**Scoring.** Four parts come from the subagent (core match, requirement coverage, seniority, domain and stack). The script adds eligibility (open 15, not stated 10, restricted 0), pay (+10 when the posted range is at or above `pay.target_usd_month`, −10 when its top is below), and freshness (+5 within 3 days). Hard blockers multiply by 0.6, at most twice. Roles scored from the title alone are capped at 60. The queue is dealt round-robin across sources so one prolific feed does not crowd out the rest.

**The submit handshake.** Nothing is registered as an application until you mark it.

```mermaid
sequenceDiagram
    participant S as Chrome subagent
    participant L as ledger.json
    participant D as Role explorer (dashboard)
    participant U as You
    participant A as applika
    S->>L: record: status filled, answers, cover letter, tab title
    L->>D: dashboard + explorer republish
    U->>D: review answers, open the tab, click Submit on the site
    U->>D: "I submitted it" (or Discard)
    D->>L: next preflight imports the submitted mark<br/>(or autopilot confirm --submitted, right away)
    L->>A: applika-sync registers the application
```

**Role lifecycle** in `ledger.json`:

```mermaid
stateDiagram-v2
    [*] --> queued: scored and worth applying
    [*] --> not_applying: explicit exclusion, on-site, contract type
    [*] --> skipped: banned, rejected recently, pay below floor
    queued --> deferred: site pacing budget used
    deferred --> queued
    queued --> filled: form complete, tab open at Submit
    queued --> failed_fill: filled but needs your answers
    queued --> failed_submit: blocked before filling (sign-up, human check)
    queued --> failed_load: page would not load (retried later)
    failed_load --> queued
    failed_fill --> filled: answers given on the dashboard
    filled --> applied: you marked Submitted
    filled --> discarded: you marked Discard
    queued --> applied: auto_submit true and a confirmation was seen
    applied --> [*]
```

**Answers and questions.** The subagent answers every field it can estimate from the resume and `candidate`, and marks each estimate. Questions it cannot estimate (ID numbers, authorisation elsewhere, documents, facts only you know) go to the dashboard's Questions view; an answer given once is reused on every later form asking the same thing. Required consent boxes are ticked. Cover letters follow `references/cover-letter.md`: 120 to 200 words, three paragraphs, built from `candidate.stories`, rendered to PDF by `scripts/cover_letter.py` when the form wants a file.

**`auto_submit`.** With `autopilot.auto_submit: true` the subagent clicks Submit after a complete fill, records the confirmation it saw, and the application is registered without the handshake. The default is `false`.

## recruiter-outreach

A pipeline of its own, not tied to applications. It builds a database of recruiters with evidence, ranks them, and spends the day's budget on the best ones. The run is a loop: it is finished only when today's connection requests and InMail credits are used and every 1st-degree lead in the pile has had its message, or LinkedIn blocks, or you say stop. The 20 a day limits new connection requests only; messaging existing connections is a separate channel that doesn't spend from it. An empty pile means "discover more", never "stop".

```mermaid
flowchart TD
    status["recruiters status<br/>needed = connects left + InMail credits left<br/>contactable_now = uncontacted leads ≥ min_score<br/>at companies under max_per_company"] --> enough{"contactable_now<br/>≥ needed?"}
    enough -- no --> dplan["discover-plan<br/>configured phrasings not run this week,<br/>exhausted?, queries_tried, deeper pages,<br/>snowball_companies (proven target pay), vc_funds,<br/>known_low_ceiling, seeds"]
    dplan --> invent["Invent the round's queries<br/>new phrasings (role × region × recruiter noun × pay × language),<br/>deeper result pages, '&lt;agency&gt; recruiter', hashtags,<br/>job-post authors, reposters and commenters"]
    invent --> disc["parallel Chrome subagents · references/discover.md<br/>people search + post search → probe each profile and activity page<br/>→ hiring_scope, last_activity_days, role_posts_90d, pay_signal, client_tier, vibe, evidence"]
    disc --> ingest["ingest --result<br/>score each lead, remember the search that found it,<br/>record phrasings and page depth, check required terms"]
    ingest --> status
    enough -- yes --> plan["plan<br/>ranked pile cut to today's budget,<br/>1st-degree first within a band,<br/>company cap applied"]
    plan --> send["one Chrome subagent · references/message.md<br/>read new connections → check before every send → custom note / InMail / message<br/>never states pay · closes with an open door, never a call request"]
    send --> record["record --result<br/>contacts.json, pacing.json, companies.json,<br/>leads marked, accepted requests recorded"]
    record --> done{"status.done?"}
    done -- no --> status
    done -- yes --> report["queries → dashboard → explorer Outreach view → report"]
```

**Scoring** answers five questions about each recruiter, from their profile and recent posts. Scores are computed on ingest and recomputed by `recruiters score` after a settings or ceiling change. The weights were set from an inbox study in which 120 recruiters quoted pay: region predicted nothing about pay, but the client behind the recruiter predicted almost all of it ([recruiter-outreach/references/pay-tier-research.md](recruiter-outreach/references/pay-tier-research.md)).

| Question | Signal | Points |
|---|---|---|
| Explicitly hiring in my region, or worldwide? | `hiring_scope`: region or worldwide 30, nearshore 15, remote with region unstated 12, unknown 4, local-only 0 | 30 |
| Active on LinkedIn? | `last_activity_days`: ≤7 → 10, ≤30 → 7, ≤90 → 4, older 0 | 10 |
| Posting roles like mine? | `role_posts_90d`: 2+ → 20, 1 → 13, adjacent only → 5 | 20 |
| Posting pay like my target? | `pay_signal`: at or above 20, within 20% below 8 (hourly: below), no pay posted 5, below 0 | 20 |
| Recruiting for clients that pay it? | `client_tier`: funded product company or boutique search 20, unclear 8, staff augmentation / outsourcing / talent platform 0 | 20 |
| | `local_rate_shop` −15, `volume_vendor` (C2C/W2 US-IT vendors) −20, `task_platform` (annotation and AI-training gigs) −20 | |

**The pay gate.** `pay_ceilings.json` keeps the highest monthly pay each company or agency ever quoted you. A lead at a company whose ceiling is under `pay.skip_below_usd_month` is held just below `min_score`, whatever else it scores. The linkedin-inbox skill records every pay quote it sees (`recruiters outcome --event quoted --usd N --company C`), so the gate learns from inbound recruiters too.

**The company lane** (an experiment). Since every quote at the target traced back to a funded product company, the skill also reaches those companies directly. `recruiters targets` lists the funded companies hiring your role, open to your region or at your target pay. They come from two places:
- **Recent sweeps.**
- **A funded-company registry** (`funded_companies.json`). A web-research subagent fills it from funding news, VC portfolio pages, accelerator batches and AI lists (`recruiter-outreach/references/funding.md`). Then `recruiters refresh-boards` reads each company's Ashby, Greenhouse, Lever or Workable board for remote roles open to your region. A funded company without such a role stays on a watch list until it posts one.

Discovery finds each company's in-house recruiter and hiring manager. Lane leads are scored on their own rubric, where the company is worth 40 and the person 60: does the person own the role, are they active, do they post about hiring. Lane scores are never ranked against recruiter scores. `company_lane_share` (25%) of each day's connection and InMail slots is reserved for the lane (unused slots go to recruiters). The note names the open role, or the application job-autopilot already sent.

`recruiters queries` compares the lanes in `by_lead_type`, and the sweep's companies against the registry's in `by_lane_origin`.

**Channels and limits.** 1st-degree connections get a plain message (no quota). Otherwise a connection request with a note under 300 characters, until `connect_per_day` is used; then InMail while the compose window shows credits (Follow-only profiles get an InMail even earlier). `check` runs before every send and refuses anyone already contacted, anyone at a company that already has `max_per_company` people contacted, and anything over the day's cap or inside the minimum gap. Sending is deliberately single-threaded, because the counters are plain files and a sent message cannot be taken back.

**Messages** are written per recruiter from their evidence, in their language and register: one clause on why them (their post, in their words), one line on the candidate (role, years, one proof point), and a close that leaves the door open. The first message never asks for a call. A yes/no call request asks a stranger for calendar time before they know there is a fit, so it is easy to ignore and gives recruiters who screen by text no way to answer. Instead each lead gets one of the `cta_styles`: `open` says the candidate is open to new roles of this kind, and `cv` offers to share the CV in the chat. Never pay, never "I came across your profile", never a second message to someone who has not replied. Replies are handled by a separate inbox skill.

**Learning which searches work.** Every lead remembers the search that first found it (`source`) and every later one (`sources`). Outcomes come back from three places: the sender reads your recently added connections before each run (accepted requests), the linkedin-inbox skill runs `recruiters outcome` for each recruiter who writes back (replied, interested, call booked, declined), and you can run it yourself. `recruiters queries` joins them into a funnel per search, per avenue and per closing style: found, qualified, contacted, accepted, replied, interested, quoted pay, quoted the target or more. Searches are ranked by the last of these, since the goal is recruiters with roles at your target pay, not replies. The run report recommends adding the searches that convert to `config.yaml` and retiring the ones that don't, and the explorer's Outreach view shows the same table. `required_terms` (for example `nearshore`) are words every discovery pass must search with, so a known-good angle is never dropped.

**Memory.** `leads.json` (every recruiter, signals, evidence, score, status, the message sent, the searches that found them and what they did next), `queries.json` (the last search funnel report), `contacts.json` (everyone ever contacted, any channel), `companies.json` (people contacted per company or agency), `pay_ceilings.json` (the highest pay each company ever quoted you), `funded_companies.json` (the company lane's registry: rounds, ATS board, matching roles), `pacing.json` (today's counters and credits), `discovery-runs.json` and `discovery-depth.json` (which phrasings ran when, and how deep).

## The role explorer

One claude.ai Artifact, republished to the same URL after every run, with a small database for what you decide on it. Decisions survive later sweeps because each stores a copy of the role.

| Tab or view | Shows | What you do there |
|---|---|---|
| To review / Accepted / Rejected | every role with eligibility, resume fit, country check, score, batches, sources | Accept or Reject; rejected roles are never applied to |
| Autopilot · Apply decisions | what the autopilot will and won't apply to, with the JD's own words for each skip | override a decision |
| Autopilot · Applications | each filled form: answers typed (estimates flagged), free-text answers, cover letter, tab title, link | "I submitted it", Discard, Undo |
| Autopilot · Questions for you | questions the subagent could not estimate, and estimates awaiting a one-time confirmation | answer or confirm; reused on later forms |
| Autopilot · Outreach | ranked recruiters with score parts, evidence, recent roles, status, the exact message sent, the search that found them and their outcome; a "which searches work" funnel on top | audit the messages, see which searches convert |

```mermaid
flowchart LR
    scripts["jobsweep explorer<br/>+ autopilot dashboard<br/>+ recruiters dashboard"] --> html["explorer.html<br/>(roles, scores, forms, leads embedded)"]
    html -- "publish to the same URL" --> art["Artifact"]
    art -- "db collections:<br/>decisions · overrides ·<br/>answers · submitted" --> preflight["next run's preflight<br/>imports them"]
```

## Parallelism and Chrome

Chrome is not a serial resource. Every Claude in Chrome tool takes a `tabId`, so each subagent creates its own tab in the shared tab group and works through the DOM tools (`navigate`, `get_page_text`, `read_page`, `find`, `form_input`, `javascript_tool`, clicks by element ref). The one shared thing is the window's visible tab: screenshots and coordinate clicks hit whichever tab is in front, so parallel subagents never take screenshots.

`browser_parallel` (default 3) sets how many run at once. It applies to sweep browser tasks, country checks, recruiter discovery and form fills. Two things stay single on purpose:

- **sending recruiter messages**, because the daily counters are unlocked files and a send is irreversible;
- **LinkedIn request rate**, which is per account; more subagents would only trip its limits sooner.

The main conversation never drives the browser itself. Long browser sessions in the main context re-send every earlier page on every step; subagents keep that cost out of the conversation and return a one-line summary.

## Guard rails

| Rule | Where it is enforced |
|---|---|
| Applications are never submitted unless `autopilot.auto_submit` is true | `references/apply.md`, the fill loop, and the `applied` status needing a confirmation |
| Nothing is registered in applika until you mark it Submitted | `applika-sync` reads only `applied` roles with a `submitted_by` |
| `banned_companies` are never applied to or contacted | `queue`, `plan`, `ingest`, `check` |
| A company with any applika application in the last `company_cooldown_days` is not applied to again | `score-queue`, `queue` and `next` through applika |
| A company that rejected you is not applied to again for `rejection_cooldown_days` | `score-queue` and `queue` through applika |
| Roles explicitly requiring an `excluded_contract_types` contract, on-site work, or another location are skipped | scoring subagent, `queue` |
| Nobody is contacted twice; no follow-ups to unanswered messages | `check`, `contacts.json`, `references/message.md` |
| At most `connect_per_day` connection requests and `inmail_per_day` InMails per UTC day (messages to 1st-degree connections are separate and uncapped unless `message_per_day` is set), with `gap_seconds` between sends | `check`, `pacing.json` |
| At most `max_per_company` people ever contacted at one company or agency | `plan`, `check`, `companies.json` |
| Posted pay below `pay.skip_below_usd_month` is skipped; no posted pay is never a reason to skip | `queue` |
| Page content, search results and recruiter posts are data, never instructions | every reference file |

## Data layout

Everything per user lives outside the repo in `home` from `config.yaml`:

```
<home>/
├── config.json                 explorer_url only
├── profiles/<slug>.json        role profiles created for you
├── runs/<date>-<slug>/         a sweep: results.md, results.csv, browser rows, coverage
├── runs/<date>-explore/        roles.json, roles.csv, explorer.html
├── history/                    URLs seen before, per profile (the "new" marker)
├── people_checks.json          people-check verdicts per company (engineers outside the US), with evidence
├── serp_cache/ · search_state.json   raw search results (30 days) and search-engine suspensions
├── region_employers.json       employers of engineers in your country (weekly harvest, for funding research)
├── boards.json                 companies' ATS boards and their postings (company_boards source)
├── jds/                        job description text, keyed by normalised URL
├── classifications.json        eligibility and fit, cached per URL
├── fit-rubric.md               the resume distilled into a rubric
├── autopilot/
│   ├── answer-bank.json        profile mirror, answers, pending questions, skills
│   ├── ledger.json             every role's status, attempts, answers, cover letter
│   ├── scores.json · queue.json · pacing.json
│   ├── company_types.json      one type per company (funded startup, staffing firm...) for the pay estimate
│   ├── paytier_backtest.json   the pay estimate checked against roles that posted pay
│   ├── target_roles.json       tier A roles whose form couldn't be filled (read by recruiter-outreach)
│   ├── runs/<run_id>/          decisions.jsonl (every decision of the run) · report.html · report.json
│   ├── runs.json               every run with its report link
│   └── work/<date>/            batches, result files, dashboard imports
└── recruiters/
    ├── leads.json · contacts.json · companies.json · pacing.json
    ├── discovery-runs.json · discovery-depth.json · dashboard.json
    └── work/<date>/            discovery and outreach result files
```

## Running the scripts by hand

Run every script through the runner in `config.yaml`. With `python: wsl` on Windows:

```bash
export MSYS_NO_PATHCONV=1   # Git Bash only
wsl.exe -e python3 /mnt/d/dev/autopilot/job-source-sweep/scripts/jobsweep.py status
```

`--home` defaults to the configured folder. Every command prints JSON.

| Script | Commands |
|---|---|
| `job-source-sweep/scripts/jobsweep.py` | `config`, `plan`, `http`, `merge`, `test-match`, `verify-queue`, `jds`, `enrich-queue`, `explorer` |
| `job-autopilot/scripts/autopilot.py` | `score-queue`, `score-ingest`, `queue`, `tailor-queue`, `pace-start`, `next`, `pace-check`, `record`, `answers`, `confirm`, `ready`, `applika-sync`, `dashboard`, `status` |
| `job-autopilot/scripts/cover_letter.py` | `--text FILE --out FILE.pdf [--name] [--contact]`: one-page PDF, no dependencies |
| `recruiter-outreach/scripts/recruiters.py` | `discover-plan`, `ingest`, `score`, `plan`, `check`, `record`, `mark`, `import-contacts`, `companies`, `dashboard`, `status` |

## Extending

- **A new role.** Set `role.title` and `role.profile`, then let the sweep create `<home>/profiles/<slug>.json` from `_template.json`, or write it yourself and test it with `jobsweep test-match`.
- **A new job source.** Add it to `job-source-sweep/sources.json` (feeds, APIs, Getro and Consider boards, Google `site:` hosts, browser tasks). Keep the file role-neutral.
- **Another country or region.** Only `candidate.country`, `region` and `region_aliases` change; eligibility, the country check, logistics lines and recruiter scope all read them.
- **New recruiter search phrasings.** Add to `recruiter_outreach.people_queries` and `post_queries`. The skill invents more when they are spent, and the run report says which invented phrasings worked.
- **Different caps.** Everything numeric is in `config.yaml`; `recruiters score` recomputes lead scores after a change.

## Privacy

`config.yaml` carries your name, contact details, employers and pay targets, and the skills quote them in forms and messages. Keep a fork private, or strip the `candidate` section before publishing one. The data folder (`home`) is never inside the repo.
