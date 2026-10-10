---
name: job-source-sweep
description: Sweep ~80 job sources for fresh postings of any role (AI Engineer by default, or any title the user names). Covers remote-job feeds and APIs (Himalayas, Remote OK, Jobgether, Working Nomads, Remotive, We Work Remotely, NoDesk, Cryptocurrency Jobs), Hacker News "Who is hiring" threads, Y Combinator's Work at a Startup jobs, twelve Getro VC portfolio networks (General Catalyst, Khosla, Accel, Menlo, Insight and seven European funds) and five Consider boards (Sequoia, Lightspeed, First Round, Bessemer, Founderful), plus browser passes over LinkedIn Jobs and posts, Remote Rocketship, Wellfound, and Google site: sweeps of 19 ATS platforms and 15 job boards. Produces a deduplicated, date-filtered table with new-since-last-run markers. Can also check each new posting's company for engineers in Brazil with one Google search per company, caching the verdicts. After the sweep it can enrich every role: fetch the job description, classify Brazil/LATAM eligibility from the JD text, and rate fit against the user's resume. It then publishes a filterable role explorer page (an Artifact) that is republished to the same link on every run. On first use it runs an interactive setup (role, age window, depth, platforms to skip, a cap on how many roles to collect, and the Brazil check), and it reruns setup when asked to "set up" or "reconfigure". Use it to search, sweep or prospect job boards for a role, to Brazil-verify the companies found, to classify or rate the roles found, to rebuild the role explorer, or to change the sweep's saved preferences. It does not apply or do outreach.
---

# Job source sweep

This skill searches a fixed catalogue of job sources for one role, then writes a merged table. It runs in two lanes:

- **HTTP lane:** `jobsweep http`. It uses only the Python standard library and needs no browser.
- **Browser lane:** subagents drive the user's Chrome through `claude-in-chrome`, following `references/browser-lane.md`.

