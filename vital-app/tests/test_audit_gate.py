"""The thing that decides whether the security check is trustworthy.

audit_gate.py is the component that turns a scanner's output into a red or
green build. If it is wrong in the permissive direction it reports "no known
vulnerabilities" over a real finding, which is worse than having no scanner
at all — a scanner nobody ran is a known gap, and a scanner that silently
passes is a false assurance written into SECURITY.md.

So it is tested against crafted payloads rather than against whatever
pip-audit happens to return today, which would be a test of the internet.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

GATE = Path(__file__).resolve().parents[1] / "scripts" / "audit_gate.py"
sys.path.insert(0, str(GATE.parent))

from audit_gate import classify, findings, parse_ignore_file, render  # noqa: E402


def _report(*deps):
    return {"dependencies": list(deps)}


def _dep(name, version, *vulns):
    return {"name": name, "version": version, "vulns": list(vulns)}


def _vuln(ident, fixes=(), aliases=()):
    return {"id": ident, "fix_versions": list(fixes), "aliases": list(aliases)}


def _run(report, ignore_file=None):
    """The script as CI runs it: JSON on stdin, exit code out."""
    cmd = [sys.executable, str(GATE)]
    if ignore_file:
        cmd += ["--ignore-file", str(ignore_file)]
    return subprocess.run(cmd, input=json.dumps(report), capture_output=True,
                          text=True)


# ---------- the rule ----------

def test_a_vulnerability_with_a_fix_fails_the_build():
    out = _run(_report(_dep("jinja2", "3.1.2",
                            _vuln("GHSA-h5c8-rqwp-cp95", ["3.1.3"]))))
    assert out.returncode == 1
    assert "3.1.3" in out.stdout, "did not say what to upgrade to"


def test_a_vulnerability_with_no_fix_is_reported_but_does_not_block():
    """THE decision. Nobody can act on an unfixed CVE in a transitive
    dependency, and a build that goes red for it teaches people to stop
    reading the build."""
    out = _run(_report(_dep("somelib", "1.0", _vuln("PYSEC-2026-1"))))
    assert out.returncode == 0
    assert "PYSEC-2026-1" in out.stdout
    assert "NO FIX PUBLISHED" in out.stdout


def test_a_mixed_report_blocks_on_the_fixable_one_and_still_shows_both():
    out = _run(_report(
        _dep("safe-ish", "1.0", _vuln("PYSEC-2026-1")),
        _dep("fixable", "2.0", _vuln("PYSEC-2026-2", ["2.1"]))))
    assert out.returncode == 1
    assert "PYSEC-2026-1" in out.stdout and "PYSEC-2026-2" in out.stdout


def test_a_clean_report_passes_and_says_so():
    out = _run(_report(_dep("fastapi", "0.115.0")))
    assert out.returncode == 0
    assert "No known vulnerabilities" in out.stdout


def test_packages_pip_audit_skipped_do_not_crash_the_gate():
    """Skipped entries appear in the same list with no `vulns` key at all."""
    assert findings({"dependencies": [{"name": "weird", "version": "1.0"},
                                      {"name": "n", "version": "2", "vulns": None}]}) == []


# ---------- the failure mode that would make this worse than nothing ----------

def test_unparseable_scanner_output_is_an_error_not_a_pass():
    """If pip-audit dies — rate limit, network, a changed flag — its output
    is not JSON. Treating that as 'no findings' is how a security check
    becomes a false assurance. Exit 2: neither clean nor a finding.

    Asserts on WHICH guard fired, not just the exit code. Mutation testing
    caught that: replacing `return 2` here with `report = {}` left this test
    green, because an empty report then trips the min-packages floor and
    exits 2 for an entirely different reason. The behaviour was right by
    accident and the test was pinning nothing — set --min-packages 0 and the
    parse guard would have been quietly dead.
    """
    out = subprocess.run([sys.executable, str(GATE), "--min-packages", "0"],
                         input="ERROR: connection refused",
                         capture_output=True, text=True)
    assert out.returncode == 2
    assert "could not parse" in out.stderr, (
        "exited 2 for some other reason — this test is not covering the "
        "JSON-decode guard it names")
    assert "No known vulnerabilities" not in out.stdout


def test_an_empty_report_is_an_error_not_a_pass():
    out = subprocess.run([sys.executable, str(GATE)], input="",
                         capture_output=True, text=True)
    assert out.returncode == 2


def test_a_scan_of_zero_packages_is_an_error_not_a_clean_bill_of_health():
    """FOUND BY RUNNING IT FOR REAL, not by writing this test first.

    The first end-to-end run fed pip-audit an empty requirements file,
    because the helper generating it had crashed. pip-audit scanned nothing,
    exited 0, emitted valid JSON, and the gate printed "No known
    vulnerabilities in the locked dependencies."

    Every test above still passed. The shape of that mistake in CI is a green
    build underneath a SECURITY.md paragraph claiming dependencies are
    scanned — strictly worse than the paragraph that admitted they were not,
    because it converts a known gap into a false assurance.
    """
    out = _run(_report())
    assert out.returncode == 2
    assert "No known vulnerabilities" not in out.stdout


def test_a_missing_report_file_is_an_error_not_a_pass():
    """CI runs pip-audit with `|| true` so that the gate decides, not the
    scanner's own exit code. That also hides a crash, after which there is no
    file at all. The third way to accidentally pass."""
    out = subprocess.run(
        [sys.executable, str(GATE), "/tmp/definitely-not-here.json"],
        capture_output=True, text=True)
    assert out.returncode == 2
    assert "No known vulnerabilities" not in out.stdout


def test_a_truncated_dependency_list_is_caught_by_the_floor():
    """Zero is the catastrophic case; a handful is the quieter one — an
    export that silently dropped a section still 'passes' without this."""
    out = subprocess.run(
        [sys.executable, str(GATE), "--min-packages", "50"],
        input=json.dumps(_report(_dep("only-one", "1.0"))),
        capture_output=True, text=True)
    assert out.returncode == 2
    assert "truncated" in out.stderr


def test_the_package_count_is_reported_so_a_reader_can_sanity_check_it():
    out = _run(_report(_dep("a", "1"), _dep("b", "2")))
    assert "2 packages scanned" in out.stdout


def test_duplicate_advisories_from_two_services_are_reported_once():
    """A real run against jinja2 3.1.2 said "Found 10 known vulnerabilities"
    for five distinct ids: pip-audit queries more than one vulnerability
    service and does not merge them."""
    doubled = {"dependencies": [
        _dep("jinja2", "3.1.2", _vuln("PYSEC-2026-1473", ["3.1.3"])),
        _dep("jinja2", "3.1.2", _vuln("PYSEC-2026-1473", ["3.1.3"])),
    ]}
    assert len(findings(doubled)) == 1


# ---------- suppressions have to justify themselves ----------

def test_a_suppressed_finding_does_not_block():
    ig = Path("/tmp/ig-ok.txt")
    ig.write_text("PYSEC-2026-2  # no upgrade path until we drop py3.11\n")
    out = _run(_report(_dep("fixable", "2.0", _vuln("PYSEC-2026-2", ["2.1"]))),
               ignore_file=ig)
    assert out.returncode == 0
    assert "SUPPRESSED" in out.stdout
    assert "drop py3.11" in out.stdout, "the reason must survive to the log"


def test_a_suppression_without_a_reason_is_itself_an_error():
    """Without this the file silently becomes a list of things somebody once
    wanted to stop thinking about."""
    ig = Path("/tmp/ig-bad.txt")
    ig.write_text("PYSEC-2026-2\n")
    out = _run(_report(_dep("fixable", "2.0", _vuln("PYSEC-2026-2", ["2.1"]))),
               ignore_file=ig)
    assert out.returncode == 2
    assert "no reason given" in out.stderr


def test_comments_and_blank_lines_are_not_suppressions():
    ignores, problems = parse_ignore_file(
        "# this whole line is a comment\n\n   \nPYSEC-1 # real one\n")
    assert ignores == {"PYSEC-1": "real one"}
    assert problems == []


def test_a_suppression_can_be_written_as_the_cve_alias():
    """People copy the CVE out of an advisory, not the PYSEC id. Matching
    only the primary id would make the file look broken for no reason."""
    rows = findings(_report(_dep("x", "1.0",
                                _vuln("PYSEC-2026-9", ["1.1"],
                                      aliases=["CVE-2026-0001"]))))
    groups = classify(rows, {"CVE-2026-0001": "upstream fix breaks our API"})
    assert groups["blocking"] == []
    assert groups["suppressed"][0]["matched"] == "CVE-2026-0001"


def test_a_suppression_for_an_unrelated_id_does_not_mask_a_real_finding():
    """The mutation that matters: an over-broad match here turns the whole
    gate off while still printing a reassuring report."""
    rows = findings(_report(_dep("x", "1.0", _vuln("PYSEC-2026-9", ["1.1"]))))
    groups = classify(rows, {"PYSEC-2026-1": "different thing entirely"})
    assert len(groups["blocking"]) == 1


# ---------- the report has to be readable by whoever sees it fail ----------

@pytest.mark.parametrize("group,expected", [
    ("blocking", "FIXABLE"),
    ("unfixable", "NO FIX PUBLISHED"),
    ("suppressed", "SUPPRESSED"),
])
def test_each_group_is_labelled_in_plain_language(group, expected):
    row = {"package": "p", "installed": "1", "id": "X", "aliases": [],
           "fix_versions": ["2"], "reason": "r", "matched": "X"}
    empty = {"blocking": [], "unfixable": [], "suppressed": []}
    assert expected in render({**empty, group: [row]})
