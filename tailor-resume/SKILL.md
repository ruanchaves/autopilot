---
name: tailor-resume
description: Tailor the user's RenderCV YAML resume to a job description (JD) and render the tailored CV to PDF. Edits ONLY the Summary and Skills sections - rewrites the summary (one paragraph, max 4 rendered lines) around the JD's keywords and the user's fit (asking the user to approve it), rebuilds Skills to at most 20 skills in at most 5 groups, and asks before adding any JD skill the resume doesn't already show. The base resume, the output folder and the file prefix come from the repo's config.yaml (resume section). Use when the user gives a JD (text, file, or URL) and asks to tailor, adapt, customize, or target their resume/CV for it; job-autopilot calls it in automatic mode for every queued role.
---

# Tailor resume to a JD

Produce a JD-specific copy of the RenderCV resume, changing only `cv.sections.summary` and `cv.sections.skills`, then render it.

## Where things are

- **Skill:** `<repo>/tailor-resume` (`<tr>`), where `<repo>` is the autopilot repo
- **Scripts:** `<tr>/scripts/check_tailored.py` (diff guard and limits) and `<tr>/scripts/summary_lines.py` (rendered line count). Both run through [uv](https://docs.astral.sh/uv/)'s `uvx` with the `rendercv[full]` bundle, inside WSL when `python` in `config.yaml` is `wsl`, so they need no install of their own: the first run downloads RenderCV.
- **Settings** come from `<repo>/config.yaml` (`jobsweep config` prints the merged result), section `resume`:

| Key | Use |
|---|---|
| `resume.base` | the RenderCV YAML to tailor; never modified |
| `resume.tailored_dir` | one folder per tailored copy goes here (default: `tailored/` next to the base) |
| `resume.file_prefix` | tailored files are `<prefix>-<company>-<role>.yaml` (default: the base file's stem) |

`candidate.stories` in the same file lists the proof points the resume supports; the summary picks from them.

## Inputs

- **Base resume**: `resume.base` unless the user names another RenderCV YAML. Never modify the base file. If it is not a RenderCV YAML (Markdown, text or PDF), say that this skill only tailors RenderCV YAML and stop.
- **JD**: pasted text, a local file (read it), or a URL (fetch it with WebFetch; if the page needs a login or comes back empty, ask the user to paste the text). If no JD was provided, ask for one.

## Hard rules

1. **Only two sections change**: `cv.sections.summary` and `cv.sections.skills`. The name, headline, contact info, achievements, experience, languages, projects, education, and `design` stay byte-for-byte identical. Section order stays the same.
2. **No invented facts.** Every claim in the summary (years, numbers, employers, domains, tools) must already appear somewhere in the base resume, or be a skill the user approved in step 3. Don't inflate metrics or seniority.
3. **Summary limit**: the summary is a single paragraph, one YAML string with no line breaks (`\n`) and no second list entry. It must fit in at most **4 rendered lines** in the PDF. Step 7 checks this.
4. **Skills limits**: at most **5 groups** and at most **20 skills** in total. Each group is a `label` / `details` entry with comma-separated skills.
5. **Missing JD skills need explicit approval.** A skill counts as *present* if it appears anywhere in the base resume: skills, experience highlights, projects, or education. A tool named only in one experience bullet is still present. Anything else is *missing*, and goes into the tailored resume only if the user says yes.

## Workflow

### 1. Analyze the JD

Extract:
- role title, seniority, company, and domain
- **required** skills and tools, and **preferred / nice-to-have** ones
- recurring keywords and phrasing, such as "agentic workflows", "LLMOps", "production ML", or "evaluation"

### 2. Map the JD against the resume

Build a table for yourself: JD skill → present (and where) / synonym present / missing.
- Treat clear synonyms as present, and prefer the **JD's wording** in the skills list when it is truly equivalent. For example, "GCP" and "Google Cloud" are equivalent, so use whichever the JD uses. "OpenAI API" is not the same as "Azure OpenAI" unless the resume supports it.
- Ignore soft skills and generic items, such as "communication" or "problem solving", for the skills list. They can shape the summary's wording instead.

### 3. Ask about missing skills (automatic mode: see the last section)

If any JD skills are missing, ask with `AskUserQuestion` before drafting anything:
- Use `multiSelect: true`, one option per missing skill, and note in each description whether the JD lists it as required or preferred.
- Each question takes at most 4 options and a call takes at most 4 questions. Group the skills sensibly, such as "Cloud / infra" or "ML frameworks", and ask most-important-first. If there are more than 16, make a second call.
- Unselected skills are excluded. Don't mention them in the summary or skills.

If nothing is missing, say so briefly and continue.

### 4. Build the Skills section

- Start from the JD's required skills that are present or approved, then preferred ones, then the resume's strongest remaining skills that still fit the role.
- Drop base skills that are irrelevant to this JD, so the section stays within 20 skills and 5 groups.
- Group into ≤5 coherent groups with short labels. The base labels can be reused or renamed to mirror the JD, such as "LLM & Agents", "MLOps", or "Data".
- Order the groups, and the skills within each group, by relevance to the JD.

### 5. Draft the summary and get approval

- Keep it to a single string entry with no line breaks that renders in **at most 4 lines**. With RenderCV's default themes 4 lines hold about 75 words (about 470 characters), so aim for **≤ 470 characters**; if the base summary already takes 4 lines, stay at or under its length.
- Open with the target role title as the JD phrases it, when that is accurate. Weave in the JD's top keywords naturally rather than as a list. Pick the 2–3 proof points from the resume that best match the JD's priorities (`candidate.stories` in `config.yaml` lists the ones the resume supports). Close with a sentence on fit with this role or company.
- Show the draft summary as text, along with the proposed Skills grouping for context. Then call `AskUserQuestion` with the options "Approve summary" and "Request changes". The user can also type edits through "Other".
- Revise and ask again until the user approves. If they comment on the skills at this point, apply those comments too while keeping rule 3.

### 6. Write the tailored YAML

- Output folder: `<resume.tailored_dir>/<company>-<role>/` (lowercase, hyphenated, e.g. `acme-senior-ai-engineer`).
- Copy the base YAML there as `<resume.file_prefix>-<company>-<role>.yaml`. Then use Edit to replace **only** the summary string and the `skills:` block, so the rest of the file stays exactly as it was. YAML-quote any string that contains `:`, `#`, or starts with a special character.
- Verify with the checker. With `python: wsl` (paths in `/mnt/...` form; from Git Bash, first `export MSYS_NO_PATHCONV=1`):

  ```
  wsl.exe -e bash -lc 'uvx --from "rendercv[full]" python "/mnt/<repo>/tailor-resume/scripts/check_tailored.py" "<base yaml>" "<tailored yaml>"'
  ```

  With a native runner, drop the `wsl.exe -e bash -lc '...'` wrapper and use local paths. Fix the file and re-run until it prints `OK`.

### 7. Render

```
wsl.exe -e bash -lc 'cd "<tailored folder>" && uvx --from "rendercv[full]" rendercv render "<file>.yaml"'
```

Output lands in `rendercv_output/` next to the YAML. Check the summary's rendered line count:

```
wsl.exe -e bash -lc 'uvx --from "rendercv[full]" --with pymupdf python "/mnt/<repo>/tailor-resume/scripts/summary_lines.py" "<tailored folder>/rendercv_output/<name>_CV.pdf" 4'
```

If it prints `FAILED`, the summary is over 4 lines. Shorten it while keeping the approved wording as much as possible, re-render, and check again. If the trim changes the meaning, not just the length, show the user the shortened summary and ask them to approve it again.

Then read the page-1 PNG to confirm that the summary and skills render cleanly: no stray markdown and no overflow. Also check that the page count hasn't grown past the base CV's; if it has, tighten the summary or skills and render again.

### 8. Report

Tell the user:
- the paths to the tailored YAML and PDF
- the final summary
- the skills grouping, and which skills were added (with their approval) or dropped
- any important JD requirements the resume still doesn't cover

## Automatic mode (called by job-autopilot)

The `job-autopilot` skill runs this skill without a person in the loop. Its `tailor-queue` command writes one **packet** file per job (`tailor_job_<id>.json`), and your prompt names it. Read it once; it holds `job` (`id`, `title`, `company`, `url`, `jd_file`, `base_resume`, `folder`, `file_name`, `result`; the last three already apply `resume.tailored_dir` and `resume.file_prefix`), the answer bank's `confirmed_skills` and `denied_skills`, and `previous_summaries` (the last few tailored summaries, inline, with their company and title). In this mode, the workflow above changes as follows. Everything else, including every hard rule, still applies.

**Token discipline.** Read the base YAML and the JD once each. Don't open other tailored folders. Pipe the renderer's output through `tail -5` (its download and progress lines are the largest thing in a tailoring session) and look at the page-1 PNG only when a check fails.

- **JD.** Read `jd_file`. If it is empty or shorter than about 400 characters, fetch `url` with WebFetch and use the page's job description. If that also fails, stop and write a result with `"status": "no_jd"`.
- **Step 3, missing skills.** Don't ask. A missing JD skill goes into the resume only if it is in `confirmed_skills`. Skills in `denied_skills` are left out. Every other missing skill that the JD lists as required or preferred is left out and reported in `skills_pending`, so the user can answer it once on the dashboard. Never mention a pending or denied skill in the summary.
- **Step 5, summary.** Don't ask for approval. Write the summary for *this* JD. Before moving on, review it critically against the JD, as a recruiter for this role would read it:
  - Could it be pasted onto a different job in the same field unchanged? If so, it is generic. Rewrite it around what this JD actually asks for: its main responsibilities, its named tools and methods, its domain, and the company.
  - Do the proof points you chose answer this JD's top requirements, or are they just the resume's usual highlights? Pick the ones that answer the JD.
  - Compare it with `previous_summaries` in the packet. If yours reads like a light rewording of one of them, rewrite it.

  Keep revising until the answer to all three is clearly no. Hard rules 2 and 3 still apply: no invented facts, and at most 4 rendered lines.
- **Steps 6 and 7.** Unchanged: `check_tailored.py`, render (append `2>&1 | tail -5` to the render command), the 4-line check, and the page count. Look at the page-1 PNG only when a check fails. If a check fails 3 times, write a result with `"status": "failed"` and the check output as `detail`.
- **Step 8, report.** Instead of a message to the user, write the `result` file as JSON:

  ```json
  {"kind": "tailor", "id": "<id>", "status": "ok", "yaml": "<host path>", "pdf": "<host path to rendercv_output/..._CV.pdf>",
   "jd_file": "<path of the JD text actually used>", "summary": "<final summary>", "keywords": ["..."],
   "skills_added": ["confirmed skills used"], "skills_pending": ["missing JD skills not yet confirmed or denied"], "detail": ""}
  ```

  If you fetched the JD yourself, save its text next to the YAML as `jd.txt` and point `jd_file` at it. Then **record the result yourself** with the command the prompt gives (`<autopilot_cmd> record --result <result>`; with `python: wsl` that is `wsl.exe -e python3 /mnt/<repo>/job-autopilot/scripts/autopilot.py record --result <result>`). Reply with one line only: `tailored <id> <company>: ok|no_jd|failed, <n> pending skills, recorded`.