Files in the skill folder (shared, role-neutral; don't put personal settings here):
- `sources.json`: the sources. Edit it only to add or remove a source.
- `profiles/<slug>.json`: bundled example role profiles. `_template.json` documents every field.
- `scripts/jobsweep.py`: the `config`, `plan`, `http`, `merge`, `test-match` and `verify-queue` commands.
- `scripts/enrich.py`: the step 8 commands `jds`, `enrich-queue` and `explorer`, registered on `jobsweep.py`.
- `assets/explorer.html`: the role explorer page template.
- `references/`: subagent instructions for the browser lane, the Brazil check, JD eligibility and resume fit.

Per-user files, in `<home>` (default `~/jobsweep`, or `$JOBSWEEP_HOME`):
- `config.json`: the preferences saved by setup mode.
- `profiles/<slug>.json`: role profiles created for this user. They take precedence over bundled profiles with the same name.
- `runs/`, `history/`, `verdicts.json`: sweep output, the seen-before history and the Brazil verdict cache.
- `jds/`, `classifications.json`, `fit-rubric.md`: the step 8 caches. They are keyed by normalised URL, so a role seen again on a later day is never fetched, classified or rated twice.
- `runs/<date>-explore/`: that day's batches, `roles.json`/`roles.csv` and `explorer.html`.

## 0. Running the script

In this file, `jobsweep <cmd> …` is shorthand for `<python> <skill>/scripts/jobsweep.py <cmd> --home <home> …`. `<python>` is `config.python`, which setup detects and saves:

- **Native Python 3.11+** (`python3`, `python` or `py -3`): pass paths as they are.
- **`wsl`** (Windows with Python only inside WSL): run `wsl.exe -e python3 /mnt/c/…/jobsweep.py`, converting every path to its `/mnt/<drive>/…` form, including `--home`. From Git Bash, first `export MSYS_NO_PATHCONV=1`, or Git Bash will mangle the `/mnt/…` paths. Output files appear at the Windows form of `<home>`. Use that form when reading `results.md` or appending browser rows.

Always pass the same `--home` and `--days` to every command in a sweep. Output lands in `<home>/runs/<YYYY-MM-DD>-<slug>/`.

## 1. Setup mode

**Before anything else**, find the runner (see below), then run `jobsweep config`. It prints `exists`, the effective `config`, the available `profiles`, and every skippable source, browser task and host under `options`.

Run setup when **`exists` is false** (first use), or when the user asks to "set up", "reconfigure" or "change my defaults". If `exists` is true and the user did neither, skip to step 2.

### 1a. Detect the runner (no question)

Try these in order and keep the first that prints Python ≥ 3.11: `python3 --version`, `python --version`, `py -3 --version`, `wsl.exe -e python3 --version`. Save the result as `python`: `"python3"`, `"python"`, `"py -3"` or `"wsl"`. If none works, tell the user they need Python 3.11+ (or WSL on Windows) and stop. When rerunning setup, reuse `config.python` if it still works.

### 1b. Ask

Say in one line that this is first-run setup, that it takes about a minute, and that `/job-source-sweep reconfigure` changes it later. Then call **AskUserQuestion three times**, with up to 4 questions per call. Skip any question the user's request already answered and use that answer. When rerunning setup, put the current value first and mark it "(current)". Otherwise, put the recommended value first.

**Call 1**
1. *Role*: "Which role should the sweep look for?" Options: AI Engineer (Recommended), Software Engineer, Data Engineer, Product Designer. The user can type any other title through "Other".
2. *Age window*: "How recent should postings be?" Options: 2 days (Recommended), 1 day, 7 days.
3. *Depth*: "How much should a sweep cover?" Options: Standard, meaning HTTP feeds plus a capped browser pass of about 20 min (Recommended); Quick, meaning HTTP feeds only, about 1 min, no Chrome needed; Full, meaning every browser task, up to about 60 min.
4. *Brazil check*: "Should found companies be checked for engineers based in Brazil?" Options: Only when I ask (Recommended); Always, after each sweep; Never. Explain in the option descriptions that it runs one Google search per company in Chrome.

**Call 2**
5. *Role cap*: "Stop collecting once this many *new* roles are found?" Options: No cap (Recommended), 20, 50, 100.
6. *Skip platforms* (multiSelect): "Any platforms to leave out?" Options: LinkedIn (jobs + posts), Google site: sweeps, Remote Rocketship, Wellfound, VC portfolio networks (Getro + Consider boards). Tell the user in the question text that they can type specific sources through "Other", for example "Remotive, crypto boards, Wellfound". Map those to ids from `options` yourself.
7. *Remote only*: "Only remote roles?" Options: Yes (Recommended), No, including on-site and hybrid.
8. *Table size*: "How many rows should the reply table show?" Options: 15 (Recommended), 30, 50.

**Call 3**
9. *Role explorer*: "After each sweep, build the role explorer page?" Options:
   - Full (Recommended): fetch every job description, classify Brazil/LATAM eligibility, rate resume fit and publish the filterable page. About 10-20 min of background work.
   - Table only: publish the page with sweep data only, without reading the JDs.
   - Off.
10. *Resume* (only if 9 is Full): "Which resume should roles be rated against?" Options: Skip, meaning no fit rating. The user types a path (YAML, Markdown, text or PDF) through "Other". Check that the file exists before saving, and write the path with forward slashes.

### 1c. Save

Map the answers onto config keys and save them all in one call: `jobsweep config --set '<json>'`.

| Key | Values |
|---|---|
| `python` | from 1a |
| `profile` | the role's slug (create it; see step 3) |
| `days` | number |
| `mode` | `quick` / `standard` / `full` |
| `brazil_check` | `on_request` / `always` / `never` |
| `brazil_limit` | companies per sweep for the Brazil check (default 20) |
| `max_new_roles` | `0` = no cap |
| `skip_tasks` | browser ids: `linkedin_jobs`, `linkedin_posts`, `rocketship`, `wellfound`, `google` (all shards) or single shard ids (`ats_a`, `boards_b`, …) |
| `skip_sources` | HTTP source ids. "VC portfolio networks" = every `getro_*` and `consider_*` id |
| `skip_hosts` | individual Google `site:` hosts from `options.hosts` |
| `remote_only` | `true` / `false` |
| `report_rows` | number |
| `explorer` | `full` / `table` / `off` |
| `resume` | path to the resume; `""` means no fit rating |
| `explorer_url` | set by step 8 after the first publish; never ask for it |

If the role has no profile yet, create it now (step 3). Then show the saved settings as a short list and say where `config.json` lives. If the invocation also asked for a sweep, continue into it. Otherwise, offer to run a first sweep.

## 2. Settle this run's parameters

Start from `config`. Anything in the current request overrides it **for this run only**, for example "just Data Engineer today" or "last 7 days". If the user says "from now on", "always" or "remember", also save the change with `config --set`. Don't ask about anything the config or request already settles.

Every mode starts with the HTTP lane, and its results are shown **before** any browser work begins. The modes differ only in how much browser work follows, and each has a hard cap. The plan already leaves out `skip_tasks`, so pick from what remains.

| Mode | Browser tasks | Page caps | Wall-clock cap |
|---|---|---|---|
| `quick` | none | none | ~1 min |
| `standard` | LinkedIn Jobs (first keyword); LinkedIn posts (first keyword); Rocketship; Wellfound (every role slug); each Google shard with the **first** `google_query` only | LinkedIn Jobs: 2 pages. Posts: 30. Rocketship: 2 pages. Wellfound: 3 pages. Each shard: 2 SERPs. | 20 min |
| `full` | every task in `plan.json` | LinkedIn Jobs: 5 pages. Posts: 60. Wellfound: 6 pages. Each shard: 5 SERPs. | 60 min |

Run the tasks in `plan.json` order: Wellfound comes before the Google shards on purpose, because it is the one board where US startups post pay and remote policy, and the Google shards are what the wall-clock cap should cut first.

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
- Set `skip_sources` / `skip_hosts` for sources that obviously can't carry the role. Examples: `cryptocurrencyjobs` for a non-crypto role; `agentic-engineering-jobs.com` for a non-AI role.
- Include a `rocketship` block only if the role exists in Remote Rocketship's title taxonomy.
- `wellfound.roles` lists the Wellfound role slugs (`wellfound.com/role/r/<slug>`). Leave it out to derive one from the role title, or set `[]` to skip Wellfound.
- For a non-engineering role, set `brazil_check_term` (for example `designer`).
- Then run `jobsweep test-match --profile <slug> himalayas jobgether workingnomads --dropped`. Check that the kept titles are the role and the dropped ones are not, then adjust the regexes once if needed.

The user's `skip_*` and `remote_only` settings are merged into every profile at load time. Don't copy them into the profile.

## 4. HTTP lane: results within about a minute

Run:

```
jobsweep http --profile <slug> --days <N> --api-queries 2 --budget 120
```

It prints one line per source as each finishes and takes about 90 seconds. `--api-queries 2 --budget 120` bounds the twelve Getro VC networks, which share one rate-limited host; they are marked `PARTIAL` if the budget cuts them off. The US networks (General Catalyst, Khosla, Accel, Menlo, Insight) are queried first so a cut lands on the European boards. Omit both flags only in `full` mode.

Sources worth knowing about when reading the output:
- `hn_hiring`: the newest two "Ask HN: Who is hiring?" threads via the Algolia API. The posting is the HN comment (its `url`), and `apply_url` holds the first link in it. Most of a month's posts land on the 1st, so a 2-day window on the 1st or 2nd brings in the whole thread.
- `yc_jobs`: Y Combinator's public jobs page, which embeds the Work at a Startup results, searched with `api_queries` and `remote=yes`.
- `consider_*` / `getro_*`: VC portfolio boards. Consider boards return the newest 400 postings in one call, which covers more than 2 days on even the busiest board.
- `himalayas`: the API ignores search terms and serves 20 jobs a page across every category, about 3,700 a day. The sweep reads 60 pages, roughly the last 5 to 8 hours. Running `http` two or three times a day is the only way to cover a full day there.

Then run `merge` straight away and show the user the first table (step 6 format) before starting any browser work. A `FAILED` line means an outage or a changed feed shape. Report it; don't retry in a loop. If the role cap is already reached, or the mode is `quick`, skip step 5.

## 5. Browser lane

1. Run `jobsweep plan --profile <slug> --days <N>` and pick the mode's subset of `tasks` from its output.
2. **Delegate: never drive the browser in the main conversation.** A long browser session in the main context re-sends every earlier page read on every step, which makes it slow and costly. Instead, run the tasks as **sequential** subagents (Agent tool, `general-purpose`, `model: "sonnet"`), one at a time because there is only one Chrome. Give each subagent one task or at most 3 Google shards, with a prompt containing:
   - the path to `references/browser-lane.md`, with an instruction to read it first and to load the `claude-in-chrome` tools through a single ToolSearch call
   - the task entries copied from `plan.json` (label, url, hosts, date_fields), `browser_rows_file`, `window_start`, the page cap and a 4-minute-per-task cap
   - an instruction to reply with only the one-line summary per task
3. After each subagent finishes, run `merge` and tell the user one line, for example: "LinkedIn Jobs: +7 new (23 total)". Then check the stop conditions. Don't paste the table again until the end.
4. If `browser_rows_file` already has a `complete:true` coverage line for a task from today, skip that task. A rerun then resumes rather than repeating work.
5. If a subagent reports a CAPTCHA or "unusual traffic" page on Google, skip the remaining Google tasks. If it reports a login wall on LinkedIn, skip the remaining LinkedIn tasks. If LinkedIn posts returns 0 rows because the page exposes no post permalinks (a known DOM limitation), skip the remaining posts tasks and record the gap.
6. If a subagent has no Chrome tools, run the remaining tasks yourself under the same caps. If Chrome is unavailable entirely, stop after the HTTP results and say so.

## 6. Merge and report

Run `jobsweep merge --profile <slug> --days <N>`. This step:
- deduplicates by normalised URL, then by company + title
- re-applies the title filter and the date window, keeping unknown dates
- marks each row `new` if its URL has not appeared in an earlier run of this profile
- writes `results.csv` and `results.md` and prints a compact JSON summary

Work from that summary. To build the table, read only the first `report_rows + 10` lines of `results.md` (new rows sort first). Never read the whole file or the `.jsonl` files into the conversation.

If `brazil_check` is `always`, run step 7 now, before replying.

Reply with:
- the unique and new counts, plus rows per source
- every coverage gap and failed source, stated plainly and never presented as "no results". Skipped platforms from the config count as gaps too; list them in one line as "skipped by your settings: …"
- the top `report_rows` new rows as a table: title linked to its URL, company, location, posted date, sources (and Brazil, when checked)
- the paths to `results.md` and `results.csv`
- when `explorer` is not `off`, one line saying the role explorer is being built; then go on to step 8

`merge` also enforces the current `skip_sources` and the profile's `location_exclude` on rows already collected. A setting changed during the day therefore applies without refetching.

## 7. Brazil verification

Run this when `brazil_check` is `always`, or when the user asks for it, for example "verify Brazil" or "check which hire in Brazil". With `never`, run it only if the user asks explicitly in this request. It is also available on an earlier run's results. It adds the Brazil column; it is not a filter.

1. After the final merge, run `jobsweep verify-queue --profile <slug> --days <N> --limit <brazil_limit> [--ttl 30] [--all]`. It lists companies from **new** rows that have no cached verdict younger than `--ttl` days, one entry per company, with the busiest companies first. It stops at `--limit`, and reports how many were left over, how many rows reused a cached verdict, and how many rows had no company. `--all` also includes rows seen in earlier runs. Raise `--limit` only when the user asks.
2. If the queue is empty, say so and skip to step 4.
3. Split the queue into batches of about 10. Run each batch as a **sequential** subagent (`general-purpose`, `model: "sonnet"`). Its prompt should contain the path of `references/brazil-verification.md`, an instruction to read that first and to load the chrome tools in one ToolSearch call, the batch's queue entries, and `verdicts_file`. Each subagent replies with one line. If a subagent reports `blocked`, don't start further batches; the leftover companies stay queued for a later run.
4. Run `merge` again. It folds the verdicts into the cache at `<home>/verdicts.json` and stamps 🟢/🔴 on each row, linked to the Google search used. The summary now includes Brazil counts, and blocked checks appear as coverage gaps.
5. In the report:
   - Give the green/red counts and the number of companies still unchecked.
   - Add the Brazil column to the table.
   - Say plainly that 🔴 means no match on Google's first page, not that the company has no one in Brazil.

The query is `<company> <term> brazil site:linkedin.com`. The search word defaults to `engineer`. For a non-engineering role, set `"brazil_check_term"` in the profile (for example `designer`). Verdicts are cached per term, so an engineer verdict is never reused as a designer verdict.

When results from Remote OK or Remotive are shown, credit them with "Job data by Remote OK" / "Job data by Remotive". Their terms require it.

## 8. Enrich and explore

Run this step after the final merge, and after step 7 if it ran, when `explorer` is `full` or `table`. Also run it whenever the user asks to "build the explorer", "classify the roles" or "rate them against my resume".

It works on the union of **every profile run today**, so a main sweep and a LATAM pass become one table. Pass `--profiles a,b` to narrow that, or `--date YYYY-MM-DD` to rebuild an earlier day. Every stage is cached and resumable: rerunning it only does what is missing.

With `explorer: table`, skip straight to 8e.

**8a. Fetch the job descriptions.** Run `jobsweep jds` in the background; it takes about 1 min per 100 new roles.
- It tries each platform's public API first (Greenhouse, Lever, Ashby, Workday, SmartRecruiters, Workable, Oracle, and LinkedIn's guest view), then JSON-LD, then page text.
- Its summary gives ok / failed / signup counts and the path to `jd_fetch_failures.csv`.
- `signup` means a sign-up wall that shows only a teaser (DailyRemote). Those count as failures, not JDs.
- Don't retry failures in Chrome unless the user asks.

