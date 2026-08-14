"""The moderation queue. Reports go here to be decided by a person.

    uv run python scripts/review_reports.py                 # what's waiting
    uv run python scripts/review_reports.py --hide 12       # take it down
    uv run python scripts/review_reports.py --restore 12    # false alarm
    uv run python scripts/review_reports.py --dismiss 12    # no action needed

Before this existed, report_post inserted a row and returned. Nothing read
the table, so a report had no effect at all: the reporter was thanked, and
no human was ever told. On a product that introduces strangers to each
other, that is the most serious thing that can be wrong.

Three reports from DISTINCT people now hide a post automatically, so the
worst case is bounded without anyone being awake. This is where the rest
of the decisions get made — including undoing an automatic hide, because a
coordinated false report is a real thing and the reversal has to be as easy
as the hide.

Deliberately a script, not a web console. An admin UI is a new
authenticated surface with its own vulnerabilities, protecting a queue that
is currently measured in single digits. A script run by whoever has gcloud
access is the proportionate answer, and the honest one to point at in a
review.
"""
import argparse
import sys

sys.path.insert(0, "src")

from vital import storage                                  # noqa: E402


def show() -> int:
    reports = storage.open_reports()
    if not reports:
        print("\nNothing waiting. (Reports resolved earlier stay resolved.)")
        return 0

    print(f"\n{len(reports)} post(s) awaiting review\n")
    for row in reports:
        flag = "HIDDEN" if row.get("hidden") else "visible"
        print(f"  post {row['post_id']}  [{flag}]  "
              f"{row['reporters']} reporter(s)  last {row['latest'][:16]}")
        print(f"    {row['display_name']} — {row['activity']} in {row['city']}")
        if row.get("notes"):
            print(f"    notes: {row['notes'][:100]}")
        for reason in storage.report_reasons(row["post_id"])[:5]:
            print(f"    reason: {reason[:100]}")
        print()

    print("Decide with:  --hide ID  |  --restore ID  |  --dismiss ID")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hide", type=int, metavar="POST_ID",
                        help="take the post down and close its reports")
    parser.add_argument("--restore", type=int, metavar="POST_ID",
                        help="put an auto-hidden post back and close its reports")
    parser.add_argument("--dismiss", type=int, metavar="POST_ID",
                        help="close the reports, leave the post as it is")
    args = parser.parse_args()

    if args.hide:
        storage.hide_post(args.hide)
        closed = storage.resolve_reports(args.hide)
        print(f"post {args.hide} hidden; {closed} report(s) closed")
        return 0

    if args.restore:
        storage.unhide_post(args.restore)
        closed = storage.resolve_reports(args.restore)
        print(f"post {args.restore} restored; {closed} report(s) closed")
        return 0

    if args.dismiss:
        closed = storage.resolve_reports(args.dismiss)
        print(f"{closed} report(s) on post {args.dismiss} closed, post unchanged")
        return 0

    return show()


if __name__ == "__main__":
    raise SystemExit(main())
