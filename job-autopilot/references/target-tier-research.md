> **Superseded on 2026-10-27.** The pay estimate, the company-type pass, the autopilot's people check and the A/B/C tiers described here were removed at the user's request (too complex to trust; the floor is now USD 8k). Kept for the record: the engine tests, the search layer and the report design still apply.

# Target tiers: why the autopilot works the way it does (2026-10-14)

The user's goal: applications to roles paying **at least $10k/month** at companies that **really hire outside the
US**, from Brazil or elsewhere in LATAM. This note records the data and the user's rules behind the target tiers,
the pay estimate, the people check, the company job-board source and the per-run report. The plan the user approved
on 2026-10-14 ran in phases 0 to 7. Where the implementation departs from it, the reason is given below.

## What the data showed before the change

The last 8 sweep dates held 1,667 scored roles, plus the autopilot ledger and applika's 302 applications.

- **Pay:** 28% of roles posted $10k+/month and 68% posted no pay at all.
- **Region:** 11% were labelled open to Brazil, LATAM or worldwide.
- **Both:** only **15 roles (0.9%)** did both, and 4 of those were a talent platform or a part-time hourly gig.
- **Our applications:** of 116, 90 went to roles with no posted pay, and only 5 went to roles with posted $10k+ and
  an open region.
- **Low-pay roles:** roles under $8k produced 20 "Beyond the offered range" rejections in applika, almost all from
  staff-augmentation and nearshore agencies.
- **US-marked roles at the target:** 21 of these were closed in applika with no positive outcome (15 ghosted, 6
  rejected).
- **Where the 15 came from:** Wellfound 6, LinkedIn 6, Lupa Hire 2, Himalayas 1. The VC boards had 156 roles at
  $10k+, but only 2 of them were labelled open to the region.

Since most pay and region information is missing, the work is estimating what's missing. Filtering on what is stated
can't reach the goal.

## The user's rules (2026-10-14)

1. **Region labels are not evidence.** "JDs are imprecise and often have mistakes since they are submitted by
   automated systems." Region-filtered job searches (plan item A3) and reading region from JD wording (B4) were
   dropped.
   - **What counts instead:** the only accepted evidence is engineers who work at the company from outside the US.
   - **What follows:** a JD's "US-based only" is overridden when that evidence exists. The role is capped at tier B.
     Citizenship, clearance, export control and work authorization are legal requirements, and still skip.
2. **Never use LinkedIn search directly.** It rate-limits the account fast, and the account is shared with
   recruiter-outreach. The people check runs on search engines with `site:linkedin.com/in` queries.
3. **Full explainability.** Every run publishes a report artifact. It shows every source and query, why each role
   was selected or rejected, who decided, and each posting's estimated age.
4. **The pay estimate (B5) was expected to have the highest return,** so it was built first, together with the
   report.

## Search engines for the people check (Phase 0)

Two tests on 2026-10-14 (`<home>/autopilot/work/2026-10-14/engine_spike/`):

| Engine | Result |
|---|---|
| Google (Chrome) | Snippets show employer and location ("São Paulo, Brazil", "Location: …"), plus `br.linkedin.com` subdomains. It found located engineers for 6 of 13 companies: Wonderful 5, ElevenLabs 3, Constructor.io 2, and others. It showed "unusual traffic" on the 14th search at 4 s spacing, and was **still blocked about 1.5 hours later**: the first real people-check batch was blocked on its first search. |
| Yahoo (Chrome) | No challenge. Its results come from Bing, with "Location:" lines and displayed `br.`/`mx.linkedin.com` paths. Wonderful: 4 of 7 profiles located; ElevenLabs 1 of 7; Constructor.io no results. |
| Bing, DuckDuckGo, Brave (Chrome) | Bot challenge on the first search. |
| DuckDuckGo HTML (script) | One answer with good snippets, then a challenge. |
| Google, DuckDuckGo, Yahoo, Bing fetched from another network (WebFetch) | Error page, CAPTCHA or HTTP 500. Bing answered but ignored `site:`. |
| Startpage, Ecosia, Mojeek | Startpage and Ecosia not tested: Chrome refused page access ("Permission denied by user"), and the agent stopped before Mojeek. |
| Claude WebSearch | Never blocked, but the raw results carry only title and URL. Only 4 of 16 companies had a hit that could be verified. Its prose summary claimed located engineers that the raw results didn't show for 7 companies, so the summary is never evidence. |

**Consequences:**
- **Google is the main engine and Yahoo the second,** at a 10-second spacing, one batch at a time, never in
  parallel.
- **The daily cap is small** (`autopilot.people_check_limit`, 20 companies), filled best candidates first.
- **WebSearch is only a strict fallback:** a country subdomain, plus the company as employer, plus an engineering
  title, all in the raw result.
- **No verdict means try again:** a company the engines couldn't settle is queued again on the next run, never
  marked "none".

Verdicts stay fresh for 90 days when engineers were found and 30 days for "none".

