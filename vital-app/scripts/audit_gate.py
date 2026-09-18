#!/usr/bin/env python3
"""Decide whether a pip-audit result should fail the build.

WHY THIS EXISTS RATHER THAN JUST RUNNING pip-audit
--------------------------------------------------
ci.yml already states two rules that a plain scanner cannot satisfy at once:

  "An advisory check that is always failing carries exactly as much
   information as no check."

  "A CI job that is sometimes red for reasons nobody controls gets ignored,
   and then it protects nothing."

`pip-audit` on its own violates the second. Vulnerabilities are published on
somebody else's schedule, into transitive dependencies nobody here chose, and
often with no released fix for days. Wiring its exit code straight to the
build means an unrelated pull request goes red overnight for something its
author cannot act on — and the documented end state of that is a job everyone
learns to ignore, or a `continue-on-error: true` that makes it decorative.
This repository has already been through that once with ruff.

THE RULE
--------
Fail on what is actionable. Report what is not.

    fix available      -> FAIL. There is a version to move to, today.
    no fix available   -> report, exit 0. Knowing is the whole available
                          action; blocking adds nothing but noise.
    suppressed         -> report, exit 0, and only with a written reason.

"Has an upstream fix" is a much better proxy for "somebody can do something
about this" than severity is. Severity in OSV is patchy, frequently absent,
and describes the worst case for any consumer rather than for this app.

A SUPPRESSION NEEDS A REASON
----------------------------
audit-ignore.txt takes `ID  # why`, and the reason is mandatory — a bare ID
is itself an error. Without a suppression path, the first fixable-but-not-
upgradable finding gets the whole job disabled, which is how these checks
actually die. Requiring the sentence means a future reader can tell an
accepted risk from a forgotten one.

Usage:
    pip-audit ... --format json | python scripts/audit_gate.py
    python scripts/audit_gate.py report.json --ignore-file audit-ignore.txt
"""
import argparse
import json
import sys
from pathlib import Path


def parse_ignore_file(text: str) -> tuple[dict[str, str], list[str]]:
    """Returns ({id: reason}, [complaints]). A reasonless ID is a complaint."""
    ignores, problems = {}, []
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        ident, _, reason = line.partition("#")
        ident, reason = ident.strip(), reason.strip()
        if not ident:
            continue
        if not reason:
            problems.append(
                f"{number}: {ident} is suppressed with no reason given. "
                "Write why after a '#' so the next reader can tell an "
                "accepted risk from a forgotten one.")
            continue
        ignores[ident] = reason
    return ignores, problems


def findings(report: dict) -> list[dict]:
    """Flatten pip-audit's per-dependency JSON into one row per vulnerability.

    Deduplicated on (package, version, id). A real run against jinja2 3.1.2
    returned every advisory twice — "Found 10 known vulnerabilities" for five
    distinct ids — because pip-audit queries more than one vulnerability
    service and does not merge them. Printing each finding twice makes a
    report look twice as alarming as it is, and anything counting rows would
    have been wrong.
    """
    seen, out = set(), []
    for dep in report.get("dependencies", []):
        # pip-audit emits skipped packages in the same list, without `vulns`.
        for vuln in dep.get("vulns", []) or []:
            key = (dep.get("name"), dep.get("version"), vuln.get("id"))
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "package": dep.get("name", "?"),
                "installed": str(dep.get("version", "?")),
                "id": vuln.get("id", "?"),
                "aliases": list(vuln.get("aliases") or []),
                "fix_versions": [str(v) for v in (vuln.get("fix_versions") or [])],
            })
    return out


def classify(rows: list[dict], ignores: dict[str, str]) -> dict:
    blocking, unfixable, suppressed = [], [], []
    for row in rows:
        names = {row["id"], *row["aliases"]}
        hit = next((i for i in names if i in ignores), None)
        if hit:
            suppressed.append({**row, "reason": ignores[hit], "matched": hit})
        elif row["fix_versions"]:
            blocking.append(row)
        else:
            unfixable.append(row)
    return {"blocking": blocking, "unfixable": unfixable,
            "suppressed": suppressed}


def render(groups: dict) -> str:
    lines = []
    if groups["blocking"]:
        lines.append("FIXABLE — these block the build:")
        for r in groups["blocking"]:
            lines.append(f"  {r['package']} {r['installed']}  {r['id']}  "
                         f"-> upgrade to {', '.join(r['fix_versions'])}")
    if groups["unfixable"]:
        lines.append("")
        lines.append("NO FIX PUBLISHED YET — reported, not blocking:")
        for r in groups["unfixable"]:
            lines.append(f"  {r['package']} {r['installed']}  {r['id']}")
    if groups["suppressed"]:
        lines.append("")
        lines.append("SUPPRESSED in audit-ignore.txt:")
        for r in groups["suppressed"]:
            lines.append(f"  {r['package']} {r['installed']}  {r['id']}  "
                         f"— {r['reason']}")
    if not any(groups.values()):
        lines.append("No known vulnerabilities in the locked dependencies.")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("report", nargs="?", default="-",
                    help="pip-audit --format json output, or - for stdin")
    ap.add_argument("--ignore-file", default=None)
    ap.add_argument(
        "--min-packages", type=int, default=1,
        help="fail if the report covers fewer packages than this. A scan of "
             "nothing exits 0 and looks exactly like a clean scan.")
    args = ap.parse_args(argv)

    if args.report == "-":
        text = sys.stdin.read()
    elif not Path(args.report).exists():
        # CI runs pip-audit with `|| true`, because pip-audit exits 1 on any
        # finding and the decision about which findings matter belongs here.
        # That also swallows a genuine crash, after which the report file
        # simply does not exist. Absent must not be mistaken for clean.
        print(f"{args.report} does not exist — the scanner did not produce a "
              "report, so nothing has been checked", file=sys.stderr)
        return 2
    else:
        text = Path(args.report).read_text()
    try:
        report = json.loads(text)
    except json.JSONDecodeError as exc:
        # A scanner that fell over must not read as a clean bill of health.
        # This is the failure mode that makes a security check worse than
        # none: silence that looks identical to success.
        print(f"could not parse the audit report: {exc}", file=sys.stderr)
        print(text[:2000], file=sys.stderr)
        return 2

    ignores, problems = ({}, [])
    if args.ignore_file and Path(args.ignore_file).exists():
        ignores, problems = parse_ignore_file(
            Path(args.ignore_file).read_text())

    # A scan of zero packages is valid JSON, exits 0, and renders as "no
    # known vulnerabilities" — indistinguishable from a real clean scan.
    # This was not hypothetical: the first end-to-end run here fed pip-audit
    # an empty requirements file (a helper had crashed) and the gate reported
    # the project clean. In CI that shape of mistake is a green build under a
    # SECURITY.md paragraph claiming dependencies are scanned, which is worse
    # than the paragraph that admitted they were not.
    scanned = len(report.get("dependencies", []))
    if scanned < args.min_packages:
        print(f"the audit covered {scanned} packages, expected at least "
              f"{args.min_packages} — the dependency list reaching the "
              "scanner is empty or truncated, so this result means nothing",
              file=sys.stderr)
        return 2

    groups = classify(findings(report), ignores)
    print(f"{scanned} packages scanned.")
    print(render(groups))

    if problems:
        print("\naudit-ignore.txt is malformed:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 2
    return 1 if groups["blocking"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
