"""The ranking function — where the recommendation is actually made.

Before this, Places returned five venues and the model picked three. That
reads as a recommendation and is not one: an LLM handed five options has
no information the search did not already have, so it picks on tone. Every
answer felt generic because the only judgement in the system was "which of
these sounds nicest".

The decision now lives here, in arithmetic that can be read and argued
with. These tests are that argument written down.
"""
import os

os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test")
os.environ.setdefault("OPENWEATHER_API_KEY", "test")
os.environ.setdefault("GOOGLE_PLACES_API_KEY", "test")
os.environ.setdefault("SESSION_COOKIE_SECURE", "false")

import pytest

from vital import ranking
from vital.ranking import Candidate

GYM = Candidate("Albany Boulder Co", 42.66, -73.77, rating=4.7,
                rating_count=420, types=["rock_climbing"])
CAFE = Candidate("Quiet Corner Cafe", 42.66, -73.77, rating=4.4,
                 rating_count=310, types=["cafe"])
SAME_DISTANCE = {GYM.name: 1.2, CAFE.name: 1.2}


def order(*, energy, **kwargs):
    ranked = ranking.rank([GYM, CAFE], predicted_energy=energy,
                          distances_km=SAME_DISTANCE, **kwargs)
    return [s.venue.name for s in ranked]


# ---------- the signal nobody else has ----------

def test_energy_decides_when_nothing_else_separates_them():
    """THE claim the whole feature rests on. A bouldering gym at the 15:30
    dip is a bad suggestion; the same gym at the 10:40 peak is a good one.
    No venue search can compute that — it needs a forecast of the person."""
    assert order(energy=0.86)[0] == GYM.name
    assert order(energy=0.45)[0] == CAFE.name


def test_energy_is_a_gate_not_just_another_preference():
    """It was additive first, and the gym still beat the cafe at a dip
    because a preference match and 420 good reviews outweighed the energy
    deficit. But a suggestion you have not the energy for is not slightly
    worse — it is one you will not act on, so everything else about it
    stops mattering."""
    demanding = ranking.score(GYM, predicted_energy=0.30, km_away=1.0)
    easy = ranking.score(CAFE, predicted_energy=0.30, km_away=1.0)
    assert easy.total > demanding.total * 1.3, (
        "a heavy energy mismatch should suppress the score, not nudge it")


def test_lacking_energy_costs_more_than_having_it_spare():
    """Asymmetric on purpose. Being asked to climb when flat is a failed
    recommendation; being sent to a cafe when sharp is a wasted hour."""
    cannot = ranking.energy_fit(effort=0.9, predicted_energy=0.4)
    spare = ranking.energy_fit(effort=0.4, predicted_energy=0.9)
    assert spare > cannot


def test_a_strong_stated_preference_can_still_win_at_a_dip():
    """The gate has a floor for a reason. Somebody who loves climbing may
    well want the gym anyway — the ranking should make it harder to
    surface, not forbid it."""
    assert order(energy=0.45, likes=["climbing"])[0] == GYM.name


# ---------- quality, honestly ----------

def test_three_glowing_reviews_do_not_beat_hundreds_of_good_ones():
    """Ranking by raw rating puts every barely-reviewed place on top — the
    5.0 from the owner's cousin problem."""
    established = Candidate("Established", 0, 0, rating=4.6, rating_count=800,
                            types=["cafe"])
    brand_new = Candidate("Brand New", 0, 0, rating=5.0, rating_count=3,
                          types=["cafe"])
    assert ranking.quality(established) > ranking.quality(brand_new)


def test_an_unrated_venue_is_unknown_not_bad():
    """Treating 'no reviews' as zero would bury every new place forever."""
    unrated = ranking.quality(Candidate("New", 0, 0, types=["cafe"]))
    terrible = ranking.quality(Candidate("Bad", 0, 0, rating=2.0,
                                         rating_count=500, types=["cafe"]))
    assert unrated > terrible


# ---------- preferences ----------

def test_a_dislike_is_close_to_disqualifying():
    """Somebody who said they hate gyms must not be shown a gym because it
    scored well on everything else."""
    scored = ranking.score(GYM, predicted_energy=0.9, km_away=0.5,
                           dislikes=["climbing"])
    assert scored.signals["preference"] == 0.0
    assert any("not climbing" in r for r in scored.reasons)


