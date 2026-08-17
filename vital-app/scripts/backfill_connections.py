"""Create connections for matches that were accepted before connections existed.

WHY THIS IS NEEDED
------------------
An accepted match used to be a row in activity_requests pointing at a post.
The connections table now holds the durable record, and decide_request writes
one on every acceptance from here on. Anyone who matched BEFORE that shipped
has an accepted request and no connection — so their relationship is still
hostage to a post either side can edit or delete.

WHAT IT CANNOT RECOVER
----------------------
The snapshot is taken from the post as it stands TODAY, not as it stood when
the request was accepted. If the owner has since renamed themselves or edited
the activity, the connection records the current wording. That is a real loss
and there is no fixing it: the old values were never stored anywhere.

If the post is already gone, the acceptance is unrecoverable and is reported
as skipped rather than guessed at. Inventing a plausible activity to fill a
row would be worse than an honest gap.

    uv run python scripts/backfill_connections.py            # dry run
    uv run python scripts/backfill_connections.py --apply
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from vital import buddies, storage  # noqa: E402


def _accepted_rows(c) -> list[dict]:
    """Accepted requests that still have their post. The LEFT JOIN is
    deliberate — rows whose post is gone must be COUNTED as skipped, not
    filtered out silently, or the report would claim a clean run over data
    it never looked at."""
    return [dict(r) for r in c.execute(
        """SELECT q.id, q.post_id, q.requester_user_id, q.requester_name,
                  p.user_id AS owner_id, p.display_name AS owner_name,
                  p.activity, p.city
           FROM activity_requests q
           LEFT JOIN activity_posts p ON p.id = q.post_id
           WHERE q.status = 'accepted'
           ORDER BY q.id""").fetchall()]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="actually write; without it this is a dry run")
    args = parser.parse_args()

    with storage._conn() as c:
        rows = _accepted_rows(c)
        existing = {(r["user_a"], r["user_b"], r["activity"]) for r in
                    c.execute("SELECT user_a, user_b, activity "
                              "FROM connections").fetchall()}

    created = skipped = already = 0
    for row in rows:
        if row["owner_id"] is None or not row["activity"]:
            skipped += 1
            print(f"  skip  request {row['id']}: post {row['post_id']} is gone "
                  "— nothing left to snapshot")
            continue
        pair = buddies._pair(row["owner_id"], row["requester_user_id"])
        if (*pair, row["activity"]) in existing:
            already += 1
            continue

        print(f"  link  {row['owner_name']} + {row['requester_name']} "
              f"({row['activity']})")
        existing.add((*pair, row["activity"]))
        created += 1
        if not args.apply:
            continue
        with storage._conn() as c:
            buddies._record_connection(
                c, owner_id=row["owner_id"], owner_name=row["owner_name"],
                other_id=row["requester_user_id"],
                other_name=row["requester_name"], activity=row["activity"],
                city=row["city"], post_id=row["post_id"])

    verb = "Created" if args.apply else "Would create"
    print(f"\n{len(rows)} accepted request(s): {verb} {created} connection(s), "
          f"{already} already linked, {skipped} unrecoverable.")
    if not args.apply:
        print("Dry run — nothing changed. Re-run with --apply to commit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