**Pitfalls the test found:**
- Companies named only as a skill ("Jenkins | GitLab").
- Same-named companies: "Sierra Studio" the Brazilian dev shop isn't Sierra the AI company.
- Sales and go-to-market staff: ElevenLabs' Brazil and Mexico profiles were mostly sales. Companies often open a
  country with sales first, so only engineering titles count.

## Searching at scale: the search layer (approved 2026-10-14, second plan)

The Chrome-only check capped tier A at about 20 companies a run. Research and tests on 2026-10-14:

**Options compared:**
- **ddgs** (github.com/deedy5/ddgs, MIT): a keyless metasearch library over about 10 engines, with fallback, proxy
  support and a REST server. It is the one adopted.
- **SearXNG:** needs Docker, and from a single IP its Google and Brave connections get suspended (a CAPTCHA suspends
  an engine for 24 h by default).
- **CrossLinked:** the same `site:linkedin.com` technique; a penetration-testing tool, unmaintained since November
  2024.
- **Google Custom Search API:** closed to new customers, shutting down 2027-01-01.
- **Brave API:** credits only, card required.
- **Serper, Exa, Tavily:** need an account; not adopted.

**Tests from WSL:**
- **Which engines answer a script:** in practice only ddgs's Yahoo engine, which is Bing's index. The first tests
  seemed to show Bing and Yandex answering with the same results. In fact ddgs 9.16.0 has no "bing" or "yandex" text
  engine, and asking for one silently runs "auto", which picks engines at random. That surfaced when a "bing" query
  failed against startpage.com. The helper now refuses engines ddgs doesn't have. Google, Brave, DuckDuckGo, Mojeek
  and Startpage returned nothing.
- **Throughput:** 30 queries in a row, no block, about 1.2 s each.
- **Coverage:** 22 of 30 companies had LinkedIn results; the 8 empties are gaps in Bing's index (Constructor.io,
  which Google found).

**The first ramp step through the full pipeline** (25 companies): 101 queries, 0 errors, 0 suspensions, canary ok,
8.5 minutes.
- **The "retry on Yahoo" in this step was an artifact:** the "bing" queries ran through "auto", so a retry on Yahoo
  recovered 11 of 32 empties. With Yahoo as the only engine, an empty result is accepted after one query.
- **Second ramp step:** see below.

**Query design:**
- **The country is the URL:** `site:br.linkedin.com/in engineer "Company"` returns only profiles registered in Brazil.
- **One query for the rest of LATAM:** `(site:mx… OR site:ar… OR site:co… OR site:cl… OR site:pe… OR site:uy…)`.
- **One company per query:** grouping several companies with OR returned unrelated profiles.
- **Snippets:** Bing's snippets carry LinkedIn's "Experiência: <current employer> · Localidade: <city>" line.

**Namesakes are the main risk to precision.** "Neon" matched a Brazilian fintech, "Octane" two different companies,
and "AZX" mostly AZX Lab, a Brazilian dev shop. So:
- **A subagent judges every result set** against what we know about the company (type, HQ, description, website,
  the role), and marks what it can't settle `needs_google`.
- **Only judged verdicts count toward tiers** (`method` `search-v2` or `chrome-v2`). The 10 Chrome-era verdicts wait
  for `people-queue --recheck`.
- **One-word names never stop early,** and the judge needs a second clue for them.

**The harvest yields little:** reading employers off `site:br.linkedin.com/in "AI Engineer"` gave about 15 employers
per 60 profiles. It runs weekly as a side source for funding research (`region_employers.json`) and never writes
verdicts.

## The pay estimate (B5)

Each role gets a pay tier: `posted_target`, `posted_near`, `posted_below`, `likely_target`, `unclear` or
`likely_below` (`scripts/target.py`). The evidence, strongest first:

1. **Pay the company quoted the user** (`recruiters/pay_ceilings.json`, 57 companies). A quote at the target means
   likely_target. A highest quote under the $8k floor holds the role back (`pay_gate`).
2. **What the company posts for its other engineering roles:** the median across scores, the company job boards and
   the funded-company registry.
3. **Company type x headquarters.** Each company gets one judgement from a subagent (`references/company-type.md`),
   cached in `company_types.json`. On 2026-10-14 that cache held 1,045 companies:
   - by confidence: 496 from the JD, 260 widely known, 53 from a web search, 236 guesses;
   - guesses don't move the estimate.
4. **A funding round** in the last 180 days, or a VC/YC board, combined with a senior title.

**Backtest:** hide the posted pay of the 537 roles that have it (307 companies), estimate, compare
(`autopilot paytier-backtest`).
- **Base rate:** 85.5% of these roles reach the target. Roles that post pay lean toward US product companies under
  pay-transparency laws, so the base rate among roles without posted pay is surely lower.

**Target rate by company type and headquarters:**

| | Funded startup | Big company | Product company | Consultancy | Staffing | Task platform |
|---|---|---|---|---|---|---|
| US/Canada HQ | 95% (238) | 91% (107) | 94% (48) | 94% (31) | 100% (13) | 14% (7) |
| Elsewhere | 82% (17) | 62% (8) | 0% (4) | 0% (8) | 20% (5) | |

