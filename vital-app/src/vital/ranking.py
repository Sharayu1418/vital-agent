"""Which venues to suggest, and why. No model involved.

THE ARCHITECTURE THIS REPLACES
------------------------------
Places returned five venues and the model picked three. That reads as a
recommendation and is not one: an LLM handed five options has no
information the search did not already have, so it picks on vibes and
writes confident prose about the result. Every answer felt generic
because it WAS generic — the only judgement in the system was "which of
these five sounds nicest".

Now: retrieve forty, score them here, and hand the model the top three
WITH their scores. The model explains a decision it did not make. That
boundary is the point — scoring is arithmetic you can read, test and
argue with, and none of it is delegated to a system that cannot show its
work.

THE SIGNAL NOBODY ELSE HAS
--------------------------
`energy_fit` ranks a venue against the user's PREDICTED energy at the
hour they would actually go. A bouldering gym at their 15:30 dip is a bad
suggestion; the same gym at their 10:40 peak is a good one. Google cannot
compute that, and neither can any other venue search — it needs a
forecast of the person, which is the thing this app has.

Pure functions throughout: venues in, scored venues out. The network
lives in tools/places.py and the explanation lives in the agent.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

# How much effort a place demands, 0 (sitting down) to 1 (hard physical).
# Keyed on Google's primary place types. A static table rather than a model
# call: it is honest about being a rough mapping, it costs nothing, it is
# testable, and a wrong entry is one line to fix rather than a prompt to
# re-tune.
EFFORT_BY_TYPE: dict[str, float] = {
    # high
    "gym": 0.85, "fitness_center": 0.85, "rock_climbing": 0.9,
    "swimming_pool": 0.75, "sports_complex": 0.8, "hiking_area": 0.8,
    "stadium": 0.6, "sports_club": 0.8,
    # moderate
    "park": 0.45, "tourist_attraction": 0.5, "zoo": 0.5, "amusement_park": 0.6,
    "bowling_alley": 0.45, "dance_hall": 0.7, "national_park": 0.7,
    # low
    "cafe": 0.2, "coffee_shop": 0.2, "restaurant": 0.25, "bar": 0.3,
    "library": 0.15, "book_store": 0.2, "art_gallery": 0.25, "museum": 0.35,
    "movie_theater": 0.15, "spa": 0.1, "bakery": 0.2,
}
DEFAULT_EFFORT = 0.4        # unknown type: assume something middling

# Energy you do not have costs more than energy you do not spend. Being
# asked to climb when you are flat is a bad suggestion; being sent to a
# cafe when you are sharp is merely a slightly wasted peak.
DEFICIT_PENALTY = 1.4
SURPLUS_PENALTY = 0.25

# Weights for the additive signals. They sum to 1 so the base reads as a
# 0-1 score, and they live here rather than scattered through the function
# so the trade-offs are arguable in one place.
WEIGHTS = {
    "preference": 0.40,  # what we know they like, and the strongest of these
    "distance": 0.28,
    "quality": 0.21,
    "novelty": 0.11,     # small: variety matters, but not more than fit
}

# Energy is a MULTIPLIER, not another weighted term.
#
# As an additive signal it lost: a bouldering gym at the 15:30 dip still
# beat a cafe, because a strong preference match and 420 good reviews
# outweighed the energy deficit. But a recommendation you have not got the
# energy for is not a slightly worse recommendation — it is one you will
# not act on, and everything else about it is then irrelevant.
#
# Multiplying scales the whole score by how plausible attending is. The
# floor is deliberately above zero so a strongly-preferred activity can
# still surface at a dip; it just has to be much better on everything else
# to get there, which is the correct trade rather than a veto.
ENERGY_GATE_FLOOR = 0.35

# Bayesian shrinkage for ratings. A 5.0 from three reviews is not better
# than a 4.6 from eight hundred, and ranking by raw rating puts every
# barely-reviewed place at the top.
PRIOR_RATING = 4.2       # roughly the mean on Places
PRIOR_WEIGHT = 30        # reviews needed before the venue's own score dominates


@dataclass
class Candidate:
    name: str
    lat: float
    lng: float
    address: str = ""
    maps_url: str = ""
    rating: float | None = None
    rating_count: int = 0
    price_level: str | None = None
    types: list[str] = field(default_factory=list)
    attributes: list[str] = field(default_factory=list)  # from reviews, later


@dataclass
class Scored:
    venue: Candidate
    total: float
    signals: dict[str, float]
    reasons: list[str]


def effort_of(candidate: Candidate) -> float:
    """How demanding this place is. First recognised type wins."""
    for kind in candidate.types:
        if kind in EFFORT_BY_TYPE:
            return EFFORT_BY_TYPE[kind]
    return DEFAULT_EFFORT


def energy_fit(effort: float, predicted_energy: float) -> float:
    """Does what this asks of you match what you will have?

    Asymmetric on purpose. Suggesting something you lack the energy for is
    a failed recommendation — you will not go. Suggesting something easy
    when you are sharp merely wastes a good hour.
    """
    deficit = max(0.0, effort - predicted_energy)
    surplus = max(0.0, predicted_energy - effort)
    return max(0.0, 1.0 - DEFICIT_PENALTY * deficit - SURPLUS_PENALTY * surplus)


def quality(candidate: Candidate) -> float:
    """Rating, shrunk toward the mean by how few reviews back it.

    The standard weighted-rating trick. Without it the top of every list
    is a venue with one glowing review from the owner's cousin.
    """
    if candidate.rating is None:
        return 0.5          # unrated is not bad, just unknown
    votes = max(0, candidate.rating_count)
    weighted = ((votes / (votes + PRIOR_WEIGHT)) * candidate.rating
                + (PRIOR_WEIGHT / (votes + PRIOR_WEIGHT)) * PRIOR_RATING)
    return max(0.0, min(1.0, (weighted - 1.0) / 4.0))   # 1-5 stars -> 0-1


def proximity(km: float, comfortable_km: float = 5.0) -> float:
    """Closeness, decaying smoothly.

    Exponential rather than a cliff: 6km should not score the same as
    60km just because both are "far", and a hard cutoff makes the ranking
    jump around when someone moves one street.
    """
    return math.exp(-max(0.0, km) / max(0.5, comfortable_km))


def preference_fit(candidate: Candidate, likes: list[str],
                   dislikes: list[str]) -> tuple[float, list[str]]:
    """Overlap with what we know about them, and which terms matched.

    Deliberately simple string matching over the venue's name, types and
    review-derived attributes. It is not embeddings, because the terms are
    short and the failure mode of fuzzy matching here is confidently
    wrong ("gym" matching "gymnastics for toddlers"). Returning the
    matched terms is what lets the explanation cite something real.
    """
    haystack = " ".join([candidate.name, *candidate.types,
                         *candidate.attributes]).lower()

    matched = [term for term in likes if term.lower() in haystack]
    against = [term for term in dislikes if term.lower() in haystack]

    if against:
        # A dislike is close to disqualifying. Somebody who said they hate
        # gyms should not be shown a gym because it scored well elsewhere.
        return 0.0, [f"but they said: not {against[0]}"]
    if not likes:
        return 0.5, []      # nothing known: neutral, not penalised
    hit_rate = min(1.0, len(matched) / min(len(likes), 3))
    return hit_rate, [f"matches: {term}" for term in matched[:2]]


def _novelty(name: str, recently_suggested: list[str]) -> float:
    """Penalise repeats.

    A recommender that shows the same three places forever is a bookmark
    list. This is deliberately a small weight — variety matters, but not
    more than suggesting something they will actually enjoy.
    """
    return 0.0 if name.lower() in {n.lower() for n in recently_suggested} else 1.0


def score(candidate: Candidate, *, predicted_energy: float, km_away: float,
          likes: list[str] | None = None, dislikes: list[str] | None = None,
          recently_suggested: list[str] | None = None) -> Scored:
    """One venue, scored on every signal, with the reasons kept."""
    likes = likes or []
    dislikes = dislikes or []
    recently_suggested = recently_suggested or []

    effort = effort_of(candidate)
    fit, preference_reasons = preference_fit(candidate, likes, dislikes)
    signals = {
        "energy": energy_fit(effort, predicted_energy),
        "preference": fit,
        "distance": proximity(km_away),
        "quality": quality(candidate),
        "novelty": _novelty(candidate.name, recently_suggested),
    }
    base = sum(weight * signals[name] for name, weight in WEIGHTS.items())
    gate = ENERGY_GATE_FLOOR + (1 - ENERGY_GATE_FLOOR) * signals["energy"]
    total = base * gate

    reasons: list[str] = []
    if signals["energy"] >= 0.8:
        reasons.append("matches your predicted energy then"
                       if effort > 0.5 else "easy enough for how you'll feel")
    elif signals["energy"] <= 0.45:
        reasons.append("more demanding than you'll likely have in you")
    reasons.extend(preference_reasons)
    if km_away <= 2:
        reasons.append(f"{km_away:.1f} km away")
    if candidate.rating and candidate.rating_count >= PRIOR_WEIGHT:
        reasons.append(f"{candidate.rating:.1f} from {candidate.rating_count} reviews")
    if candidate.price_level in ("FREE", "INEXPENSIVE"):
        reasons.append("cheap or free")

    return Scored(venue=candidate, total=round(total, 4),
                  signals={k: round(v, 3) for k, v in signals.items()},
                  reasons=reasons)


def rank(candidates: list[Candidate], *, predicted_energy: float,
         distances_km: dict[str, float], likes: list[str] | None = None,
         dislikes: list[str] | None = None,
         recently_suggested: list[str] | None = None,
         limit: int = 3) -> list[Scored]:
    """The shortlist, best first.

    Whatever comes out of here is what the user is shown. The model that
    writes the answer receives this list and explains it; it does not get
    to reorder or substitute, because then the scoring would be advisory
    and we would be back to picking on vibes.
    """
    scored = [
        score(candidate, predicted_energy=predicted_energy,
              km_away=distances_km.get(candidate.name, 999.0),
              likes=likes, dislikes=dislikes,
              recently_suggested=recently_suggested)
        for candidate in candidates
    ]
    scored.sort(key=lambda s: s.total, reverse=True)
    return scored[:limit]
