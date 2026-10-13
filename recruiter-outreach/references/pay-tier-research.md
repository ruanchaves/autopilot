# Why recruiter-outreach scores the way it does: the 2026-10-13 pay-tier study

This note records the evidence behind the October 2026 changes to recruiter scoring, discovery and the learning report. The question was: which recruiters bring roles at the target pay (`pay.target_usd_month`, $10k/month at the time), and how do they differ from the ones whose roles top out at $8k, $7k and below?

## Method

- **Source.** The user's whole LinkedIn inbox, searched with LinkedIn's own messaging search for pay words (USD, per month, salary, budget, compensation, rate, range, annual, hourly, k/year, salário, pretensão, mensual, screening, interview and others). That surfaced 556 conversations, and 354 were read in full (the latest 20 messages of each).
- **Quotes.** In 120 of those threads the recruiter, not the candidate, stated a number. Each was normalised to USD per month: yearly ÷ 12, hourly × 160, BRL at 5.4 per USD, AED at 3.67 per USD. Ranges count at their top.
- **Tiers.** The bands were ≥ $10k (the target), $8k to under $10k, and under $8k. For each recruiter the study noted the client they were hiring for, how pay was stated, how the role was framed, who wrote first, the screening process and the language.

The per-recruiter data stays with the user's other private data: `<home>/recruiters/pay_ceilings.json`, filled by `recruiters import-quotes`. The repo keeps only the conclusions.

## Findings

About 9 of the 120 quoting recruiters (7%) reached the target: 6 remote roles, 1 onsite relocation role and 2 within a few hundred dollars of it.

What the recruiters at the target had in common:

1. **They named the client, and it was a funded product company.** Examples were YC-backed and Series A AI startups, a Stanford-born healthtech, and a government-backed venture studio. Recruiters under the floor wrote "a US-based client" or named a consultancy placing contractors at an end client.
2. **The role was framed as ownership.** "Owns the AI system end to end", "founding", "applied research" and "take over the founder's technical work" were typical. Lower bands offered staffing: "AI Engineer for our client", 2-month contracts, task work, "side hustle".
3. **Pay was stated as an annual USD salary, or a monthly figure at the target.** Apart from one part-time evaluation gig, every hourly quote in the inbox was $15 to $50/hr, which is $8k/month or less. An hourly rate was the single strongest marker of a low band.
4. **They came from small specialist search firms or the startup itself.** Typically a founder or partner of a boutique AI search firm, a talent lead at a YC-backed recruiting startup, or an in-house recruiter. None came from high-volume staffing.
5. **They gave the range up front or as soon as asked,** and never asked for the candidate's number first only to bargain down from it.
6. **The screening step was short and human.** "Send the CV and I'll set up a screening with the team." Lower bands used AI-interview links, Google Forms, assessments, platform sign-ups and English checks.
7. **Most wrote first** (six of eight).
8. **Region didn't separate them.** Several target-pay roles were explicitly remote-LATAM. The segment that recruits LATAM engineers for a living, nearshore staff augmentation, was the one with a hard ceiling.

The $8k band was mostly nearshore staff-augmentation agencies with a fixed ceiling ("we can go up to USD 8,000/month plus benefits"). One recruiter put it plainly: roles in their space top out around $8k/month, and above that clients hire elsewhere. These recruiters are friendly and do negotiate, but their ceiling comes from their business model, so asking for more rarely works.

Under $7k (about 100 recruiters):
- US-IT vendors quoting C2C or W2 hourly rates ($20 to $40/hr)
- LATAM talent platforms and nearshore agencies ($2k to $5k/month)
- AI-training and annotation gigs
- domestic roles paid in local currency (R$18k to R$45k a month)

## What changed in the skill, and why

