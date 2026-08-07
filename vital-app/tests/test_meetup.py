"""Where two people should meet, and what the document may reveal.

Two properties matter and both fail silently:

1. **Fairness beats proximity.** A naive midpoint search optimises TOTAL
   distance, which reliably produces a venue three minutes from one person
   and forty from the other. Nobody would report that as a bug; they would
   just stop using the feature.
2. **Nothing precise leaks.** The buddy board's whole promise is that it
   holds city and area, never an address. Adding coordinates to make this
   feature work is exactly where that promise could quietly be broken.
"""
import os

os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test")
os.environ.setdefault("OPENWEATHER_API_KEY", "test")
os.environ.setdefault("GOOGLE_PLACES_API_KEY", "test")
os.environ.setdefault("SESSION_COOKIE_SECURE", "false")

import pytest

from vital import meetup
from vital.meetup import Person, Venue

# Albany and Schenectady, about 23 km apart.
ALBANY = Person("Sharayu", 42.65, -73.76)
SCHENECTADY = Person("Alex", 42.81, -73.94)


# ---------- geometry ----------

def test_distance_is_roughly_right():
    km = meetup.haversine_km(42.65, -73.76, 42.81, -73.94)
    assert 20 < km < 26, f"got {km} km between Albany and Schenectady"


def test_the_midpoint_is_between_them():
    lat, lng = meetup.midpoint(ALBANY, SCHENECTADY)
    assert 42.65 < lat < 42.81
    assert -73.94 < lng < -73.76


def test_the_midpoint_survives_the_antimeridian():
    """Averaging longitude puts the midpoint of 179 and -179 at ZERO — the
    other side of the planet. Rare, and completely wrong when it happens."""
    lat, lng = meetup.midpoint(Person("a", 0.0, 179.0), Person("b", 0.0, -179.0))
    assert abs(abs(lng) - 180) < 0.01, f"got {lng}"
    assert abs(lat) < 0.01


def test_the_search_radius_scales_with_separation():
    close = meetup.search_radius_km(ALBANY, Person("b", 42.66, -73.77))
    far = meetup.search_radius_km(ALBANY, Person("b", 43.9, -75.5))
    assert close >= 3.0, "two neighbours still need more than a 1km circle"
    assert far > close and far <= 25.0, "and the gap is not all walkable"


# ---------- the ranking, which is the actual product decision ----------

def _venues():
    return [
        Venue("Fair Middle Gym", "", 42.73, -73.85, rating=4.4),
        Venue("Next Door To One Of Them", "", 42.655, -73.765, rating=4.9),
        Venue("Another Hour Away", "", 43.9, -75.6, rating=5.0),
    ]


def test_a_fair_venue_beats_a_better_rated_one_next_door():
    """THE decision this feature exists to make. The 4.9 venue is better on
    paper and 22 km from the other person; the 4.4 is even. Even wins."""
    best = meetup.rank(_venues(), ALBANY, SCHENECTADY)[0]
    assert best.venue.name == "Fair Middle Gym"


def test_unreachable_venues_are_dropped_not_ranked_last():
    """A suggestion nobody would act on makes the whole document look like
    it was not thinking. Better a shorter list."""
    names = [s.venue.name for s in meetup.rank(_venues(), ALBANY, SCHENECTADY)]
    assert "Another Hour Away" not in names


def test_it_returns_at_most_three():
    many = [Venue(f"V{i}", "", 42.73 + i / 1000, -73.85, rating=4.0)
            for i in range(20)]
    assert len(meetup.rank(many, ALBANY, SCHENECTADY)) == 3


def test_every_suggestion_explains_itself_and_names_its_tradeoff():
    for s in meetup.rank(_venues(), ALBANY, SCHENECTADY):
        assert s.reasons, "a recommendation with no reason is a search result"
        assert s.tradeoff, "the cost to the other person must be stated"


def test_an_uneven_split_says_so_in_plain_numbers():
    """Two adults can decide for themselves if you tell them the numbers."""
    lopsided = [Venue("Next Door", "", 42.655, -73.765, rating=4.9)]
    s = meetup.rank(lopsided, ALBANY, SCHENECTADY)[0]
    assert "km" in s.tradeoff
    assert s.km_a < 2 and s.km_b > 20


def test_an_even_split_is_described_as_even():
    even = [Venue("Middle", "", 42.73, -73.85, rating=4.0)]
    s = meetup.rank(even, ALBANY, SCHENECTADY)[0]
    assert abs(s.km_a - s.km_b) < 1.5
    assert "even" in s.tradeoff.lower() or "each" in s.tradeoff.lower()


def test_stated_preferences_are_cited_not_invented():
    s = meetup.rank(_venues(), ALBANY, SCHENECTADY,
                    preferences=["weekday evenings"])[0]
    assert any("weekday evenings" in r for r in s.reasons)


def test_no_venues_returns_nothing_rather_than_guessing():
    assert meetup.rank([], ALBANY, SCHENECTADY) == []


def test_the_rejected_count_is_reported():
    """Three suggestions with no visible alternatives read as the only three
    results that existed."""
    chosen = meetup.rank(_venues(), ALBANY, SCHENECTADY)
    line = meetup.why_not_others(_venues(), chosen, ALBANY, SCHENECTADY)
    assert "3 places were considered" in line
    assert " was " in line, "singular count must not read 'the other 1 were'"


# ---------- privacy ----------

def test_stored_coordinates_are_coarse():
    """2dp is ~1.1km: enough to rank venues across a city, useless for
    finding where somebody lives. Rounded server-side because a modified
    client can send whatever precision it likes."""
    from vital import buddies, storage

    storage.set_location("42.6526134", "-73.7562891", "Albany")
    try:
        lat, lng = buddies._approx_location()
        assert lat == 42.65 and lng == -73.76
    finally:
        storage.current_location.set(None)


def test_no_location_stores_nothing_rather_than_a_default():
    from vital import buddies, storage

    storage.current_location.set(None)
    assert buddies._approx_location() == (None, None)


def test_the_document_never_contains_coordinates():
    """Both people get the SAME file, so anything in it is something each
    has agreed the other may see. Distance affects the decision; position
    does not, and is withheld."""
    pytest.importorskip("fpdf")
    from vital import meetup_pdf

    pdf = meetup_pdf.build("bouldering", ALBANY, SCHENECTADY,
                           meetup.rank(_venues(), ALBANY, SCHENECTADY))
    body = pdf.decode("latin-1", "ignore")
    for leak in ["42.65", "-73.76", "42.81", "-73.94", "42.73"]:
        assert leak not in body, f"the PDF leaks the coordinate {leak}"


def test_the_document_renders_with_awkward_characters():
    """fpdf2's built-in fonts are latin-1. One Portuguese cafe with a curly
    apostrophe would otherwise raise mid-render and 500 the whole
    document."""
    pytest.importorskip("fpdf")
    from vital import meetup_pdf

    awkward = [Venue("Café Niño — “the good one”", "Rua Açaí 12, Lisboa",
                     42.73, -73.85, rating=4.6)]
    pdf = meetup_pdf.build("coffee", ALBANY, SCHENECTADY,
                           meetup.rank(awkward, ALBANY, SCHENECTADY))
    assert pdf.startswith(b"%PDF")


def test_an_empty_shortlist_still_produces_a_readable_document():
    """Failing to find a fair venue is a real outcome and deserves a
    sentence, not a blank page."""
    pytest.importorskip("fpdf")
    from vital import meetup_pdf

    pdf = meetup_pdf.build("curling", ALBANY, SCHENECTADY, [])
    assert pdf.startswith(b"%PDF") and len(pdf) > 800
