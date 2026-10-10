# Brazil eligibility from the job description

You classify one batch of job descriptions (JDs) by whether someone based in Brazil can take the role. You need no browser and no web access.

## Input

- The batch file is a JSON list of `{id, key, file, title, company}`. `file` is the JD text: a few header lines (Title, Company, Location, Posted, URL, Fetched via), then the description.
- The output file is `elig_out_<n>.jsonl`, next to the batch file.

## Work loop

Go through the entries in order. For each one:

1. Read its `file` once.
2. Decide.
3. Append the verdict line to the output file right away, with a short `python -c` that uses `json.dumps` (safe quoting), or a quoted heredoc.

Never re-read a file you have already classified, and never revisit earlier entries. If a JD is confusing, make your best call and move on. A batch that stalls must not lose finished work, which is why every line is written as soon as it is decided.

## Categories: pick exactly one

**1. Explicitly open.** The JD or its location says the role is open worldwide, "anywhere" or globally. Or it names Brazil, LATAM, Latin America, South America, or "Americas" including LATAM countries.
- A requirement to be authorised to work in Brazil or LATAM still counts as 1.
- None of these are 1 on their own: "Remote", "global company", or a list of countries outside LATAM.

**3. Explicitly restricted.** The JD body states one of these:
- a visa, work-authorisation or citizenship requirement, such as "must be a US citizen", "must be authorized to work in the US", "unable to sponsor visas", "no sponsorship", "security clearance / US person (ITAR/EAR)" or "right to work in the UK/EU";
- that candidates must live, reside or be based in a specific country outside LATAM, such as "must live in the US" or "must reside in the UK".

It must be stated in the body, not only in a location tag. Generic EEO text, E-Verify notices and "proof of eligibility per applicable law" boilerplate don't count.

A role tied to one LATAM country that excludes the others counts as 3. For example: "must be authorized to work in Colombia; no sponsorship".

If a JD is both 1 and 3, choose 3 only when the restriction excludes someone based in Brazil. Otherwise choose 1.

**2. Nothing stated.** Everything else: the role is neither explicitly open nor explicitly restricted. A typical case is a "US Remote" tag that the body never develops.

## Output line: one per entry, valid JSON

```json
{"id": "<id from the batch>", "category": 1, "residency_required": false, "region": "Brazil", "evidence": "<verbatim quote, max 200 chars>"}
```

- `category`: 1, 2 or 3.
- `residency_required`: true when the body says candidates must live in a specific country or region outside LATAM.
- `region`: a short label, such as US, Worldwide, Brazil, LATAM, EU, UK, Canada, India or unclear.
- `evidence`: a verbatim quote supporting the category. For category 2, use the location tag.

At the end, check that the output has exactly one valid line per input entry, and fix any missing or duplicate lines.

Final message: `elig batch <n>: <count> - 1:<a> 2:<b> 3:<c>`.
