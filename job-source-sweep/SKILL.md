---
name: job-source-sweep
description: Sweep ~80 job sources for fresh postings of the role in the repo's config.yaml (or any title the user names). Covers remote-job feeds and APIs (Himalayas, Remote OK, Jobgether, Working Nomads, Remotive, We Work Remotely, NoDesk, Cryptocurrency Jobs, web3.career's AI and ML pages), Hacker News "Who is hiring" threads (the whole current thread, every run), Y Combinator's public job pages, the a16z portfolio board, fifteen Getro VC portfolio networks (General Catalyst, Khosla, Accel, Menlo, Insight, seven European funds and the Solana, Electric Capital and Dragonfly crypto boards) and five Consider boards (Sequoia, Lightspeed, First Round, Bessemer, Founderful), plus browser passes over LinkedIn Jobs and posts, Remote Rocketship, Wellfound, Work at a Startup's signed-in search, and Google site: sweeps of 19 ATS platforms and 15 job boards. Produces a deduplicated, date-filtered table with new-since-last-run markers. Reads companies' own Ashby/Greenhouse/Lever/Workable boards through their APIs. Can also check each company for engineers working there from the user's country or region (search-engine site:linkedin.com/in queries through the ddgs library, judged by a subagent; never LinkedIn search), caching the verdicts. After the sweep it can enrich every role: fetch the job description, classify location eligibility (the user's country and region) from the JD text, and rate fit against the user's resume (all personal settings come from the repo's config.yaml). It then publishes a filterable role explorer page (an Artifact) that is republished to the same link on every run. On first use it runs an interactive setup (role, age window, depth, platforms to skip, a cap on how many roles to collect, and the country check), and it reruns setup when asked to "set up" or "reconfigure". Use it to search, sweep or prospect job boards for a role, to country-verify the companies found, to classify or rate the roles found, to rebuild the role explorer, or to change the sweep's saved preferences. It does not apply or do outreach.
---

# Job source sweep

This skill searches a fixed catalogue of job sources for one role, then writes a merged table. It runs in two lanes:

- **HTTP lane:** `jobsweep http`. It uses only the Python standard library and needs no browser.
- **Browser lane:** subagents drive the user's Chrome through `claude-in-chrome`, following `references/browser-lane.md`.

Files in the skill folder (shared, role-neutral; don't put personal settings here):
- `sources.json`: the sources. Edit it only to add or remove a source.
- `profiles/<slug>.json`: bundled example role profiles. `_template.json` documents every field.
- `scripts/jobsweep.py`: the `config`, `plan`, `http`, `merge`, `test-match` and `verify-queue` commands.
- `scripts/companies.py`: company facts shared with job-autopilot and recruiter-outreach. It holds:
  - the `boards`, `people-queue`, `people-ingest`, `search`, `people-harvest` and `people-harvest-ingest` commands;
  - the search layer (`Searcher`);
  - the ATS board readers, posting-region labels and employer-type patterns;
  - the people-check cache.
- `scripts/serp_helper.py`: one ddgs search, run inside the search virtualenv (`search.python` in config.yaml).
- `scripts/enrich.py`: the step 8 commands `jds`, `enrich-queue` and `explorer`, registered on `jobsweep.py`.
- `assets/explorer.html`: the role explorer page template.
- `references/`: subagent instructions for the browser lane, the country hiring check, JD eligibility and resume fit.
- `scripts/userconfig.py`: reads `<repo>/config.yaml`, the one file with everything personal. The other skills import it too.

Per-user files, in `<home>` (default `~/jobsweep`, or `$JOBSWEEP_HOME`):
- `config.json`: only `explorer_url` (state). Preferences are in the repo's `config.yaml`.
- `profiles/<slug>.json`: role profiles created for this user. They take precedence over bundled profiles with the same name.
- `runs/`, `history/`: sweep output (each run also writes `funnel.json`: per source, what was fetched and what each filter removed) and the seen-before history.
- `people_checks.json`: the people-check verdicts per company. `verdicts.json` is the older country-check cache, no longer written.
- `boards.json`: companies' ATS boards and their postings, read by the `company_boards` source.
- `serp_cache/`, `search_state.json`, `region_employers.json`: the search layer's raw results and engine suspensions,
  and the weekly employer harvest (`jobsweep people-harvest`, judged with `references/people-harvest.md`).
- `jds/`, `classifications.json`, `fit-rubric.md`: the step 8 caches. They are keyed by normalised URL, so a role seen again on a later day is never fetched, classified or rated twice.
- `runs/<date>-explore/`: that day's batches, `roles.json`/`roles.csv` and `explorer.html`.

## 0. Running the script

In this file, `jobsweep <cmd> …` is shorthand for `<python> <skill>/scripts/jobsweep.py <cmd> …`. `--home` defaults to `home` in `config.yaml`, so it can be omitted. `<python>` is `python` in `config.yaml`, which setup detects and saves:

- **Native Python 3.11+** (`python3`, `python` or `py -3`): pass paths as they are.
- **`wsl`** (Windows with Python only inside WSL): run `wsl.exe -e python3 /mnt/c/…/jobsweep.py`, converting every path to its `/mnt/<drive>/…` form, including `--home`. From Git Bash, first `export MSYS_NO_PATHCONV=1`, or Git Bash will mangle the `/mnt/…` paths. Output files appear at the Windows form of `<home>`. Use that form when reading `results.md` or appending browser rows.

Always pass the same `--home` and `--days` to every command in a sweep. Output lands in `<home>/runs/<YYYY-MM-DD>-<slug>/`.

## 1. Setup mode

**Before anything else**, run `jobsweep config`. It prints whether `config.yaml` exists (`exists`), its path (`config_yaml`), the effective `config`, the available `profiles`, and every skippable source, browser task and host under `options`.

Everything personal lives in **`config.yaml` at the repo root** (`<repo>/config.yaml`): the candidate's facts, the resume, the target role and pay, the data folder (`home`), the Python runner, and this skill's preferences under `sweep:`. The only per-home state the script writes is `<home>/config.json` with `explorer_url`. A new user copies the repo and edits `config.yaml`; the comments in it explain every key.

Run setup when `exists` is false, when `job-source-sweep` is not in `setup_done` (the list in `config.yaml` of skills whose first-run setup has run; a fresh clone starts with it empty, so the `candidate`, `resume`, `role` and `pay` sections still hold the previous owner's values and must be replaced), or when the user asks to "set up", "reconfigure" or "change my defaults". Otherwise skip to step 2. The other two skills run this setup first when their own first run comes before the sweep's.

### 1a. Detect the runner (no question)

Try these in order and keep the first that prints Python ≥ 3.11: `python3 --version`, `python --version`, `py -3 --version`, `wsl.exe -e python3 --version`. Write the result as `python:` in `config.yaml` (`python3`, `python`, `py -3` or `wsl`). If none works, tell the user they need Python 3.11+ with PyYAML (or WSL on Windows) and stop.

### 1b. Ask

Say in one line that this is first-run setup, that it takes a couple of minutes, and that `/job-source-sweep reconfigure` or editing `config.yaml` changes it later. Then call **AskUserQuestion**, up to 4 questions per call. Skip any question the user's request or the existing `config.yaml` already answers, and put the current value first marked "(current)" when rerunning.

**Call 1**
1. *Role*: "Which role should the sweep look for?" Options: AI Engineer (Recommended), Software Engineer, Data Engineer, Product Designer; any other title through "Other". → `role.title`, and `role.profile` (the slug; create the profile in step 3).
2. *Age window*: "How recent should postings be?" Options: 2 days (Recommended), 1 day, 7 days. → `sweep.days`
3. *Depth*: "How much should a sweep cover?" Options: Standard, meaning HTTP feeds plus a capped browser pass of about 20 min (Recommended); Quick, meaning HTTP feeds only, about 1 min, no Chrome needed; Full, meaning every browser task, up to about 60 min. → `sweep.mode`
4. *Country check*: "Should found companies be checked for people already working there from your country?" Options: Only when I ask (Recommended); Always, after each sweep; Never. Explain that it runs one Google search per company in Chrome. → `sweep.country_check`

**Call 2**
5. *Role cap*: "Stop collecting once this many *new* roles are found?" Options: No cap (Recommended), 20, 50, 100. → `sweep.max_new_roles` (0 = no cap)
6. *Skip platforms* (multiSelect): "Any platforms to leave out?" Options: LinkedIn (jobs + posts), Google site: sweeps, Remote Rocketship, Wellfound, YC Work at a Startup (signed-in search), VC portfolio networks (Getro, Consider and a16z boards); specific sources through "Other". Map them to ids from `options`. → `sweep.skip_tasks` / `sweep.skip_sources` / `sweep.skip_hosts`
7. *Remote only*: "Only remote roles?" Options: Yes (Recommended), No. → `sweep.remote_only`
8. *Table size*: "How many rows should the reply table show?" Options: 15 (Recommended), 30, 50. → `sweep.report_rows`

**Call 3**
9. *Role explorer*: "After each sweep, build the role explorer page?" Options: Full (Recommended): fetch every job description, classify location eligibility, rate resume fit and publish the filterable page, about 10-20 min of background work; Table only; Off. → `sweep.explorer`
10. *Resume* (only if 9 is Full): "Which resume should roles be rated against?" Options: Skip (no fit rating); a path (YAML, Markdown, text or PDF) through "Other". Check that the file exists. → `resume.base`

**Call 4, only when `job-source-sweep` is not in `setup_done`** (the candidate section is empty or someone else's): ask for the resume path first if question 10 didn't, then ask for the candidate's country and the wider region job posts use for it (for example Brazil and LATAM), the city and time zone, the target pay in USD per month, and their current employers or clients (never applied to). → `candidate.country`, `candidate.region`, `candidate.region_aliases`, `candidate.city`, `candidate.timezone`, `pay.target_usd_month`, `banned_companies`. Then **read the resume** and fill the rest of `candidate` from it without asking: name, email, phone, LinkedIn and GitHub URLs, languages and native language, years of experience and the year they started, level, headline, a two-line `pitch`, `core_areas`, `domains`, `stack`, three to five `stories` (proof points the resume supports), `location_pitch`, `work_authorization`, `sponsorship`, `employment_types`, `excluded_contract_types` (ask: local contract types they won't take), `working_hours`, `availability`, `relocation`. Show the filled section and ask one question: "Does this describe you?" with options Yes (Recommended) and "let me correct something" (Other).

### 1c. Save

Edit `config.yaml` with the Edit tool, key by key, keeping its comments. Don't use `jobsweep config --set` for preferences: it only stores `explorer_url`. If the role has no profile yet, create it now (step 3). Add `job-source-sweep` to `setup_done`. Then show the saved settings as a short list and say where `config.yaml` lives. If the invocation also asked for a sweep, continue into it. Otherwise, offer to run a first sweep.

## 2. Settle this run's parameters

Start from `config`. Anything in the current request overrides it **for this run only**, for example "just Data Engineer today" or "last 7 days". If the user says "from now on", "always" or "remember", also save the change by editing `config.yaml`. Don't ask about anything the config or request already settles.

Every mode starts with the HTTP lane, and its results are shown **before** any browser work begins. The modes differ only in how much browser work follows. `quick` runs no browser task. For `standard` and `full`, **the plan is the mode table**: `jobsweep plan` reads `sweep.lane_caps.<mode>` from `config.yaml` (defaults in `CONFIG_DEFAULTS`), emits only the keywords and queries that mode allows, stamps every task with its own `caps` (`pages`, `posts`, `batches`, `budget_minutes`) and prints `wall_clock_minutes` and `lane_budgets_minutes`. Copy a task's `caps` into its subagent prompt; don't carry a page table in your head. The plan already leaves out `skip_tasks`.

Two lanes have a minutes budget of their own, outside `wall_clock_minutes`, because they are the two that produced interviews (one via the Lever shard, one via LinkedIn Jobs; 2026-10-27): `linkedin_jobs` (every keyword in `linkedin.jobs_keywords`, each location, in **one** tab, since paging LinkedIn in parallel pauses its search) and `google_direct` (the shards flagged `direct` in `sources.json`: Lever, Greenhouse and Ashby, each alone, with the first few `google_queries` at full depth). The other Google shards (`google_shard`) run the first query at shallow depth. The Chrome-free `ats_search` HTTP source runs every phrasing on the same direct hosts, so the Google pass only needs the top phrasings.

Run the tasks in `plan.json` order: LinkedIn Jobs first, then Wellfound and Work at a Startup, then the direct-hire shards, then the rest. Wellfound and Work at a Startup come before the Google shards on purpose, because they are the boards where funded startups post pay and remote policy with no staffing layer in between, and the job-board shards are what the wall-clock cap should cut first. Work at a Startup needs the user signed in to a free account (`references/browser-lane.md`, `yc_waas`); a login wall is a coverage gap to report, not something to work around. The task reads the signed-in **companies** list, which honours the query and the "US visa not required" filter and scrolls through every match; the `/jobs` page ignores the query (tested 2026-10-15). If the extension is attached to another browser than the signed-in one, switch with `select_browser` before the task.

`deep` is an add-on to any browser mode: it opens Google-shard postings to read their JSON-LD. Only do it when asked, for at most ~30 postings.

**Stop conditions.** Stop starting new browser tasks when either of these happens:
- the wall-clock cap is reached
- `max_new_roles > 0` and the latest merge summary's new count is at least `max_new_roles`

Record the unrun tasks as coverage gaps, labelled with the reason, for example "not run: role cap of 50 reached". Then go on to the final merge. Report partial coverage rather than overrunning a cap.

## 3. Pick or create the profile

Look the slug up in `profiles` from `jobsweep config`. If it's missing, copy `_template.json` to `<home>/profiles/<role-slug>.json`, never into the skill folder. Fill it in for the role, delete `_help`, and tell the user in one line that you created it and where. Guidance for filling it in:

- `include` is the only filter on most feeds, because they ignore search terms and return every role on the board. Cover the role's synonyms, abbreviations and spelling variants: `back.?end`, `\bsre\b`, `ux|ui designer`, `product designer`. Aim for about 5–20 patterns that are broad for the role and narrow against neighbouring roles.
- `exclude`: keep the template's noise list. Add the unwanted seniority, management or neighbouring-role terms. Drop anything that *is* the role; a Product Manager profile must not exclude `\bmanager\b`.
- For `google_queries` and `api_queries`, use 3–6 phrasings. `remoteok_tags` should be one Remote OK tag or `""`. For `rss_category`, use `programming`/`engineering` for software roles and `all` otherwise.
- Set `skip_sources` / `skip_hosts` for sources that obviously can't carry the role. Examples: `agentic-engineering-jobs.com` for a non-AI role. Don't skip the web3 sources (`cryptocurrencyjobs`, `web3career`, `getro_solana`, `getro_electriccapital`, `getro_dragonfly`) for an engineering role: web3 employers hire worldwide in USD and carry a pay premium, so they are a pay lane, not a niche (2026-10-15).
- Include a `rocketship` block only if the role exists in Remote Rocketship's title taxonomy.
- `wellfound.roles` lists the Wellfound role slugs (`wellfound.com/role/r/<slug>`). Leave it out to derive one from the role title, or set `[]` to skip Wellfound.
- For a non-engineering role, set `country_check_term` (for example `designer`).
- Then run `jobsweep test-match --profile <slug> himalayas jobgether workingnomads --dropped`. Check that the kept titles are the role and the dropped ones are not, then adjust the regexes once if needed.

The user's `skip_*` and `remote_only` settings are merged into every profile at load time. Don't copy them into the profile.

## 4. HTTP lane: results within about a minute

Run:

```
jobsweep http --profile <slug> --days <N> --api-queries 2 --budget 120
```

It prints one line per source as each finishes and takes about 90 seconds. `--api-queries 2 --budget 120` bounds the twelve Getro VC networks, which share one rate-limited host; they are marked `PARTIAL` if the budget cuts them off. The US networks (General Catalyst, Khosla, Accel, Menlo, Insight) are queried first so a cut lands on the European boards. Omit both flags only in `full` mode.

Sources worth knowing about when reading the output:
- `hn_hiring`: the newest two "Ask HN: Who is hiring?" threads via the Algolia API. The posting is the HN comment (its `url`), and `apply_url` holds the first link in it. Posts land all month long (on 2026-10-15, 2 of the 179 on-role posts were inside a 2-day window and 90 inside 30 days), so the source has its own `window_days` of 35: every run sees the whole current thread, and new-since-last-run marks what is new. Most HN posts apply by email or a careers link, which the autopilot's email channel handles.
- `yc_jobs`: Y Combinator postings from the public pages that embed Work at a Startup data (`ycombinator.com/jobs`, its `/jobs/role/...` pages and the anonymous `workatastartup.com/jobs`). Checked 2026-10-15: those pages ignore every query and page parameter and embed fixed samples (about 110 postings in all), so the fetcher merges them and keeps a 45-day window. The real search (about 2,900 engineering jobs) needs a free Work at a Startup account: that is the browser task `yc_waas`.
- `a16z_jobs`: Andreessen Horowitz's portfolio board (`jobs.a16z.com`, 18,000+ jobs), server-rendered: one search per `api_query`, 25 results by relevance with the posting's own date, pay and ATS apply link; `window_days` 14 because the results aren't date-sorted. Greylock's board ignores its query and is listed under `_unreachable`.
- `consider_*` / `getro_*`: VC portfolio boards. Consider boards return the newest 400 postings in one call, which covers more than 2 days on even the busiest board. `getro_solana`, `getro_electriccapital` and `getro_dragonfly` are crypto ecosystem and VC boards (added 2026-10-15).
- **Web3 lane** (2026-10-15): web3 employers hire worldwide in USD or stablecoins and pay a premium, so the sweep reads them directly instead of through Google. `web3career` scrapes web3.career's AI and ML category pages (server-rendered, 15 rows a page, the posting's own date and pay; "estimated" salaries are dropped). `cryptocurrencyjobs` is the site-wide RSS; the title filter keeps the on-role posts. `company_boards` seeds 30 web3 employers' Greenhouse/Lever/Ashby boards from `sources.json` (`company_boards.seed`) before any role link points at them. Rows from all of these carry `sector: web3`, which the autopilot's pay estimate reads (a non-US HQ no longer pushes a web3 company to "likely below").
- `ats_search` (2026-10-27): the Chrome-free twin of the direct-hire Google shards. `site:jobs.lever.co`, `site:boards.greenhouse.io` and `site:jobs.ashbyhq.com` searches for every profile `google_query` through the search layer (`companies.Searcher`: ddgs 9.16.1 with DuckDuckGo, Yandex and Yahoo in turn, three indexes, each with its own circuit breaker, paced, cached, at most `max_queries` a run). A search index date is not a posting date, so each hit is resolved through the board's public API (one read per board) and the row carries the posting's own date, location and pay; postings the board no longer lists, and onsite ones, are dropped. `window_days` 7. Boards found here enter `boards.json` on the next merge, so `company_boards` keeps reading them.
- `ats_directory` (2026-10-27): the third route to the same boards, with no search engine at all. The tenant slugs of Lever, Ashby and Greenhouse are enumerated from the web archives' URL indexes (Common Crawl's newest monthly indexes, the Wayback CDX API) into `<home>/ats_tenants.json`; each run reads the boards of `max_boards` tenants not yet checked, newest first, through the ATS APIs. A board that lists the target role joins `boards.json` for `company_boards`; the rest are re-checked after `recheck_days`. Rows carry the board's own date, location and pay. The user's rule: keep the search engines and this route both, redundancy over elegance.
- `company_boards`: companies' own Ashby, Greenhouse, Lever and Workable boards, read through their public APIs.
  - **Which companies:** every company whose role link pointed at such a board, plus recruiter-outreach's
    funded-company registry, plus the boards `sources.json` seeds (`company_boards.seed`, web3 employers since
    2026-10-15). They are kept in `<home>/boards.json`; a seeded entry carries its `sector`.
  - **Reading:** a board is re-read at most every 20 hours. `jobsweep boards` reads them by hand.
  - **What comes back:** postings with a target title term and an engineering word, each with the board's own
    posted date, location and pay.
  - **Window:** it looks back 21 days (`window_days`), because a company already known to hire the role is worth an
    older posting.
  - **Region labels** go on the row as `region_hint` only.
- `himalayas`: the API ignores search terms and serves 20 jobs a page across every category, about 3,700 a day. The sweep reads 60 pages, roughly the last 5 to 8 hours. Running `http` two or three times a day is the only way to cover a full day there.

**Run the whole HTTP lane.** Pass source ids to `http` only when the user asks for specific sources. A run that fetches a subset (the 2026-10-14 autopilot run fetched three sources of 29 and lost HN, YC and every VC board) must list every source it left out as a coverage gap; the autopilot's run report marks them "not run" by itself.

Then run `merge` straight away and show the user the first table (step 6 format) before starting any browser work. A `FAILED` line means an outage or a changed feed shape. Report it; don't retry in a loop. If the role cap is already reached, or the mode is `quick`, skip step 5.

## 5. Browser lane

1. Run `jobsweep plan --profile <slug> --days <N> --brief` (add `--mode full` to override `sweep.mode`, or `--only linkedin_jobs,google_direct` for the autopilot's daily mode). Its `tasks` are already the mode's subset, each with its `caps` and a `file` (`tasks/NN.json`) holding the full entry (URL, hosts, fallback URLs, rows file, window). `--brief` keeps the URLs out of this conversation; the subagents read their task files.
**Which Chrome.** Before launching any Chrome subagent, load `mcp__claude-in-chrome__list_connected_browsers` and `mcp__claude-in-chrome__select_browser`, list the browsers, and select the one whose `osPlatform` is `browser_os` in `config.yaml` (Windows). Never select or keep the macOS Chrome (user rule 2026-10-15); if no browser on that OS is connected, treat Chrome as unavailable and say so. Every subagent repeats the check itself (its reference says how), because the extension can reconnect to the other Chrome mid-run.
2. **Delegate: never drive the browser in the main conversation.** A long browser session in the main context re-sends every earlier page read on every step, which makes it slow and costly. Instead, run the tasks as subagents (Agent tool, `general-purpose`, `model: "sonnet"`), up to `browser_parallel` of them **at once** (config.yaml, default 3), launched in one message. Chrome is not a serial resource: each subagent creates its own tab in the shared tab group and works through the DOM tools, so tasks only compete for the sites' own rate limits (keep LinkedIn tasks to one subagent at a time; Google shards and the other boards can run side by side). Give each subagent one task or at most 3 Google shards, with a prompt containing:
   - the path to `references/browser-lane.md`, with an instruction to read it first, to load the `claude-in-chrome` tools through a single ToolSearch call, and to use only its own tab (no screenshots, since other subagents share the window)
   - the task `file` paths from the brief plan (one per task), and each task's `caps` in one line; the files hold the URLs, hosts, rows file and window
   - an instruction to reply with only the one-line summary per task

   The prompt is therefore three lines; never paste task JSON into it.
3. After each subagent finishes, run `merge` and tell the user one line, for example: "LinkedIn Jobs: +7 new (23 total)". Then check the stop conditions. Don't paste the table again until the end.
4. If `browser_rows_file` already has a `complete:true` coverage line for a task from today, skip that task. A rerun then resumes rather than repeating work.
5. If a subagent reports a CAPTCHA or "unusual traffic" page on Google, the remaining shard tasks run on Bing, then DuckDuckGo (each task's `fallback_urls`; `references/browser-lane.md`, google_shard): tell the next shard subagents to start on the fallback engine instead of Google. A shard is a coverage gap only when all three engines refused it. If it reports a login wall on LinkedIn, skip the remaining LinkedIn tasks. If LinkedIn posts returns 0 rows because the page exposes no post permalinks (a known DOM limitation), skip the remaining posts tasks and record the gap.
6. If a subagent has no Chrome tools, run the remaining tasks yourself under the same caps. If Chrome is unavailable entirely, stop after the HTTP results and say so.

## 6. Merge and report

Run `jobsweep merge --profile <slug> --days <N>`. This step:
- deduplicates by normalised URL, then by company + title
- re-applies the title filter and the date window, keeping unknown dates
- marks each row `new` if its URL has not appeared in an earlier run of this profile
- writes `results.csv` and `results.md` and prints a compact JSON summary

Work from that summary. To build the table, read only the first `report_rows + 10` lines of `results.md` (new rows sort first). Never read the whole file or the `.jsonl` files into the conversation.

If `sweep.country_check` is `always`, run step 7 now, before replying.

Reply with:
- the unique and new counts, plus rows per source
- every coverage gap and failed source, stated plainly and never presented as "no results". Skipped platforms from the config count as gaps too; list them in one line as "skipped by your settings: …"
- the top `report_rows` new rows as a table: title linked to its URL, company, location, posted date, sources (and the country check, when run)
- the paths to `results.md` and `results.csv`
- when `explorer` is not `off`, one line saying the role explorer is being built; then go on to step 8

`merge` also enforces the current `skip_sources` and the profile's `location_exclude` on rows already collected. A setting changed during the day therefore applies without refetching.

## 7. People check: engineers outside the US

**The question:** do engineers who work at the company live in the candidate's country (`candidate.country`),
elsewhere in its region, or elsewhere outside the US? It is the only accepted evidence that a company hires outside
the US. A posting's location label, in the JD or the ATS field, is written by automated systems and is often wrong
(user rule 2026-10-14).

**How it's searched:** a script runs the searches through the search layer (`scripts/companies.py`, `Searcher`).
- **Engines:** the ddgs library (9.16.1) with DuckDuckGo, Yandex and Yahoo in turn, three indexes with a circuit breaker each (`search:` settings in config.yaml; Google and Bing are refused: the one returns nothing to a script, the other ignores `site:`, 2026-10-27).
- **Queries:** profile-country subdomains such as `site:br.linkedin.com/in engineer "Company"`. The subdomain proves
  the country.
- **Judging:** a subagent judges the results without a browser (`references/people-judge.md`), rejecting namesakes.
- **Fallback:** what it can't settle goes to Google and Yahoo in Chrome (`references/people-check.md`).

**Never LinkedIn's own search**, which rate-limits the
user's account fast.

**When it runs:** when `sweep.country_check` is `always`, or when the user asks ("verify Brazil", "check which hire
in my country"). With `never`, run it only if the user asks explicitly in this request. It adds a column; it isn't a
filter. Inside /job-autopilot this step is skipped (the autopilot only reads the cached verdicts, for its location
override).

1. After the final merge, run `jobsweep verify-queue --profile <slug> --days <N> --limit <country_check_limit> [--all]`.
   - It fetches the search results for companies from **new** rows that have no fresh verdict, busiest first, and
     writes judge batches of 25.
   - **Judge them first:** one parallel subagent per batch, no browser, with the prompt "Read
     `<skill>/references/people-judge.md` and follow it exactly. Batch file: `<batch>`. Output file: `<out>`.
     country: `<country>`, region: `<region>`."
   - **Then the Chrome fallback** (step 3 below) runs for the companies marked `needs_google`. Get its batch with
     `jobsweep people-queue --mode chrome`.
   - A positive verdict stays fresh for 90 days, "none" for 30.
   - `--all` also includes rows seen in earlier runs.
2. If the queue is empty, say so and skip to step 4.
3. **The Chrome fallback:** run its batches **one at a time**, never in parallel, as Chrome subagents (`general-purpose`, `model: "sonnet"`).
   - **Prompt:** "Read `<skill>/references/people-check.md` and follow it. Batch file: `<batch>`. Output file:
     `<out>`. country: `<country>`, region: `<region>`."
   - **Why one at a time:** Google and Yahoo are the engines whose snippets show where people live. In the 2026-10-14 tests
     Google blocked after 13 searches; Bing, DuckDuckGo and Brave blocked on the first; Yahoo never blocked.
   - **When every engine blocks:** if a subagent reports Google and Yahoo both blocked, start no further batches. The rest stays queued.
4. Run `merge` again. It folds the verdicts into `<home>/people_checks.json` and stamps each row:
   - 🟢 engineers in the country or region
   - 🟡 engineers only elsewhere outside the US
   - 🔴 none found

   Each stamp links to the search result behind it.
5. In the report:
   - give the counts per verdict and the number of companies still unchecked;
   - add the column to the table;
   - say plainly that 🔴 means no located engineer on the searched pages, not that the company has nobody there.

When results from Remote OK or Remotive are shown, credit them with "Job data by Remote OK" / "Job data by Remotive". Their terms require it.

## 8. Enrich and explore

Run this step after the final merge, and after step 7 if it ran, when `explorer` is `full` or `table`. Also run it whenever the user asks to "build the explorer", "classify the roles" or "rate them against my resume".

It works on the union of **every profile run today**, so a main sweep and a regional pass become one table. Pass `--profiles a,b` to narrow that, or `--date YYYY-MM-DD` to rebuild an earlier day. Every stage is cached and resumable: rerunning it only does what is missing.

With `explorer: table`, skip straight to 8e.

**8a. Fetch the job descriptions.** Run `jobsweep jds` in the background; it takes about 1 min per 100 new roles.
- It tries each platform's public API first (Greenhouse, Lever, Ashby, Workday, SmartRecruiters, Workable, Oracle, and LinkedIn's guest view), then JSON-LD, then page text.
- Its summary gives ok / failed / signup counts and the path to `jd_fetch_failures.csv`.
- `signup` means a sign-up wall that shows only a teaser (DailyRemote). Those count as failures, not JDs.
- Don't retry failures in Chrome unless the user asks.

**8b. Classify location eligibility.** Run `jobsweep enrich-queue --stage eligibility`. It prints `batches`, each with a `batch` file and an `out` file, plus `context` (the candidate's `country`, `region` and `region_aliases` from `config.yaml`). Launch one subagent per batch **in parallel** (Agent tool, `general-purpose`, `model: "haiku"`; no browser needed). The task is rubric-bound classification with a verbatim quote as evidence, which Haiku does at a twentieth of Sonnet's price (changed 2026-10-28); the autopilot's score pass, which decides what gets applied to, stays on Sonnet. Prompt:

> Read `<skill>/references/jd-eligibility.md` first and follow it exactly. country: `<context.country>`, region: `<context.region>`, region_aliases: `<context.region_aliases>`. Batch file: `<batch>`. Output file: `<out>`. Reply with only the final message the reference specifies.

If a subagent stalls, stop it and rerun `enrich-queue`. Finished lines are kept, and only the rest is queued again.

**8c. Rate resume fit.** Do this only when `resume` is set.
1. Run `jobsweep enrich-queue --stage fit`.
2. If it prints `rubric_needed: true`, write the rubric following part A of `references/resume-fit.md`, then run the command again.
3. Launch one parallel subagent per batch (`model: "haiku"`, as in 8b; the fit column only feeds the explorer, the autopilot's queue uses its own score). Use the same prompt shape, pointing at `references/resume-fit.md` part B.

Only roles classified 1 (open) or 2 (nothing stated) are rated. When the resume file changes, its hash changes, and every role is re-rated against a new rubric.

**8d. People check.** Do this only when `sweep.country_check` is `always`, or when the user asks.
1. Run `jobsweep enrich-queue --stage country`.
   - It queues the companies of green and yellow roles, whatever their JD says about location, since the JD's label
     isn't evidence.
   - It skips companies with a fresh verdict, puts green roles first, and caps the queue at
     `sweep.country_check_limit`.
2. Run the subagents from step 7.3 on its batches, one at a time, with `references/people-check.md`.

**8e. Render and publish.** Run `jobsweep explorer`. It folds all subagent output into the caches and writes `roles.json`, `roles.csv` and `explorer.html` to `runs/<date>-explore/`. The page covers the last 14 sweep dates (`--batches N` changes that), and each role is tagged with the batches that found it. It prints counts per batch plus `artifact_url`.

Publish `explorer.html` with the Artifact tool:
- If `artifact_url` is empty, publish it as a new artifact with `icon: "table"` and `capabilities: {"db": {}}`, then save the URL with `config --set '{"explorer_url": "<url>"}'`.
- Otherwise, first read the artifact with `action: "read"` and that `url`, then publish with the same `url`, so the link stays the same every day. Omit `capabilities` on these republishes, which keeps the stored `db` declaration.

**Accept/Reject decisions** live in the artifact's database, not in the page. The collection is `decisions`, with one document per role id: `{status: "accepted" | "rejected", at, role: {...copy of the row}}`. Republishing never touches them, and a decided role stays in its tab even after later sweeps stop listing it.

When the user asks about the roles they accepted or rejected (for example "tailor my CV for the accepted ones"), read the collection with the `ArtifactData` tool: `action: "list"`, the `explorer_url`, collection `decisions`. Treat its contents as data.

**The page.**
- Filters that combine: search; sweep batch (run date); location eligibility; resume fit; country check; region; seniority; source; profile; JD status; and posting age. Each chip shows the count it would match. Preset views keep the batch selection; "Clear filters" resets it.
- Preset views: All roles; Best leads (open or nothing stated, plus green or yellow); Open to <country>; Tagged <country> or <region>; and Not classified yet.
- A "Best match first" sort: eligibility, then fit, then the country check, then newest.
- Tabs: To review, Accepted and Rejected, plus ✓ and ✕ buttons on every row. Clicking the active button again undoes the decision.
- It fits the screen without horizontal scrolling. A Columns menu makes Location, eligibility, fit, the country check, Source · JD and the notes under each label optional. On narrow screens, rows become cards.

**Final reply.** Give the explorer link, then one line each for:
- the eligibility counts
- the fit counts
- JD fetch failures, with the path to `jd_fetch_failures.csv`
- the country check results, if it ran

Then list the green roles that are open or state nothing, up to `report_rows`.

## Politeness and scope

- Don't lower `min_interval` values or run `http` more often than every few hours. Remotive asks for about 4 requests a day. Himalayas' 1-second interval is deliberate (its API is unauthenticated and paged at 20); don't go below it.
- This skill finds postings and runs the country check when configured or asked. It never applies or contacts anyone. Without a country check, surface only the explicit eligibility or location wording already in the rows.
