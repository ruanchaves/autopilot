# autopilot

Claude Code skills for the job hunt. Each folder is one skill; `~/.claude/skills/<name>` is a
directory junction pointing here, so `/job-source-sweep`, `/job-autopilot` and `/recruiter-outreach`
load from this repo.

- `job-source-sweep/` sweeps ~80 job sources, enriches the roles (JD, eligibility, resume fit) and
  publishes the role explorer. The other two skills import its scripts as a sibling folder.
- `job-autopilot/` scores the roles, tailors the resume, fills application forms up to the Submit
  button (never submits), and registers confirmed applications in applika.
- `recruiter-outreach/` builds a scored database of LinkedIn recruiters and contacts the best ones
  within the daily limits.

Per-user data (sweeps, caches, ledgers, answer bank, recruiter leads) lives outside the repo in
`~/jobsweep` (`C:\Users\<you>\jobsweep`). The `tailor-resume` skill it calls stays in `~/.claude/skills`.

Run scripts through WSL, e.g.
`wsl.exe -e python3 /mnt/d/dev/autopilot/job-autopilot/scripts/autopilot.py --home /mnt/c/Users/<you>/jobsweep status`.
