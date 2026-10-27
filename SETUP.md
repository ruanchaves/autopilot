# Install the autopilot skills (instructions for Claude Code)

The user cloned this repo and asked you to install its skills. `<repo>` is the folder that holds this file. Do every step below, in order, then start the first-run setup. Ask only when a step says to.

## 1. Link the skills

Make `~/.claude/skills/<name>` point at `<repo>/<name>` for each of `job-source-sweep`, `job-autopilot`, `recruiter-outreach` and `hiring-posts-outreach`. Create `~/.claude/skills` if it doesn't exist.

- Windows (PowerShell): `New-Item -ItemType Junction -Path "$HOME\.claude\skills\<name>" -Target "<repo>\<name>"`
- macOS / Linux: `ln -s "<repo>/<name>" ~/.claude/skills/<name>`

If `~/.claude/skills/<name>` already exists and is a real folder with its own files, stop and ask the user whether to replace it. If it is already a link to this repo, leave it.

## 2. Find Python

Try in order and keep the first that reports Python 3.11 or newer: `python3 --version`, `python --version`, `py -3 --version`, `wsl.exe -e python3 --version`. Then check PyYAML with the same interpreter: `<python> -c "import yaml"`. If it is missing, say so and run `<python> -m pip install pyyaml` (inside WSL when the runner is `wsl`). If no interpreter works, tell the user they need Python 3.11+ with PyYAML, or WSL on Windows, and stop.

Then set up the search virtualenv for the people check, with the same runner (inside WSL when it is `wsl`):

```
python3 -m venv --without-pip ~/.venvs/search && curl -sS https://bootstrap.pypa.io/get-pip.py | ~/.venvs/search/bin/python
~/.venvs/search/bin/python -m pip install ddgs==9.16.0 pyyaml
```

Check it with `jobsweep search 'site:br.linkedin.com/in engineer "Stripe"' --no-cache`. It should print LinkedIn
results. If it can't be installed, the people check falls back to Chrome (`references/people-check.md`).

Set `python:` in `<repo>/config.yaml` to `python3`, `python`, `py -3` or `wsl`. With `wsl`, every path handed to the scripts takes its `/mnt/<drive>/…` form, and from Git Bash `export MSYS_NO_PATHCONV=1` first.

## 3. Choose the data folder

Set `home:` in `config.yaml` to a folder outside the repo for per-user data, by default `~/jobsweep` (on Windows, its `C:/Users/<name>/jobsweep` form). Create it.

## 4. Create config.yaml

`config.yaml` is git-ignored and does not exist in a fresh clone. Copy `<repo>/config.example.yaml` to `<repo>/config.yaml` before steps 2 and 3 edit it. The copy holds placeholder values in `candidate`, `resume`, `role`, `pay` and `banned_companies` and `setup_done: []`, so each skill runs its first-run questions and replaces them. Tell the user in one line that this is what the setup is about to do.

## 5. Verify

Run `<python> <repo>/job-source-sweep/scripts/jobsweep.py config`. It must print JSON with `setup_done: []` and the `python` and `home` you set. Fix anything it complains about before going on.

## 6. Optional tools

Check, without asking, whether these exist, and tell the user in one line each what is missing and what that means:
- `applika whoami` (the application tracker; without it `tracker.enabled` will be set to false by the autopilot's setup, so no tracking and no rejection cooldowns)
- `~/.claude/skills/tailor-resume/SKILL.md` (per-role CV tailoring; without it the autopilot uploads the resume PDF as-is)
- the Claude in Chrome extension, connected and logged in to LinkedIn (needed by the sweep's browser lane, the form filling and recruiter outreach)

## 7. Start the first-run setup now

Don't wait for a restart: read `<repo>/job-source-sweep/SKILL.md`, go to its step 1 (Setup mode) and run it to the end. It asks about the role, the age window, depth, the role explorer, the resume, the country and region, pay and current employers, fills the `candidate` section from the resume, and adds `job-source-sweep` to `setup_done`. Offer to run a first sweep right after.

## 8. What to tell the user at the end

- Restart Claude Code so `/job-source-sweep`, `/job-autopilot` and `/recruiter-outreach` appear in the skill list.
- `/job-autopilot` and `/recruiter-outreach` ask their own few questions the first time they run (submit policy, cover letters, channels and tracker; permission to send, daily and per-company caps, strictness and search phrasings).
- Everything personal lives in `<repo>/config.yaml`; "reconfigure" or an empty `setup_done` reruns the questions. The file is git-ignored, so their details never reach a commit; `config.example.yaml` is the only copy in the repo.
