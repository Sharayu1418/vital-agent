"""Retrieve wide, rank here, let the model explain.

This is the seam between "called a search API" and "built a recommender".

    search_places (40 candidates)
        -> ranking.score  (energy fit, preference, distance, quality, novelty)
            -> top 3, WITH their signals and reasons
                -> the agent writes prose about a decision already made

The model never chooses. Handed five results and asked to pick three, an
LLM has no information the search did not already have — so it picks on
tone and writes confident prose about an arbitrary selection. That is why
the old answers felt generic: the only judgement in the system was
"which of these sounds nicest".

Everything that decides is arithmetic in ranking.py, which can be read,
tested and disagreed with. The model's job is the one it is actually good
at: turning a scored list into a sentence a person wants to read.
"""
from __future__ import annotations

from vital import ranking, storage
from vital.meetup import haversine_km

# Wide enough that ranking is a decision rather than a formality. Ranking
# three candidates out of three is not ranking.
CANDIDATE_POOL = 40


def _preferences(user_id: str) -> tuple[list[str], list[str]]:
    """Likes and dislikes, from what memory already holds.

    Reuses the semantic memory rather than adding a preferences table: the
    facts are already there, already user-visible, and already deletable.
    A second store would drift from the first, which is the mistake this
    codebase has made more than once.
    """
    from vital import memory

    likes: list[str] = []
    dislikes: list[str] = []
    try:
        facts = memory.all_memories(memory.get_store(), user_id)
    except Exception:
        return likes, dislikes

    for entry in facts:
        text = str(entry.get("fact", ""))
        lowered = text.lower()
        # Crude on purpose. The alternative is a model call per turn to
        # classify facts we wrote ourselves in a known format, which costs
        # latency to re-derive something we could simply have stored.
        target = dislikes if any(w in lowered for w in
                                 ("dislike", "hate", "avoid", "not into",
                                  "doesn't like", "does not like")) else likes
        for token in lowered.replace(".", " ").split():
            if len(token) > 4 and token not in _STOPWORDS:
                target.append(token)
    return likes[:12], dislikes[:8]


_STOPWORDS = {"user", "likes", "loves", "enjoys", "into", "prefers", "wants",
              "dislike", "dislikes", "hates", "avoid", "avoids", "doesn't",
              "their", "there", "about", "would", "really", "getting",
              "started", "interested"}


def find_activities(query: str, *, user_id: str, predicted_energy: float,
                    lat: float | None = None, lng: float | None = None,
                    city: str = "", limit: int = 3) -> dict:
    """Ranked activity suggestions, with the reasoning attached.

    Returns `error` rather than raising, like every other tool, so a
    Places outage degrades the answer instead of ending the conversation.
    """
    from vital.tools.places import search_near, search_places

    if lat is None or lng is None:
        # No coordinates: fall back to the text search, which cannot be
        # ranked by distance. Degraded, and says so, rather than inventing
        # a location to rank against.
        #
        # This used to pass `city or "nearby"`, which contradicted the line
        # above: "nearby" is not a place, so the text query became
        # "<query> in nearby" and Places ran an unbiased global search whose
        # results were then presented in the ordinary way. Passing None lets
        # search_places report that it has no location, which is the true
        # state and the one that gets the user asked.
        found = search_places.func(query=query, city=city or None,
                                   max_results=8)
        if "error" in found:
            return found
        return {"unranked": found.get("venues", []),
                "note": "no location available, so these are not ranked by "
                        "fit — they are search results"}

    found = search_near(query, lat, lng, radius_km=12.0,
                        max_results=CANDIDATE_POOL)
    if "error" in found:
        return found

    raw = found.get("venues", [])
    if not raw:
        return {"venues": [], "considered": 0}

    candidates = [
        ranking.Candidate(
            name=v["name"], lat=v["lat"], lng=v["lng"],
            address=v.get("address", ""), maps_url=v.get("maps_url", ""),
            rating=v.get("rating"), rating_count=v.get("rating_count", 0),
            price_level=v.get("price_level"), types=v.get("types") or [])
        for v in raw
    ]
    distances = {c.name: haversine_km(lat, lng, c.lat, c.lng)
                 for c in candidates}
    likes, dislikes = _preferences(user_id)

    top = ranking.rank(candidates, predicted_energy=predicted_energy,
                       distances_km=distances, likes=likes, dislikes=dislikes,
                       recently_suggested=storage.recent_suggestions(user_id),
                       limit=limit)

    storage.record_suggestions(user_id, [s.venue.name for s in top])

    return {
        "venues": [{
            "name": s.venue.name,
            "maps_url": s.venue.maps_url,
            "address": s.venue.address,
            "rating": s.venue.rating,
            "rating_count": s.venue.rating_count,
            "km_away": round(distances[s.venue.name], 1),
            "why": s.reasons,
            # Exposed so the explanation can cite the actual basis rather
            # than inventing one. The model is describing a decision, and
            # it should be able to say what drove it.
            "signals": s.signals,
            "effort": round(ranking.effort_of(s.venue), 2),
        } for s in top],
        "considered": len(candidates),
        "ranked_for_energy": round(predicted_energy, 2),
    }
