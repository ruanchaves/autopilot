#!/usr/bin/env python3
"""One search through ddgs (https://github.com/deedy5/ddgs, MIT), run by companies.Searcher.

It runs in the search virtualenv (config.yaml search.python, default ~/.venvs/search/bin/python, ddgs pinned at
DDGS_VERSION in companies.py), so the rest of the scripts stay on the standard library.

    serp_helper.py '{"query": "...", "backend": "bing", "max_results": 10, "page": 1, "timeout": 10}'

Prints one JSON object: {"kind": "ok", "results": [{title, href, body}]} or {"kind": "empty" | "ratelimit" | "timeout"
| "error", "error": "..."}. "empty" is ddgs's "No results found", which is also what a quietly blocked engine returns;
the caller tells the two apart from a canary query and from streaks of empties.
"""

import json
import sys


def main() -> int:
    args = json.loads(sys.argv[1])
    try:
        from ddgs import DDGS
        from ddgs.exceptions import DDGSException, RatelimitException, TimeoutException
    except ImportError as err:
        print(json.dumps({"kind": "error", "error": f"ddgs not installed in this Python: {err}"}))
        return 0
    # An engine ddgs doesn't register (in 9.16.0 "bing" and "yandex" are disabled) would make ddgs silently fall
    # back to "auto": random engines, wikipedia first. Refuse instead, so the caller's circuit breaker sees it.
    try:
        from ddgs.engines import ENGINES
        if args.get("backend", "yahoo") not in ENGINES.get("text", {}):
            print(json.dumps({"kind": "unavailable", "error": f"ddgs has no text engine '{args.get('backend')}' "
                                                              f"(available: {', '.join(sorted(ENGINES.get('text', {})))})"}))
            return 0
    except ImportError:
        pass
    try:
        res = DDGS(timeout=args.get("timeout", 10)).text(args["query"], backend=args.get("backend", "bing"),
                                                         max_results=args.get("max_results", 10), page=args.get("page", 1))
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
