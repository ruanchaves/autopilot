# Funding research

You are filling the company lane's own source of targets: software companies that raised money recently, or sit in a
VC portfolio or accelerator batch, and may be hiring the candidate's role. You research on the open web with
**WebSearch** and **WebFetch** only. You don't use Chrome or LinkedIn, and you never contact anyone or apply to anything.
The orchestrator merges your result into `funded_companies.json`. A script then reads each company's ATS board, and a
LinkedIn subagent later finds the people at the companies with a matching role.

Why this exists: every pay quote at or above the target traced back to a funded product company
(`references/pay-tier-research.md`). Until now the lane only saw companies the job sweep had already found, and it ran
dry. Funding news and portfolios reach companies whose roles never showed up on a job board the sweep reads.

## Your inputs

From your prompt:
- **Budget and window:** `per_round` (how many companies to check), `funded_since` (oldest announcement date worth a
  look), `min_round_usd` (smallest announced round worth a slot).
- **Already covered:** `known_companies` (in the registry already: skip them unless they're in `recheck`),
  `already_targets` (the job sweep already found them hiring: skip them), `queries_tried` (searches run in the last
  three weeks: don't repeat one), `recheck` (registry companies to look at again).
- **Funds:** `vc_funds`, the portfolios the job sweep already reads.
- **Search terms:** `seeds` (the months in the window, role terms, title terms, region terms).
- **The candidate:** `candidate`, `target_role`, `pay_target_usd_month`, `pay_floor_usd_month`.
- **Excluded:** `banned_companies` (never), `known_low_ceiling` (companies that quoted under the floor: skip them).
- **Output:** `result`, the file to write.

## Where to look

Use at least four of these avenues per pass, always including avenue 6, and spread the companies across them. Prefer breadth over depth.

1. **Funding news.** Search the way the announcements are written. Combine a round, an AI term and a month from
   `seeds.months`:
   - "AI startup raises Series A October 2026"
   - "raises $20 million seed LLM agents"
   - "announces Series B AI platform September 2026"
   - "funding round generative AI startup remote"

   Good sources:
   - **News sites:** TechCrunch's venture coverage, Crunchbase News (its weekly "biggest funding rounds" and
     AI-funding roundups), Axios Pro Rata, Fortune Term Sheet, VentureBeat, SiliconANGLE.
   - **Press releases:** Business Wire and PR Newswire ("announces $X million Series A").
   - **European startups:** Sifted, Tech.eu, EU-Startups. European startups often hire across Americas time zones.
   - **LATAM-founded startups backed by US or LATAM VCs:** LatamList, Contxto, LAVCA, Bloomberg Línea. These are the
     companies most likely to hire in the candidate's region and pay in USD.

   For rounds from the last few weeks, use WebSearch `mode: "extended"`. Otherwise use `"standard"`.
2. **VC portfolio pages.** For funds in `vc_funds` and for AI-heavy funds, open the portfolio page and keep companies
   tagged AI, ML or infrastructure, newest additions first. AI-heavy funds include a16z, Sequoia, Greylock, Accel,
   Index, Lightspeed, Kleiner Perkins, Founders Fund, Khosla, Conviction, Radical Ventures, Felicis, General Catalyst,
   Thrive, Redpoint, Menlo, Amplify, Elad Gil, and, for LATAM, Kaszek, monashees, Canary and Valor. A portfolio
   listing without a dated round still counts: record the round as `"portfolio"` with the fund in `investors`.
3. **Accelerator batches.**
   - The YC company directory, latest two batches, filtered to hiring companies and AI.
   - a16z speedrun, AI Grant, South Park Commons, Neo, Techstars AI.

   Record the round as the batch, for example `"YC F26"`.
4. **Lists.** Forbes AI 50, CB Insights AI 100, "fastest-growing AI startups" lists from the last six months.
5. **Remote-first signals.** Search for "remote-first AI startup hiring Latin America", "AI startup hiring engineers
   in LATAM USD", or a careers page saying "we hire across the Americas". The company still needs funding evidence.
6. **Where engineers in the candidate's region already work.** A company that employs AI engineers in the
   candidate's country is the strongest sign it hires there. Postings' location labels are often wrong (user rule
   2026-10-14).
   - **Queries:** WebSearch for profiles, such as `"AI engineer" OR "machine learning engineer" Brazil
     site:linkedin.com/in`, with the region's other countries in place of Brazil, and with "LLM" or "GenAI" in
     place of the titles.
   - **Reading the results:** read the raw result titles ("Name - Title - Company"). Never trust the prose summary.
   - **What to keep:** collect the current employers that are funded product companies headquartered outside the
     region (US or European startups with a team in the region), then check their funding and board as for any
     other company.
   - **What to record:** `found_via: people:<query>`, plus `people_evidence`: the profile URL and title that showed
     it.
   - **Never use LinkedIn's own search.**

Write each search into `queries_run` exactly as you ran it. Searches that gave nothing go into `dead_queries` too.

## Which companies to keep

Keep software companies building with AI, either an AI product or a product with a serious AI/LLM team, that:
- announced a round of at least `min_round_usd` on or after `funded_since`, **or**
- sit in a VC portfolio or accelerator batch.

Skip these:
- anything in `banned_companies`, `known_low_ceiling`, `known_companies` (unless it's in `recheck`) or
  `already_targets`
- staffing, outsourcing and talent marketplaces
- hardware-only companies and wet-lab biotech without a software team
- token projects without a product team
- companies whose careers page says every role is onsite in one city outside the candidate's region

## For each company you keep

1. **Find the careers page and its ATS.** Most startups use one of these, linked from the careers page:
   - `jobs.ashbyhq.com/<slug>`
   - `boards.greenhouse.io/<slug>` or `job-boards.greenhouse.io/<slug>`
   - `jobs.lever.co/<slug>`
   - `apply.workable.com/<slug>`

   Record `ats: {"kind": "ashby|greenhouse|lever|workable", "slug": "<slug>"}`. The script reads these boards itself
   and keeps reading them on every run, so when a company has one, leave `roles` as `null`. That way a company that
   posts the role next month still gets caught.
2. **No such board?** For a custom page, Rippling, BambooHR, a Notion page or Wellfound, read the open roles and
   report only the ones like the target role (an AI/ML/LLM term from `seeds.title_terms` plus an engineering word),
   each with:
   - `title`, `url`, `posted`
   - `location`: the posting's own location text
   - `remote`: true, false, or null when unknown
   - `pay_usd_month`: yearly ÷ 12, or null
   - `region`, one of:
     - `open`: names the candidate's country or region, Americas, anywhere or worldwide
     - `nearby`: names another country in the candidate's region, for example Colombia for a Brazil-based candidate in LATAM
     - `unclear`: remote with no region stated
     - `us_only`: remote but US/Canada only, or a US city
     - `other_region`: remote in Europe, India and so on
     - `onsite`: not remote at all

   An empty list means you read the page and nothing matched.
3. **Record the company facts:**
   - `website`, `careers_url`
   - `linkedin_url`: the company page, if a search result shows it
   - `description`: one line on what it builds
   - `ai_native`: true when AI is the product
   - `headcount`: an estimate, from LinkedIn or the article
   - `hq`
   - `remote_policy`: remote-first, remote-friendly, hybrid or onsite
4. **Record the rounds** in `funding`: round, `amount_usd` (a number), `date` (YYYY-MM-DD), lead `investors`,
   `source_url`.
5. **Record `found_via`:** `news:<search as run>`, `portfolio:<fund>`, `batch:<name>`, `list:<name>`, `people:<search as run>`, or `recheck`.

For each company in `recheck`, read the careers page again and report its `roles`, plus `ats` if it now has a board.

Stop at about `per_round` companies. At least half of them should have a careers page or board identified.

## Result

Write this JSON to `result` with the Write tool (plain UTF-8, no comments), then reply with one line: companies
found, how many have a board, and how many have a matching role open to the region.

```json
{"kind": "funding", "queries_run": ["AI startup raises Series A October 2026"], "dead_queries": [],
 "sources_read": ["https://techcrunch.com/..."],
 "companies": [{"name": "Example AI", "website": "https://example.ai", "linkedin_url": "https://www.linkedin.com/company/example-ai/",
                "description": "Agents for insurance claims", "ai_native": true, "headcount": 40, "hq": "San Francisco",
                "remote_policy": "remote-first", "careers_url": "https://jobs.ashbyhq.com/example",
                "ats": {"kind": "ashby", "slug": "example"}, "roles": null,
                "funding": [{"round": "Series A", "amount_usd": 25000000, "date": "2026-09-30", "investors": ["Index Ventures"],
                             "source_url": "https://techcrunch.com/..."}],
                "found_via": "news:AI startup raises Series A October 2026", "notes": ""}]}
```
