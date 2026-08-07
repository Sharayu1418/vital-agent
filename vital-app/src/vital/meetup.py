"""Where two people should actually meet.

When a buddy request is accepted, both people get the same short document:
three places to do the activity, why those three, and what each one costs
the other person in travel.

WHY THIS IS NOT JUST "SEARCH NEAR THE MIDPOINT"
-----------------------------------------------
A naive midpoint search optimises total distance, which reliably produces a
venue three minutes from one person and forty from the other. What people
actually want is *fairness* — a place neither of them resents travelling
to. So ranking penalises the IMBALANCE between the two journeys as heavily
as it rewards the total being short.

The tradeoff is stated in the document rather than hidden: if the best
venue for the activity happens to sit near one person, that is often the
right answer, and saying "12 minutes for you, 34 for them" lets two adults
decide for themselves.

PRECISION AND PRIVACY
---------------------
Coordinates are rounded to 2dp (~1.1km) before they are ever stored. That
is deliberately too coarse to identify a home and easily precise enough to
rank venues across a city. Nothing here ever sees or stores an address, and
neither person is shown the other's coordinates — only the travel distance,
which is the part that affects their decision.

Pure functions, no I/O: venues come in as data, the document goes out as
bytes. The network lives in places.py and api.py.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

EARTH_RADIUS_KM = 6371.0

# How hard to punish an unfair split. At 1.0 a venue 10km from one person
# and 0km from the other scores the same as one 5km from each — which is
# wrong, because the first is a bad meeting point and the second is ideal.
# Above 1.0, fairness dominates total distance, which is the intent.
IMBALANCE_WEIGHT = 1.6

# Beyond this, "meeting in the middle" stops being a real suggestion.
MAX_REASONABLE_KM = 60.0


@dataclass(frozen=True)
class Person:
    label: str          # display name only; never a user id
    lat: float
    lng: float


@dataclass(frozen=True)
class Venue:
    name: str
    address: str
    lat: float
    lng: float
    rating: float | None = None
    price_level: str | None = None
    maps_url: str = ""


@dataclass(frozen=True)
class Suggestion:
    venue: Venue
    km_a: float
    km_b: float
    score: float
    reasons: list[str]
    tradeoff: str


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance. Straight-line, not driving distance.

    Honest about what it is: a routing API would give travel time, cost
    money per call, and change the answer by minutes rather than by which
    venue wins. The document says "straight-line" so nobody reads it as a
    door-to-door estimate.
    """
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = (math.sin(dp / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def midpoint(a: Person, b: Person) -> tuple[float, float]:
    """Spherical midpoint.

    Averaging latitude and longitude is wrong across the antimeridian and
    drifts at high latitude. This is barely more code and is simply
    correct, which matters because the search is centred here.
    """
    lat1, lng1 = math.radians(a.lat), math.radians(a.lng)
    lat2, lng2 = math.radians(b.lat), math.radians(b.lng)
    dl = lng2 - lng1

    bx = math.cos(lat2) * math.cos(dl)
    by = math.cos(lat2) * math.sin(dl)
    lat3 = math.atan2(math.sin(lat1) + math.sin(lat2),
                      math.sqrt((math.cos(lat1) + bx) ** 2 + by ** 2))
    lng3 = lng1 + math.atan2(by, math.cos(lat1) + bx)
    return round(math.degrees(lat3), 4), round(
        (math.degrees(lng3) + 540) % 360 - 180, 4)


def search_radius_km(a: Person, b: Person) -> float:
    """How wide to search around the midpoint.

    Half the separation plus a floor: two people 2km apart still want more
    than a 1km circle, and two people 40km apart do not want the whole gap
    treated as walkable.
    """
    return max(3.0, min(haversine_km(a.lat, a.lng, b.lat, b.lng) / 2 + 2.0, 25.0))


def _score(km_a: float, km_b: float) -> float:
    """Lower is better. Total travel, plus a penalty for imbalance."""
    return (km_a + km_b) + IMBALANCE_WEIGHT * abs(km_a - km_b)


def _reasons(venue: Venue, km_a: float, km_b: float, a: Person, b: Person,
             preferences: list[str]) -> list[str]:
    """Why this venue made the list. Only claims the data supports."""
    out = []
    gap = abs(km_a - km_b)
    if gap < 1.0:
        out.append("Almost exactly between you both")
    elif gap < 3.0:
        out.append("Close to evenly split between you")
    else:
        nearer = a.label if km_a < km_b else b.label
        out.append(f"Nearer {nearer}, but the best-rated option in range")

    if venue.rating is not None and venue.rating >= 4.5:
        out.append(f"Rated {venue.rating:.1f} — among the strongest nearby")
    elif venue.rating is not None and venue.rating >= 4.0:
        out.append(f"Rated {venue.rating:.1f}")

    if venue.price_level in ("FREE", "INEXPENSIVE"):
        out.append("Cheap or free")

    for preference in preferences[:2]:
        out.append(f"Fits what you said: {preference}")
    return out


def _tradeoff(km_a: float, km_b: float, a: Person, b: Person) -> str:
    """The honest sentence. Named as a tradeoff so nobody has to infer it."""
    gap = abs(km_a - km_b)
    if gap < 1.0:
        return (f"Roughly {km_a:.0f} km each — as even as it gets, "
                "so neither of you is doing the travelling.")
    further = a.label if km_a > km_b else b.label
    closer = b.label if km_a > km_b else a.label
    return (f"{further} travels about {max(km_a, km_b):.0f} km and {closer} "
            f"about {min(km_a, km_b):.0f} km. Worth it only if the venue is "
            "clearly the better fit — otherwise take one of the others.")


def rank(venues: list[Venue], a: Person, b: Person,
         preferences: list[str] | None = None, limit: int = 3) -> list[Suggestion]:
    """The three to suggest, best first.

    Venues further than MAX_REASONABLE_KM from either person are dropped
    entirely rather than ranked last: a suggestion nobody would act on is
    worse than a shorter list, because it makes the whole document look
    like it was not thinking.
    """
    preferences = preferences or []
    scored: list[Suggestion] = []
    for venue in venues:
        km_a = haversine_km(a.lat, a.lng, venue.lat, venue.lng)
        km_b = haversine_km(b.lat, b.lng, venue.lat, venue.lng)
        if km_a > MAX_REASONABLE_KM or km_b > MAX_REASONABLE_KM:
            continue
        scored.append(Suggestion(
            venue=venue, km_a=round(km_a, 1), km_b=round(km_b, 1),
            score=round(_score(km_a, km_b), 2),
            reasons=_reasons(venue, km_a, km_b, a, b, preferences),
            tradeoff=_tradeoff(km_a, km_b, a, b)))

    scored.sort(key=lambda s: (s.score, -(s.venue.rating or 0)))
    return scored[:limit]


def why_not_others(all_venues: list[Venue], chosen: list[Suggestion],
                   a: Person, b: Person) -> str:
    """One line on what was considered and rejected.

    A recommendation with no visible alternatives reads like the only three
    results that existed. Saying how many were weighed is the difference
    between a search result and a decision.
    """
    considered = len(all_venues)
    if considered <= len(chosen):
        return ""
    dropped = considered - len(chosen)
    plural = "was" if dropped == 1 else "were"
    return (f"{considered} places were considered. The other {dropped} "
            f"{plural} further from one of you, lower rated, or both.")
