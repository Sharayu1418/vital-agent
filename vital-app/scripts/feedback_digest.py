"""What people actually thought, from data already being collected.

Every thumbs up and down since launch has been written to the `feedback`
table and never read once. That is the Reddit shape again: the signal
exists, nobody looks at it, so a decline is invisible until somebody
happens to notice.

    uv run python scripts/feedback_digest.py            # last 30 days
    uv run python scripts/feedback_digest.py --days 7
    uv run python scripts/feedback_digest.py --threads  # thread ids to read

Prints a rate, a trend, and the comments. The rate on its own says
whether things are getting worse; the comments say why. Thread ids let
you go and read the conversation that earned a thumbs down, which is the
only thing here that reliably suggests a fix.

Hashed identifiers only. This is for finding bad ANSWERS, not for looking
at particular people.
"""
import argparse
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "src")

from vital.storage import _conn                       # noqa: E402


def rows(days: int) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with _conn() as c:
        found = c.execute(
            "SELECT user_id, thread_id, ts, rating, comment FROM feedback "
            "WHERE ts >= ? ORDER BY ts DESC", (cutoff,)).fetchall()
    return [dict(r) for r in found]


def weekly_trend(entries: list[dict]) -> list[tuple[str, int, int]]:
    """(week, up, down), oldest first.

    A single rate is a number; a trend is information. 70% positive means
    nothing until you know last month was 85%.
    """
    buckets: dict[str, Counter] = {}
    for entry in entries:
        try:
            week = datetime.fromisoformat(entry["ts"]).strftime("%Y-W%V")
        except (TypeError, ValueError):
            continue
        buckets.setdefault(week, Counter())[entry["rating"]] += 1
    return [(week, counts.get("up", 0), counts.get("down", 0))
            for week, counts in sorted(buckets.items())]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--threads", action="store_true",
                        help="list thread ids behind the negative ratings")
    args = parser.parse_args()

    entries = rows(args.days)
    if not entries:
        print(f"No feedback in the last {args.days} days.\n"
              "Either nobody is rating, or the thumbs are hard to find — "
              "both are worth knowing.")
        return 0

    up = sum(1 for e in entries if e["rating"] == "up")
    down = sum(1 for e in entries if e["rating"] == "down")
    total = up + down

    print(f"\nLast {args.days} days: {total} ratings")
    print(f"  up   {up:4}   {up / total:.0%}")
    print(f"  down {down:4}   {down / total:.0%}")

    trend = weekly_trend(entries)
    if len(trend) > 1:
        print("\nBy week")
        for week, week_up, week_down in trend:
            week_total = week_up + week_down
            rate = week_up / week_total if week_total else 0
            bar = "#" * round(rate * 20)
            print(f"  {week}  {rate:4.0%} {bar:<20} ({week_total})")

    comments = [e for e in entries if (e.get("comment") or "").strip()]
    if comments:
        print(f"\nComments ({len(comments)})")
        for entry in comments[:25]:
            mark = "+" if entry["rating"] == "up" else "-"
            print(f"  {mark} {entry['comment'].strip()[:110]}")

    if args.threads:
        negative = [e["thread_id"] for e in entries if e["rating"] == "down"]
        if negative:
            print(f"\nThreads rated down ({len(negative)}) — read these:")
            for thread_id in negative[:30]:
                print(f"  {thread_id}")

    # The only line that asks for action. A digest that reports without
    # prompting anything gets skimmed and then ignored.
    if total >= 10 and down / total > 0.3:
        print(f"\n>> {down / total:.0%} negative. Read the threads above and "
              "add the ones you can articulate to tests/answer_cases.py.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