| Change | Where | Reason (finding) |
|---|---|---|
| New `client_tier` signal (20 points): funded product or boutique search 20, unclear 8, staff augmentation 0 | `recruiters.py` `score_parts`, `discover.md` | 1, 2, 4: the client type separated the bands; region did not (8) |
| `hiring_scope` reweighted: nearshore 32 → 15, region/worldwide 35 → 30 | `recruiters.py` | 8: nearshore is the $8k-ceiling segment |
| Activity 20 → 10, role 25 → 20 | `recruiters.py` | Activity and role match were common to every band, so they can't rank pay |
| Hourly `near` counts as `below`; `pay_basis` recorded | `recruiters.py`, `discover.md` | 3: no hourly quote reached the target |
| Penalties `volume_vendor` −20 and `task_platform` −20, with text-pattern fallbacks for leads probed before the signals existed | `recruiters.py`, `discover.md` | US-IT vendors and task platforms were all under $7k |
| Pay gate: `pay_ceilings.json` holds each company's highest quote; a ceiling under `pay.skip_below_usd_month` holds the lead at `min_score − 1` | `recruiters.py` (`rescore`, `ceilings`, `import-quotes`) | 179 of 209 probed leads had `pay_signal: unknown`, so the pay question never ranked anything. The inbox already knew the ceilings of 56 companies |
| `min_score` 45 → 55 | `config.yaml`, `SKILL.md` setup | A nearshore staffing recruiter with no posted pay now scores about 50, so it no longer clears the bar on region and role alone |
| `quoted` outcome (`--event quoted --usd N --company C`), recorded by linkedin-inbox for any recruiter, contacted or not | `recruiters.py`, linkedin-inbox | The goal is target-pay roles. Replies alone rewarded the low bands, which reply most |
| `recruiters queries` ranks searches by `quoted_target` first | `recruiters.py`, explorer | Same reason: optimise the outcome that matters |
| Snowball only from firms proven to place at the target (ceilings, quotes, posted pay) | `recruiters.py` `discover-plan`, `discover.md` | Snowballing from any high scorer had pulled whole low-ceiling platforms into the pile |
| New avenues: VC talent networks (`vc_funds`), client and annual-salary terms in `seeds`, salary-banded post phrasings | `recruiters.py`, `discover.md`, `SKILL.md`, `config.yaml` | 1, 3, 4: funded companies and boutique firms post annual USD bands |
| Job-post authors kept only for postings at the target, or naming a funded product employer | `discover.md` | 1, 3 |
| At most one native-language phrasing per pass; English first | `discover.md`, `SKILL.md`, `config.yaml` | Native-language recruiters quoted local-currency pay well under the floor |
| Retired phrasings: a native-language one, "nearshore staffing recruiter …", "recruiter contract … USD", "… hiring contractor" | `config.yaml` | They target the under-floor segments. One nearshore phrasing per pass stays (`required_terms`, the user's rule of 2026-10-12) |
| Messages lead with an ownership proof point; contract terms stay out of the opening and the close | `message.md` | 2: ownership framing marked the target-pay roles; hourly contracting is what vendors sell |
| `network-rank` orders 1st-degree messaging queues by client tier | `recruiters.py` | LinkedIn rate-limits messaging (an HTTP 429 on day one of the network round), so the order decides who gets reached |

## Company lane (an experiment, added the same day)

**Why.** The study's strongest finding was that every quote at the target traced back to a funded product company, and the recruiter was only a way in. That suggests going to those companies directly: their in-house recruiters, and the people who own the role (head of AI, engineering manager, CTO, a founder at a small startup). Two more facts point the same way. Six of the eight high-tier contacts were inbound, so direct reach is the outbound version of what already worked. And job-source-sweep already collects postings from YC and 17 VC portfolio boards, with posted pay and region eligibility, so target companies can be chosen on pay and region *before* anyone is contacted, which recruiter-first discovery can't do.

**Evidence against.** Before job-autopilot stopped contacting people on LinkedIn, it sent "I applied for X" notes to hiring-side people. In the inbox those got polite acknowledgments (a thumbs-up, "the team will reach out", a wrong-company redirect) and no pay conversation. Those were follow-ups to applications, not introductions, so they may have been the wrong message, but the data does not show hiring managers converting better than good recruiters. That is why the lane is an experiment with a fixed budget share, not a replacement.

**How it works.** `company_targets` keeps companies that posted a role matching `company_lane_title_terms`, that aren't marked closed to the candidate's region, and whose posted pay reaches the target or whose posting came from a VC/YC portfolio or startup board with no pay under the floor. Staffing names, marketplaces, banned companies and known low ceilings are excluded. The list is ranked by open-to-region first, because a US salary on a posting that states no region is often US-only in practice, then posted pay, then the funded source, then fit. Discovery finds at most an in-house recruiter and a hiring manager per company, at first scored with the same rubric (role, scope and pay read from the posting; `client_tier: funded_product`), now with the lane rubric below. `plan` reserves `company_lane_share` (25%) of the connection and InMail slots and backfills any unused slots from the other lane. Lane tasks carry the company's open roles and whether job-autopilot applied there. Hiring managers get an engineer-to-engineer note naming the role, with the same rules: no pay, no call request, English.

**The test.** `recruiters queries` reports `by_lead_type`. After two or three weeks, compare the lane's `quoted_target` and `positive` rates per contacted lead with recruiters'. A hiring manager may say "let's talk" without quoting a number, so `positive` counts too. Raise `company_lane_share` if the lane wins, and set it to 0 if after about 30 contacted lane leads it does no better than recruiters.

## Company lane, second version (same evening)

The first lane run (10 of the day's 20 connection requests) showed two flaws, and the user asked for both fixes. They also asked that the new source go beyond VC portfolio lists to recent funding news.

**1. Lane scores measured the posting, not the person.** Every lane signal except activity came from the company's posting, and `client_tier` was `funded_product` by construction, so every lane lead scored 64-92 whoever they were. A dormant talent-acquisition generalist and a head of ML who had just posted about the role scored alike, and the scores couldn't be compared with recruiter scores either.

**Fix: a lane rubric.**
- The company is worth 40, from its posting: region 15, pay 15 (8 when it's funded but posts no pay), a matching open role 10.
- The person is worth 60: `role_owner` 30, activity 15, `hiring_voice` 15.

`role_owner` asks whether this person owns the posted role. An open-door note works when the reader can act on it. A head of ML or a founder at a small startup can; an executive at a large company or an HR generalist usually can't. `hiring_voice` asks whether their own posts talk about hiring. It is the lane's version of "posting roles like yours", which predicted recruiter quality in the study.

A lane lead whose company has no matching open role is held below the line, because the lane exists to reach companies with an open role. Lane leads have their own contact line (`company_lane_min_score`). `plan` already filled each lane's slots from its own leads, so the two scales never compete for a slot.

Rescoring the 21 lane leads gave 38-80, ordered by how close each person sits to the role. An active AI recruiter at a company with no matching open role would have qualified on the person alone; the gate holds them. A "Head of … AI" at a company with an open role rose above the line.

**2. The lane could only see what the job sweep saw.** By the end of the run the LATAM-open startup targets were used up. Three US-pay targets with no stated region (Fal, Exa, Postman) all turned out to be onsite, and the sweep's eligibility labels couldn't tell.

**Fix: a funded-company registry with its own research and board reading.**
- A research subagent works on the open web, not LinkedIn, so it costs no LinkedIn rate limit. It reads:
  - funding news: TechCrunch, Crunchbase News, Axios Pro Rata, press-release wires, and Sifted and Tech.eu for Europe
  - LATAM startup press (LatamList, Contxto, LAVCA): LATAM-founded startups with US money are the likeliest to hire in the region and pay in USD
  - VC portfolio pages beyond the ones the sweep reads
  - accelerator batches (YC directory, a16z speedrun, AI Grant and others)
  - AI company lists
- For each company it records the rounds and the ATS board.
- `refresh-boards` then reads those Ashby, Greenhouse, Lever and Workable boards through their public APIs on every run. It labels each matching posting from its own location and workplace fields: open to the region, unclear, US-only, other region, or onsite. That is the check the Fal/Exa/Postman targets lacked.
- A funded company without a matching role stays on the watch list, so it becomes a target the day it posts one. LinkedIn-side discovery isn't needed to notice that.

Funding is a good proxy for pay. The study's target-pay quotes all came from funded product companies, and a recent round is when a startup hires.

**How to judge it.** `recruiters queries` now also reports `by_lane_origin`: lane leads whose company came from the sweep, from the registry, or from both. These are judged on the same `quoted_target` and `positive` outcomes as before.

## Caveats

- **Small sample.** About 9 recruiters at the target against about 110 below. Treat the weights as a hypothesis. The `quoted_target` column in `recruiters queries` is the test: after a few runs, compare the share of quoting recruiters at the target with the roughly 7% baseline above.
- **Incomplete reading.** About 200 keyword-matched threads weren't read (most matched only "interview", "rate" or "range"), and each thread was read only to its latest 20 messages.
- **Approximate conversions.** Currency conversion and hourly × 160 are approximations. A ceiling is the highest quote ever seen, so one target-pay quote lifts a company out of the gate.
- **Imperfect text fallbacks.** The inferred tiers for older leads are regex guesses. A new discovery probe overwrites them with explicit signals.
