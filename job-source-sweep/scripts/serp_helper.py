#!/usr/bin/env python3
"""One search through ddgs (https://github.com/deedy5/ddgs, MIT), run by companies.Searcher.

It runs in the search virtualenv (config.yaml search.python, default ~/.venvs/search/bin/python, ddgs pinned at
DDGS_VERSION in companies.py), so the rest of the scripts stay on the standard library.

    serp_helper.py '{"query": "...", "backend": "google", "max_results": 10, "page": 1, "timeout": 10, "timelimit": "w"}'

timelimit (optional): d / w / m / y, the engine's own "past day/week/month/year" filter (ddgs passes it through).

Engines (tested 2026-10-27 from WSL with ddgs 9.16.1 on `site:jobs.lever.co "AI engineer" remote`): duckduckgo 6
hits, fails with a timelimit; yandex 6 hits (ddgs ships it `disabled`, re-enabled here), one page, timelimit
unconfirmed; yahoo 8 hits (Bing's index, with ads), broken with a timelimit; google and bing are shipped disabled and
asking ddgs for them silently runs "auto" (random engines), so both are refused here: re-enabled, google returns
nothing and bing ignores the site: operator; brave, mojeek and startpage return nothing to a script.

Prints one JSON object: {"kind": "ok", "results": [{title, href, body}]} or {"kind": "empty" | "ratelimit" | "timeout"
| "error", "error": "..."}. "empty" is ddgs's "No results found", which is also what a quietly blocked engine returns;
the caller tells the two apart from a canary query and from streaks of empties.
"""

import json
import sys

REENABLE = {"yandex"}  # shipped disabled by ddgs, works for site: queries (2026-10-27)
BROKEN = {"bing": "ignores the site: operator (2026-10-27 test: generic AI results for site:jobs.lever.co)",
          "google": "shipped disabled by ddgs 9.16.1 and returns nothing when re-enabled (2026-10-27 test)"}


def main() -> int:
    args = json.loads(sys.argv[1])
    try:
        from ddgs import DDGS
        from ddgs.exceptions import DDGSException, RatelimitException, TimeoutException
    except ImportError as err:
        print(json.dumps({"kind": "error", "error": f"ddgs not installed in this Python: {err}"}))
        return 0
    be = args.get("backend", "duckduckgo")
    if be in BROKEN:
        print(json.dumps({"kind": "unavailable", "error": f"ddgs engine '{be}' {BROKEN[be]}"}))
        return 0
    # An engine ddgs doesn't register (it ships "bing" and "yandex" with `disabled = True`) would make ddgs silently
    # fall back to "auto": random engines, wikipedia first. Re-enable the ones that work (yandex), refuse the rest,
    # so the caller's circuit breaker sees it.
    try:
        from ddgs.engines import ENGINES
        if be in REENABLE and be not in ENGINES.get("text", {}):
            import importlib
            mod = importlib.import_module(f"ddgs.engines.{be}")
            for cls in list(vars(mod).values()):
                if isinstance(cls, type) and getattr(cls, "disabled", None) is True:
                    cls.disabled = False
                    ENGINES["text"][be] = cls
        if be not in ENGINES.get("text", {}):
            print(json.dumps({"kind": "unavailable", "error": f"ddgs has no text engine '{args.get('backend')}' "
                                                              f"(available: {', '.join(sorted(ENGINES.get('text', {})))})"}))
            return 0
    except ImportError:
        pass
    try:
        res = DDGS(timeout=args.get("timeout", 10)).text(args["query"], backend=be,
                                                         max_results=args.get("max_results", 10), page=args.get("page", 1),
                                                         timelimit=args.get("timelimit") or None)
        out = {"kind": "ok" if res else "empty",
               "results": [{"title": r.get("title", ""), "href": r.get("href", ""), "body": r.get("body", "")} for r in res or []]}
    except RatelimitException as err:
        out = {"kind": "ratelimit", "error": str(err)[:200]}
    except TimeoutException as err:
        out = {"kind": "timeout", "error": str(err)[:200]}
    except DDGSException as err:
        msg = str(err)
        out = {"kind": "empty" if "no results" in msg.lower() else "error", "error": msg[:200]}
    except Exception as err:  # noqa: BLE001 - every failure goes back to the caller as data
        out = {"kind": "error", "error": f"{type(err).__name__}: {err}"[:200]}
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
