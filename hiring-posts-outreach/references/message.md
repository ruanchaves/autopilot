# Contact the authors of hiring posts (Chrome subagent)

You write to the people who posted a hiring for the target role, best-ranked first, on the user's behalf and with their authorisation. The writing rules, the Chrome setup, the channel rules and the result format are the ones in **`<repo>/recruiter-outreach/references/message.md`**: read that file first and follow it. This page lists only what is different here.

## What a task carries

Each entry in `tasks` is a post author, not a probed recruiter. There are no `score`, `signals` or `vibe` fields. Instead:

- `post`: the post that ranked them: `url`, `posted`, `roles`, `company`, `pay_text`, `region_text`, `excerpt`. This is the whole evidence. Take the tone from the excerpt (terse, warm, emoji-heavy, Portuguese) and match it, in English.
- `other_posts`: up to two more hiring posts by the same person, for context.
- `rank`: `pay_usd_month` (the highest pay the posts stated, converted), `pay_basis`, `eligibility` (`latam`, `worldwide`, `unclear`), `tier` (0 pay at or above the floor, 1 no pay stated), and for tier-1 `unclear` leads `origin` (the poster's likely country guessed from their name, with `distance_km` from the candidate's country; it only set their place in the order). Never mention the origin guess or anything about where the poster is from: it is a ranking hint, not a fact about them, and the message is built from the post alone.
- `degree` and `first_degree`: from the search card. When `degree` is empty, the profile page tells you: a "Message" button without "Connect" usually means 1st degree.
- `headline`, `company`, `lead_type: "hiring_post"`, `cta`.

## The message

- **The why-them clause names the post.** "Saw your post for a Senior AI Engineer, remote LATAM" or "Saw you're hiring a Forward Deployed Engineer for your enterprise agents team". Use the role words from `post.roles` and the region words from `post.region_text`. Never mention the pay, even though the post stated it.
- **Everything else as in the recruiter rules:** two lines on the candidate (role, years, one proof point from `candidate.stories` that fits the post), the logistics fact only in an InMail or message, the close from `cta` (`open` or `cv`), no call request, no pay, English.
- **When the author is the hiring manager or founder** (the headline says CTO, founder, head of AI, engineering manager), write engineer to engineer: the role or the problem in one clause, then one technical proof point. No recruiter phrasing.
- **When the author is a company page** (a `/company/` URL), skip the task as `skipped` with detail "company page": there is no person to write to.

## Review before every send

Nothing is inserted into LinkedIn until the draft has passed two checks, in this order:

1. **`hiringposts lint --kind <connect|inmail|message> --name "<their name>" --text "<draft>"`** (add `--subject` for an InMail; the command prefix is in your prompt). It fails the draft on the phrases that mark a generated note, dashes, a question at the end, a call request, any pay mention, a missing why-them clause, and template reuse (the share of 4-word phrases already sent to someone else in the last 60 days, across both outreach skills). `verdict: rewrite` means rewrite; `warnings` are read and usually acted on. A draft is never trimmed to dodge a check: fix the sentence the check named.
2. **The reviewer's read, from `<hp>/references/review.md`** (`review_reference` in your task file): read the draft as the recipient would, on a phone, in two seconds. Who, what, why them in the first line; one detail only a reader of this post could know; one proof point, one number; a close that cannot be refused; nothing the candidate could not back; and not the shape of the last three notes you sent today.

Rewrite until both pass, then run `lint` once more on the final text. When the day's notes start sharing a second sentence or a close, change the structure for the next one (proof point first, logistics first, a different story from `candidate.stories`), not only the words. Put the final lint `reuse.share` into the result as `reuse` for each sent contact; `hiringposts record` lints every sent message again and reports `lint_flags` for any that slipped through.

## Order and channel

Strictly the order of `tasks`: the list is already ranked by posted pay and then by region eligibility. For each task run `hiringposts check --kind <kind> --profile <url> --company "<company>"` (the command prefix is in your prompt) and obey it exactly as the recruiter rules say: a 1st-degree lead gets a plain message; otherwise a connection request with a note while requests remain; then InMail while the compose window shows credits; then `deferred`. Don't reorder to use up a channel: a 2nd-degree lead ranked above a 1st-degree lead is contacted first.

There is no `accept_check` here; skip that section.

## Result

Same JSON as the recruiter rules, with `"purpose": "hiring_post"` and every contact carrying `"lead_type": "hiring_post"`. Write it at the `result` path with the Write tool, then reply with the one-line summary.
