# Pre-send review: does this read like a person, and will they accept?

You are reviewing an outreach message before it is sent, as a sales and communication expert. One goal only: **the recipient accepts the invitation (or answers the message)**. Everything below serves that. A note that is accurate, polite and complete but reads as generated or mass-sent is a failed note: recruiters receive several of those a day and have learnt to skip them in the two seconds the invitation pane gives them.

Run this review on every draft, for every channel (connection note, InMail, 1st-degree message), after the draft is written and before any text is inserted into LinkedIn. The deterministic checks come from `hiringposts lint`; this page is the judgement the script cannot make. Both must pass.

## How a recruiter decides

- **They read the first line and your headline, not the whole note.** In the invitation pane the note is truncated and sits under the sender's name and headline. The first clause must already say why them (the post they wrote) and the first line must already say what you are (the role, the years). If "why them" arrives in sentence two, it is never read.
- **They accept when accepting is cheaper than ignoring.** A candidate for the exact role they are filling this week is worth a click. "Open to opportunities" is not: it hands them work. The note must make the match obvious without them thinking.
- **They ignore anything that could have been sent to anyone.** Specificity is the proof that a person read their post. One detail lifted from the post (the role title in their words, the region they named, the team or product) does more than any compliment.
- **They distrust a note that asks for something.** No call, no favour, no "would you be open to". The close is a statement (`open`) or the cheapest yes in text (`cv`). Nothing to refuse means nothing to ignore.
- **They notice the template.** Same second sentence, same close, same proof point across notes: recruiters compare notes with colleagues and see the same text under different greetings. Vary the structure, not just the words.

## The slop markers (rewrite, don't send)

A message fails the review if it shows any of these. The lint catches the literal phrases; you catch the pattern.

1. **Generated openers and fillers.** "I hope this finds you well", "I came across your post", "I'm reaching out because", "I wanted to connect", "I noticed that", "As a ...". A person who read the post starts with the post: "Saw your post for ...".
2. **Corporate vocabulary.** leverage, synergy, cutting-edge, passionate, dynamic, robust, seamless, innovative, state-of-the-art, proven track record, results-driven, fast-paced, wealth of experience, skill set, add value, bring to the table, align with, resonate, journey, landscape, elevate, empower, unlock, delve, spearheaded, tailored, impactful. Each one marks the note as written for nobody.
3. **Enthusiasm you cannot have for a stranger.** "I'm excited", "thrilled", "I'd love to", "would be a great fit", "I admire", "love what you're building", "impressive". The candidate has read one post; the note can only say what the post said.
4. **Rhythm tells.** Lists of three ("LLM, RAG and agents" is a natural pair of nouns; "scalable, reliable and secure" is a tell), em-dashes, balanced parallel clauses, a sentence that restates the previous one, a closing that summarises the note, more than one exclamation mark, a sign-off ("Best regards", "Kind regards", "Cheers") on a LinkedIn note.
5. **Résumé prose.** A paragraph of career history, more than two proof points, numbers stacked for effect ("7+ years, 30M+ users, 10+ projects"), a stack list longer than three names. One role, one number, one proof point that fits the post.
6. **Hedging and padding.** "just", "I think", "maybe", "a bit", "I believe", "kindly", "if possible", "whenever you have a moment", "no worries if not", "feel free to". Say the thing.
7. **The wrong register.** A founder's terse post answered with a formal cover-letter paragraph; a warm Portuguese post answered with a cold note; "Dear Ms. X" on LinkedIn; the candidate's full name in the note. Match the post's temperature, in English, with the first name.
8. **A question at the end.** "Would you be open to ...?", "Worth a chat?", "Any roles that fit?" Every one of these is answered by silence. The close is a statement.
9. **Anything the candidate cannot back.** A proof point not in `candidate.stories`, a tech not in `candidate.stack`, a claimed interest in their product, a shared connection. One checkable false note loses the reply.
10. **The template under the words.** If the second sentence and the close are the same as the last three notes with only the role swapped, it is a mail merge. Change the order (proof point before role, logistics first for a LATAM post), the shape of the proof point, and the close wording within the `cta` style.

## The naturalness test

Read the draft aloud once, as the recipient, on a phone. Then answer:

- Could the candidate have typed this in 40 seconds after reading the post? If it needed a thesaurus, no.
- Does it say who, what, and why them before the first line ends?
- Is there one detail that only someone who read this post would know?
- Is there one proof point, chosen for this post, with one number at most?
- Does it end on something the recipient cannot refuse?
- Would the candidate be glad to see it quoted back to them in a screening call?

Any "no" means rewrite. A rewrite shortens; it never adds.

## Rewrite method

1. Cut the opener to "Hi <first name>, saw your post for <role in their words>, <region in their words>." or the engineer-to-engineer form for founders and managers ("Saw you're hiring a <role> for <the problem the post names>.").
2. One line on the candidate, in a shape that fits the post: a founder post gets the system built ("took a LangGraph agent system from prototype to production at a 30M-customer bank"), a recruiter post gets the role and the years first. One proof point from `candidate.stories`, one number at most.
3. For InMails and messages only, the logistics fact as a plain sentence from `candidate.location_pitch`; never contract terms in a connection note.
4. The close in the `cta` style, worded fresh: `open` names the kind of role and says the candidate is available, `cv` offers the CV in text. Neither asks.
5. Delete every word that would survive in a note to somebody else.

Then run `hiringposts lint` again and re-read once more. Send only when both pass.

## Examples

**Fails** (generated, generic, asks): "Hi Marta, I hope this message finds you well! I came across your post about the Senior AI Engineer role and I'm really excited about the opportunity. With 7+ years of experience in cutting-edge AI, LLMs and RAG, I believe I would be a great fit. Would you be open to a quick chat?"

**Passes** (`open`, recruiter post, LATAM): "Hi Marta, saw your post for a Senior AI Engineer, remote from LATAM. I build LLM and agent systems in Python, seven years in, last at a bank with 30M customers. Open to senior remote AI roles and glad to be on your radar."

**Passes** (`cv`, founder post, engineer to engineer): "Hi Daniel, saw you're hiring an AI engineer to take your support agent to production. That's the work I did at a 30M-customer bank: LangGraph agents, RAG over internal docs, evals. Happy to send my CV here if useful."

**Fails** (template reuse): the third note of the day whose second sentence reads "Senior AI engineer here, 7 yrs in LLM/RAG systems and LangGraph agents, last at a 30M-customer bank." The facts are fine; the sentence has been sent twice already today. Reshape it.