**8b. Classify Brazil eligibility.** Run `jobsweep enrich-queue --stage eligibility`. It prints `batches`, each with a `batch` file and an `out` file. Launch one subagent per batch **in parallel** (Agent tool, `general-purpose`, `model: "sonnet"`; no browser needed), with this prompt:

> Read `<skill>/references/jd-eligibility.md` first and follow it exactly. Batch file: `<batch>`. Output file: `<out>`. Reply with only the final message the reference specifies.

If a subagent stalls, stop it and rerun `enrich-queue`. Finished lines are kept, and only the rest is queued again.

**8c. Rate resume fit.** Do this only when `resume` is set.
1. Run `jobsweep enrich-queue --stage fit`.
2. If it prints `rubric_needed: true`, write the rubric following part A of `references/resume-fit.md`, then run the command again.
3. Launch one parallel subagent per batch. Use the same prompt shape, pointing at `references/resume-fit.md` part B.

Only roles classified 1 (open) or 2 (nothing stated) are rated. When the resume file changes, its hash changes, and every role is re-rated against a new rubric.

**8d. Brazil hiring check.** Do this only when `brazil_check` is `always`, or when the user asks.
1. Run `jobsweep enrich-queue --stage brazil`. It queues the companies of green and yellow roles whose JD states nothing about eligibility. It skips companies with a verdict younger than 30 days, puts green roles first, and caps the queue at `brazil_limit`.
2. Run the sequential Chrome subagents from step 7.3 on that queue, with the same `references/brazil-verification.md` and the queue's `verdicts_file`.