def test_knowing_nothing_is_neutral_not_negative():
    """A new user with no stored facts should still get sensible results,
    not a list ranked as if they had rejected everything."""
    assert ranking.score(GYM, predicted_energy=0.8, km_away=1.0,
                         likes=[]).signals["preference"] == 0.5


def test_matched_preference_terms_come_back_for_the_explanation():
    """The model cites these. Without them it invents a rationale, which
    is exactly the generic prose this replaced."""
    scored = ranking.score(GYM, predicted_energy=0.8, km_away=1.0,
                           likes=["climbing"])
    assert any("climbing" in r for r in scored.reasons)


# ---------- distance and novelty ----------

def test_distance_decays_smoothly_rather_than_cliffing():
    """A hard cutoff makes the ranking lurch when somebody moves one
    street, and treats 6km and 60km as equally 'far'."""
    near, mid, far = (ranking.proximity(k) for k in (1, 6, 60))
    assert near > mid > far
    assert far > 0, "a distant venue should score low, not be erased"


def test_a_recently_suggested_venue_is_penalised():
    """A recommender that shows the same three places forever is a
    bookmark list."""
    fresh = ranking.score(GYM, predicted_energy=0.8, km_away=1.0)
    repeat = ranking.score(GYM, predicted_energy=0.8, km_away=1.0,
                           recently_suggested=["albany boulder co"])
    assert repeat.total < fresh.total


def test_novelty_does_not_outweigh_fit():
    """Variety matters less than suggesting something they will enjoy. A
    seen-before perfect match should still beat an unseen bad one."""
    seen_good = ranking.score(GYM, predicted_energy=0.9, km_away=1.0,
                              likes=["climbing"],
                              recently_suggested=[GYM.name])
    unseen_poor = ranking.score(CAFE, predicted_energy=0.9, km_away=25.0)
    assert seen_good.total > unseen_poor.total


# ---------- the contract with the agent ----------

def test_effort_falls_back_for_unknown_place_types():
    """Google adds place types constantly. An unrecognised one must score
    as middling, not crash or read as zero effort."""
    assert ranking.effort_of(Candidate("X", 0, 0, types=["axe_throwing"])) \
        == ranking.DEFAULT_EFFORT
    assert ranking.effort_of(Candidate("X", 0, 0, types=[])) \
        == ranking.DEFAULT_EFFORT


def test_every_result_carries_its_signals():
    """The agent explains a decision it did not make, so it needs the
    basis. Returning bare names would put it back to inventing reasons."""
    for scored in ranking.rank([GYM, CAFE], predicted_energy=0.8,
                               distances_km=SAME_DISTANCE):
        assert set(scored.signals) == {"energy", "preference", "distance",
                                       "quality", "novelty"}
        assert 0.0 <= scored.total <= 1.0


def test_scores_stay_within_range_under_absurd_input():
    """Ranking feeds a UI. A negative or >1 score would render as a broken
    bar and hide the real ordering."""
    for energy in (0.0, 1.0):
        for km in (0.0, 500.0):
            scored = ranking.score(GYM, predicted_energy=energy, km_away=km,
                                   dislikes=["climbing"])
            assert 0.0 <= scored.total <= 1.0


def test_the_shortlist_is_ordered_best_first():
    many = [Candidate(f"V{i}", 0, 0, rating=4.0 + i / 20, rating_count=100,
                      types=["cafe"]) for i in range(10)]
    ranked = ranking.rank(many, predicted_energy=0.5,
                          distances_km={c.name: 2.0 for c in many}, limit=3)
    assert len(ranked) == 3
    assert [s.total for s in ranked] == sorted((s.total for s in ranked),
                                               reverse=True)


def test_the_agent_prompt_forbids_reordering():
    """If the model may reorder, the scoring is advisory and we are back to
    picking on vibes with extra steps."""
    from vital.agents.activity_scout import SYSTEM_PROMPT

    # Collapse whitespace before matching. The first version of this asserted
    # `"do not" in SYSTEM_PROMPT.lower()` and failed the moment the prompt was
    # rewrapped — the text reads "Do\n   NOT reorder", so the substring was
    # split across a line break. A test that breaks on reflowing a paragraph
    # is testing the formatting, not the instruction.
    lowered = " ".join(SYSTEM_PROMPT.lower().split())
    assert "order given" in lowered or "already ranked" in lowered
    assert "do not" in lowered and "reorder" in lowered