**The mapping that came out of it** (`TYPE_PAY`):
- **likely_target:** funded startups, and US/Canada big and product companies.
- **likely_below:** non-US product companies, consultancies and staffing firms, plus task platforms and local
  employers anywhere.
- **unclear, two exceptions to the table:**
  - US staffing firms and consultancies post US salaries for US placements, while their quotes to the user for
    remote LATAM work were under the floor (`recruiter-outreach/references/pay-tier-research.md`).
  - Marketplaces post "up to" ranges they didn't pay the user: micro1 posted $130–160k and quoted $7.5k.

**Results with that mapping:**
- **likely_target:** 401 roles, 92.8% truly at target, against the 85.5% base rate.
- **likely_below:** 41 roles, 75.6% truly under target. That is 5.2× the base under-target rate of 14.5%.
- **By source:**
  - Sibling postings: 96% (224 roles) for likely_target, 100% (6) for likely_below. This is the cleanest number,
    because the role itself is left out.
  - Company type: 89% (172) for likely_target, 80% (30) for likely_below.
- **Caveat:** the company-type mapping was set from this same data, so its figures are in-sample. Re-run the
  backtest as new roles post pay, and change `TYPE_PAY` only if the out-of-sample numbers move.
- **The quote gate is "wrong" on 4 of 5 posted roles,** because micro1 and Insight Global post target-pay ranges.
  The user's own quotes are the better evidence of what they pay the user, so the gate stays.
- **Verdict rule:** "use" needs likely_target above the base rate, and likely_below at least 2× the base
  under-target rate. A plain "base + 10 points" rule is unreachable at an 85% base rate.

**Score change with no posted pay:** likely_target +6, likely_below −8. Posted pay keeps its +10 / +6 / −10.

## Tiers and the queue

- **Tier A:** pay at target (posted or likely) and engineers in the candidate's country or region.
- **Tier B:** one of the two proven, the other unknown.
- **Tier C:** the rest.

The queue takes A, then B, then C. Inside each tier it keeps the source round-robin with the 25-point gap guard
(the user's 2026-10-02 rule against one feed taking over, now applied within tiers). `target_mode: only` drops tier C.

The first rebuild (2026-10-14, before any people check had settled) queued 808 roles: B 445, C 363. Tier A needs
people checks, which accumulate at about 20 companies per run.

Other changes:
- **Cover letters:** `cover_letter: target` writes one for every tier A role.
- **Salary answers:** when the posted range reaches the target, the form answer sits three quarters of the way up
  the range, never under `ask_usd_month`.
- **Blocked tier A forms** go to `target_roles.json`. Recruiter-outreach's company lane then reaches the company's
  hiring manager directly.

## More tier A candidates: the companies' own boards

`job-source-sweep/scripts/companies.py` keeps `boards.json`. It holds every company whose role link pointed at an
Ashby, Greenhouse, Lever or Workable board, plus the funded-company registry, and reads each board through its
public API. The first read on 2026-10-14:

| | |
|---|---|
| Boards read in 2 minutes | 249 (2 errors), 244 after merging boards saved under two names |
| Engineering postings | 12,247 |
| Postings like the target role | 1,865 |
| Of those, onsite | about 54% |
| In the last 21 days and not onsite | 322 |

**The `company_boards` sweep source** feeds those postings into the sweep:
- **Window:** 21 days, since a known hiring company is worth an older posting.
- **What each posting brings:** the board's own date, location and pay. The pay also feeds the pay estimate's
  sibling evidence.
- **First run:** 191 unique roles, 156 of them new.

**Discovery from where engineers work:** the funding-research subagent has a sixth avenue, required in every pass
(`recruiter-outreach/references/funding.md`). It searches for the employers of AI engineers based in the region,
reading raw result titles only.

## The run report

- **What gets logged:** every step writes its decisions to `runs/<run_id>/decisions.jsonl` (step, outcome, reason,
  evidence, who decided). The sweep writes `funnel.json` per run: per source, rows fetched, then what the title
  filter, the date window, the location filter and dedupe each removed.
- **How it's built:** `autopilot run-report` joins those with the ledger, the queue and the caches into a page
  (`assets/run-report.html`).
- **Posting age, strongest method first:**
  1. the ATS API or JSON-LD date, now captured while fetching JDs;
  2. the date the source gave;
  3. the first time the sweep saw the URL, shown as a minimum.

  A posting whose sweep sighting is 7+ days older than its date is flagged as a likely repost.
- **Publishing:** each run's report is a new artifact, and `runs.json` keeps the links, which the explorer shows on
  its Autopilot tab.

## What to watch

- **People-check throughput:** if Google keeps blocking, tier A stays small. Look at the report's "People checks
  this run", and consider spreading the checks over the day.
- **Pay-estimate drift:** re-run `paytier-backtest` when many companies are added. The out-of-sample number is
  sibling postings, at 96%.
- **The point of it all:** the count of tier A applications per week, and their replies.
  - **Interviews aren't tracked yet:** applika records no interview step, so asking the user to log it, or letting
    linkedin-inbox and gmail-inbox record replies, is the missing piece for measuring interviews.
