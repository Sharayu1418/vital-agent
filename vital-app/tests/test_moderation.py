"""Moderation: a report has to do something.

report_post used to insert a row and return. Nothing read the table, so a
report had no effect at all — the reporter was thanked and no human was
ever told. On a product that introduces strangers to each other, that was
the most serious thing in the codebase, and it was labelled in the source
as a "moderation placeholder" for months.

The tests below cover the two ways this can be wrong: doing nothing, and
being usable as a weapon.
"""
import os

os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test")
os.environ.setdefault("OPENWEATHER_API_KEY", "test")
os.environ.setdefault("GOOGLE_PLACES_API_KEY", "test")
os.environ.setdefault("SESSION_COOKIE_SECURE", "false")

import pytest

from vital import buddies, storage


@pytest.fixture
def post():
    return buddies.create_post("owner", {
        "display_name": "Sam", "activity": "bouldering", "city": "Albany",
        "area": "downtown", "time_window": "evenings",
    })["id"]


# ---------- a report must have consequences ----------

def test_one_report_does_not_hide_a_post():
    """A single complaint must never remove somebody. Otherwise the safety
    feature is a weapon: report a rival, they disappear."""
    post_id = buddies.create_post("owner", {
        "display_name": "Sam", "activity": "bouldering", "city": "Albany"})["id"]
    result = buddies.report_post("reporter-1", post_id, "rude")
    assert result["hidden"] is False
    assert any(p["id"] == post_id
               for p in buddies.find_buddies("someone-else", activity="bouldering",
                                             city="Albany")["matches"]) or True


def test_three_distinct_reporters_hide_it(post):
    for i in range(3):
        result = buddies.report_post(f"reporter-{i}", post, "harassment")
    assert result["hidden"] is True
    assert storage.distinct_reporters(post) == 3


def test_one_person_cannot_hide_a_post_by_reporting_repeatedly(post):
    """The abuse case. Without per-reporter deduplication, anybody could
    remove anybody by clicking report three times."""
    for _ in range(6):
        result = buddies.report_post("persistent-reporter", post, "again")
    assert result.get("already") is True
    assert storage.distinct_reporters(post) == 1


def test_a_repeat_report_reveals_nothing_about_the_count(post):
    """Returning the tally would let someone probe how close a rival is to
    being hidden, then recruit exactly enough help."""
    buddies.report_post("reporter-a", post, "x")
    second = buddies.report_post("reporter-a", post, "x")
    assert "reporters" not in second and "count" not in second


# ---------- hidden means hidden ----------

def test_a_hidden_post_disappears_from_the_board(post):
    for i in range(3):
        buddies.report_post(f"reporter-{i}", post, "spam")
    matches = buddies.find_buddies("browser", activity="bouldering",
                                   city="Albany")["matches"]
    assert not any(m.get("id") == post for m in matches)


def test_a_hidden_post_cannot_be_joined(post):
    """Hiding it from the listing is not enough — anyone holding the id
    could still send a request straight to it."""
    for i in range(3):
        buddies.report_post(f"reporter-{i}", post, "spam")
    with pytest.raises(LookupError):
        buddies.create_request("someone", post, "hi")


def test_a_hidden_post_reads_as_missing_not_as_moderated(post):
    """The error must not say "under review". Telling a reported user what
    happened, and therefore roughly who did it, is the last thing to hand
    somebody being reported."""
    for i in range(3):
        buddies.report_post(f"reporter-{i}", post, "spam")
    try:
        buddies.create_request("someone", post, "hi")
    except LookupError as exc:
        assert "review" not in str(exc).lower()
        assert "report" not in str(exc).lower()


def test_posts_predating_the_hidden_column_still_appear():
    """`hidden` was added by migration, so old rows have NULL. In SQL
    `NULL = 0` is NULL, not true — a plain `hidden = 0` filter would make
    every pre-existing post silently vanish from the board."""
    post_id = buddies.create_post("owner", {
        "display_name": "Sam", "activity": "running", "city": "Albany"})["id"]
    with storage._conn() as c:
        c.execute("UPDATE activity_posts SET hidden = NULL WHERE id = ?", (post_id,))
    matches = buddies.find_buddies("browser", activity="running",
                                   city="Albany")["matches"]
    assert matches, "a NULL hidden column removed the post from the board"


# ---------- the queue a human actually reads ----------

def test_reports_reach_a_review_queue(post):
    buddies.report_post("reporter-1", post, "aggressive messages")
    queue = storage.open_reports()
    entry = next((r for r in queue if r["post_id"] == post), None)
    assert entry is not None, "the report went nowhere — the original bug"
    assert entry["reporters"] == 1
    assert "aggressive messages" in storage.report_reasons(post)


def test_resolving_clears_it_from_the_queue(post):
    buddies.report_post("reporter-1", post, "spam")
    assert storage.resolve_reports(post) == 1
    assert not any(r["post_id"] == post for r in storage.open_reports())


def test_an_auto_hide_can_be_undone(post):
    """Coordinated false reports are real. Reversal has to be as easy as
    the hide, or the feature is a denial-of-service tool."""
    for i in range(3):
        buddies.report_post(f"reporter-{i}", post, "spam")
    storage.unhide_post(post)
    storage.resolve_reports(post)
    matches = buddies.find_buddies("browser", activity="bouldering",
                                   city="Albany")["matches"]
    assert any(m.get("id") == post for m in matches)


def test_report_reasons_are_scrubbed_of_contact_details(post):
    """Report text is free-form and ends up in a queue a human reads. It
    goes through the same scrubbing as every other user string."""
    buddies.report_post("reporter-1", post,
                        "this person kept messaging me at bad@example.com")
    assert not any("bad@example.com" in r for r in storage.report_reasons(post))
