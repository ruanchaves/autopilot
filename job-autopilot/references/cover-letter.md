# Writing a cover letter (and any free-text answer)

Most recruiters admit they rarely read cover letters. The ones who do read them when the resume is borderline, and they read for one thing: something the resume doesn't show, written by a person. That sets the brief. The letter is short, specific to this company and this role, and sounds like the applicant talking, not like a template or a model. A generic or obviously generated letter costs more than no letter, because it tells the reader the applicant didn't care.

These rules were drawn from hiring managers' and recruiters' own advice (Ask a Manager, engineering hiring guides, recruiter posts, LinkedIn's InMail data, Wikipedia's "Signs of AI writing") in October 2026.

## Shape

- **120 to 200 words. Three short paragraphs. Never more than half a page.**
- **Greeting:** the hiring manager's or recruiter's name if the posting gives one; otherwise "Hi <Company> team," or "Hello,". Never "Dear Sir or Madam" or "To whom it may concern".
- **Paragraph 1 (one or two sentences):** which role, and one concrete thing about this company or this job that connects to the applicant's work. A product they ship, a problem the JD names, a stack choice, something from their engineering blog. If nothing specific is known, name the role and go straight to the story.
- **Paragraph 2 (two to four sentences):** one story from the resume that answers this JD's main requirement. Situation, what the applicant decided or built, a measured result. Add one sentence the resume doesn't say: a trade-off, a judgement call, what was deliberately not built.
- **Paragraph 3 (one or two sentences):** the logistics line and a plain close. The logistics line is factual, not apologetic, and comes from `candidate.location_pitch` (city, time zone, the hours and clients the applicant already works with). Then: "Happy to walk through the architecture on a call." or "Looking forward to hearing from you." Sign with the applicant's first name.

Before writing, pull from the JD: the top two requirements, the named tools, the domain, and anything the company says about itself. Then pick the resume story that matches the top requirement. The applicant's strongest stories are listed in `candidate.stories` (the resume has the details); choose the one this JD cares about, not the most impressive one.

## Voice

- Write the way the applicant would email a colleague they respect: direct, specific, slightly informal in one place.
- Vary sentence length. One short sentence somewhere.
- Say "I built", "I chose", "I shipped". Not "I was responsible for".
- Numbers where the resume has them. "cut review time by 40%" beats "significantly improved efficiency".
- One idea per sentence. Commas and full stops, not semicolons or dashes.
- The applicant's native language (`candidate.native_language`) when the posting is written in it and the company is local; otherwise English.

## Never

These are the tells of a generated letter. The reader recognises them in a sentence, and the letter is binned.

- Openers: "I am writing to express my interest", "I am excited/thrilled to apply", "I was delighted to come across", "As a passionate...".
- Stock praise: "aligns perfectly with", "your innovative company", "mission-driven", "I admire your commitment to".
- Resume adjectives: "proven track record", "results-driven", "detail-oriented", "dynamic", "seasoned", "self-starter", "valuable asset", "unwavering".
- Model vocabulary: delve, tapestry, leverage, showcase, underscore, pivotal, crucial, multifaceted, fostering, robust, seamless, cutting-edge, synergy, testament, elevate.
- Structures: "not just X, but Y"; "it's not about X, it's about Y"; lists of three ("fast, reliable and scalable"); a sentence that restates the previous one; a closing paragraph that summarises the letter; every sentence starting with "I".
- Em-dashes. At most one, preferably none.
- Restating the resume. The reader has it.
- Claims the resume doesn't support. No invented metrics, clients or tools. A denied skill is never mentioned.
- Mentioning pay, visa needs or that the applicant is "looking for new opportunities".
- More than 200 words.

## Check before saving

Read the finished letter once as the hiring manager would, and answer three questions. If any answer is wrong, rewrite the offending paragraph.
1. Could this letter be sent to a different company unchanged? It must not be.
2. Does paragraph 2 tell one concrete story with a result, or does it list skills? It must tell a story.
3. Does any sentence contain a phrase from the "Never" list, or sound like something a model would write? Remove it.

## Example shape (not a template to fill in)

> Hi Clara,
>
> I'm applying for the Senior AI Engineer role. The JD's focus on retrieval over messy internal documents is close to what I spent the last two years on.
>
> At a bank with 30 million customers I replaced a rules-based document triage with a RAG pipeline over scanned contracts. The hard part wasn't the model; it was chunking documents that had been photocopied five times. We settled on layout-aware chunking plus a reranker, and review time dropped by about 40%. I'd have liked to fine-tune the embedder, but the data-residency rules made a hosted model the only option, so we worked around it with query rewriting.
>
> I'm in São Paulo (UTC-3) and have worked US-Eastern hours for North American clients since 2022. Happy to walk through the pipeline on a call.
>
> <first name>

Every detail in this example (the bank, the photocopied contracts, the reranker, the residency rule, the city) is invented to show the shape. Take the applicant's facts only from the resume and the tailored summary. Write a different letter every time: the story, the specific hook and the trade-off sentence change with the JD.
