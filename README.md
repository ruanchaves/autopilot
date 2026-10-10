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

## Make it yours: `config.yaml`

Everything personal is in **`config.yaml`** at the repo root, and nothing personal is hardcoded in the
skills: who the candidate is (contact details, country and region, time zone, years, pitch, stories,
employment types, contract types to avoid), the resume paths, the target role and pay, the current
employers never to apply to, the data folder (`home`), the Python runner, and each skill's tunables
(`sweep`, `autopilot`, `recruiter_outreach`, `tracker`). Fork the repo, edit the file (every key is
commented), and run `python3 job-source-sweep/scripts/jobsweep.py config` to see the merged result.
`job-source-sweep/scripts/userconfig.py` is the loader the three skills share; PyYAML is required.

Per-user data (sweeps, caches, ledgers, answer bank, recruiter leads) lives outside the repo in the
`home` folder from `config.yaml`. The only file written there as state is `config.json` with the
explorer artifact URL. The `tailor-resume` skill the autopilot calls stays in `~/.claude/skills`.

Run scripts through the runner in `config.yaml`, e.g. with `python: wsl`:
`wsl.exe -e python3 /mnt/d/dev/autopilot/job-autopilot/scripts/autopilot.py status` (`--home` defaults to
the configured folder).