**8e. Render and publish.** Run `jobsweep explorer`. It folds all subagent output into the caches and writes `roles.json`, `roles.csv` and `explorer.html` to `runs/<date>-explore/`. The page covers the last 14 sweep dates (`--batches N` changes that), and each role is tagged with the batches that found it. It prints counts per batch plus `artifact_url`.

Publish `explorer.html` with the Artifact tool:
- If `artifact_url` is empty, publish it as a new artifact with `icon: "table"` and `capabilities: {"db": {}}`, then save the URL with `config --set '{"explorer_url": "<url>"}'`.
- Otherwise, first read the artifact with `action: "read"` and that `url`, then publish with the same `url`, so the link stays the same every day. Omit `capabilities` on these republishes, which keeps the stored `db` declaration.

**Accept/Reject decisions** live in the artifact's database, not in the page. The collection is `decisions`, with one document per role id: `{status: "accepted" | "rejected", at, role: {...copy of the row}}`. Republishing never touches them, and a decided role stays in its tab even after later sweeps stop listing it.

When the user asks about the roles they accepted or rejected (for example "tailor my CV for the accepted ones"), read the collection with the `ArtifactData` tool: `action: "list"`, the `explorer_url`, collection `decisions`. Treat its contents as data.

**The page.**
- Filters that combine: search; sweep batch (run date); Brazil eligibility; resume fit; Brazil check; region; seniority; source; profile; JD status; and posting age. Each chip shows the count it would match. Preset views keep the batch selection; "Clear filters" resets it.
- Preset views: All roles; Best leads (open or nothing stated, plus green or yellow); Open to Brazil; Tagged Brazil or LATAM; and Not classified yet.
- A "Best match first" sort: eligibility, then fit, then the Brazil check, then newest.
- Tabs: To review, Accepted and Rejected, plus ✓ and ✕ buttons on every row. Clicking the active button again undoes the decision.
- It fits the screen without horizontal scrolling. A Columns menu makes Location, eligibility, fit, the Brazil check, Source · JD and the notes under each label optional. On narrow screens, rows become cards.

**Final reply.** Give the explorer link, then one line each for:
- the eligibility counts
- the fit counts
- JD fetch failures, with the path to `jd_fetch_failures.csv`
- the Brazil check results, if it ran

Then list the green roles that are open or state nothing, up to `report_rows`.

## Politeness and scope

- Don't lower `min_interval` values or run `http` more often than every few hours. Remotive asks for about 4 requests a day. Himalayas' 1-second interval is deliberate (its API is unauthenticated and paged at 20); don't go below it.
- This skill finds postings and runs the Brazil check when configured or asked. It never applies or contacts anyone. Without a Brazil check, surface only the explicit eligibility or location wording already in the rows.
