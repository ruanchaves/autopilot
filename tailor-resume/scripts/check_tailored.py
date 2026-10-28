"""Verify a tailored RenderCV YAML against its base resume.

Usage: python check_tailored.py BASE.yaml TAILORED.yaml

Fails (exit 1) if anything other than cv.sections.summary and
cv.sections.skills differs, or if skills exceed 5 groups / 20 items.
"""
import sys

from ruamel.yaml import YAML

MAX_GROUPS = 5
MAX_SKILLS = 20
EDITABLE = ("summary", "skills")


def load(path):
    with open(path, encoding="utf-8") as f:
        return YAML(typ="safe").load(f)


def strip_editable(doc):
    doc = dict(doc)
    cv = dict(doc.get("cv", {}))
    sections = dict(cv.get("sections", {}))
    for key in EDITABLE:
        sections.pop(key, None)
    cv["sections"] = sections
    doc["cv"] = cv
    return doc


def main(base_path, tailored_path):
    base, tailored = load(base_path), load(tailored_path)
    errors = []

    if strip_editable(base) != strip_editable(tailored):
        errors.append("Content outside cv.sections.summary / cv.sections.skills was changed.")

    sections = tailored.get("cv", {}).get("sections", {})
    if list(sections) != list(base.get("cv", {}).get("sections", {})):
        errors.append("Section order or set of sections changed.")

    groups = sections.get("skills") or []
    if len(groups) > MAX_GROUPS:
        errors.append(f"Skills has {len(groups)} groups (max {MAX_GROUPS}).")
    total = 0
    for group in groups:
        items = [s.strip() for s in str(group.get("details", "")).split(",") if s.strip()]
        total += len(items)
        print(f"  {group.get('label')}: {len(items)} -> {', '.join(items)}")
    if total > MAX_SKILLS:
        errors.append(f"Skills has {total} items (max {MAX_SKILLS}).")

    summary = sections.get("summary") or []
    if not summary:
        errors.append("Summary section is empty.")
    elif len(summary) != 1 or not isinstance(summary[0], str):
        errors.append("Summary must be exactly one string entry (a single paragraph).")
    elif "\n" in summary[0].strip():
        errors.append("Summary must not contain line breaks.")
    else:
        print(f"Summary: {len(summary[0])} chars (aim for <= 470 to fit 4 lines)")

    print(f"Groups: {len(groups)}/{MAX_GROUPS}, skills: {total}/{MAX_SKILLS}")
    for e in errors:
        print("ERROR:", e)
    print("OK" if not errors else "FAILED")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
