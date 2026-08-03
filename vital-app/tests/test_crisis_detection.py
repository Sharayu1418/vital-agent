"""Crisis detection (P0-4): cascade behaviour + measured recall.

The measurement matters as much as the behaviour. Before this change the
detector was a substring match that caught 13.3% of the phrasings in
crisis_cases.py and fired on 4 innocent ones. Numbers, not vibes — so the
thresholds below are asserted, and they should only ever move up.

Note on honesty: the patterns and the case file were written together, so
the deterministic score is partly self-graded. It is asserted as a FLOOR
(the behaviour during a model outage), not as evidence the product works.
Real-world quality lives in test_crisis_live.py, which needs credentials.
"""
import os

os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test")
os.environ.setdefault("OPENWEATHER_API_KEY", "test")
os.environ.setdefault("GOOGLE_PLACES_API_KEY", "test")
os.environ.setdefault("SESSION_COOKIE_SECURE", "false")

import pytest

from crisis_cases import CRISIS, NOT_CRISIS
from vital import guardrails
from vital.config import settings


def _score(predicate):
    tp = [m for m in CRISIS if predicate(m)]
    fp = [m for m in NOT_CRISIS if predicate(m)]
    recall = len(tp) / len(CRISIS)
    precision = len(tp) / max(1, len(tp) + len(fp))
    return recall, precision, tp, fp


# ---------- measured floor ----------

def test_outage_floor_recall_beats_the_old_substring_detector():
    """The deterministic layer is now the fallback, not the detector. It
    must still be a large improvement, because it is what runs when the
    model is unreachable."""
    old_recall, _, _, _ = _score(guardrails.crisis_check)
    new_recall, _, _, _ = _score(guardrails.deterministic_crisis)
    assert old_recall < 0.20, f"baseline moved unexpectedly: {old_recall:.1%}"
    assert new_recall >= 0.85, f"outage floor regressed to {new_recall:.1%}"
    assert new_recall > old_recall * 4


def test_outage_floor_never_misses_a_stated_plan_or_active_self_harm():
    """Indirect phrasing can wait for the classifier. A stated method or
    current self-harm must be caught even with the model down."""
    urgent = [
        "I've been planning how I'd do it",
        "I have the pills ready",
        "I've been googling ways to go",
        "I've been cutting again",
        "I burned myself on purpose",
    ]
    for message in urgent:
        assert guardrails.deterministic_crisis(message), message


def test_outage_floor_covers_non_english():
    """A substring list of English phrases is blind to these entirely."""
    for message in ["ya no quiero vivir", "je ne veux plus vivre",
                    "ich will nicht mehr leben", "não quero mais viver",
                    "我不想活了", "もう生きていたくない"]:
        assert guardrails.deterministic_crisis(message), message


def test_everyday_language_still_does_not_trip_the_strict_list():
    """Regression guard on the original negatives."""
    for message in ["this workout is brutal, my legs are dead",
                    "I'm dying to try that pottery class",
                    "I slept terribly and feel like a zombie",
                    "kill the lights at 11pm, that's my plan"]:
        assert not guardrails.crisis_check(message), message


def test_vitals_own_subject_matter_is_never_a_crisis():
    """Tiredness, burnout and a bad week are what this product is FOR.
    Firing crisis resources at them would be both wrong and alienating."""
    for message in ["I'm exhausted and unmotivated lately",
                    "feeling really low energy this week",
                    "burnt out and I need something restorative this weekend",
                    "I have no energy and no direction right now"]:
        assert not guardrails.deterministic_crisis(message), message


# ---------- cascade behaviour ----------

def test_classifier_verdict_wins_over_keywords():
    """The Suicide Squad fix. 'suicide' is in the strict list, but the
    classifier can see it's a film and clear it."""
    message = "I watched Suicide Squad last night, any similar films?"
    assert guardrails.crisis_check(message)          # keywords say crisis
    assert not guardrails.assess(message, classifier=lambda _c: "clear")


def test_classifier_catches_what_keywords_cannot():
    message = "work was fine I guess. I just don't want to be around anymore"
    assert not guardrails.deterministic_crisis(message)   # no pattern matches
    assert guardrails.assess(message, classifier=lambda _c: "crisis")


def test_classifier_error_falls_back_to_deterministic():
    def broken(_context):
        raise RuntimeError("vertex unavailable")

    assert guardrails.assess("I want to kill myself", classifier=broken)
    assert not guardrails.assess("what a lovely morning", classifier=broken)


def test_classifier_timeout_falls_back_and_does_not_hang(monkeypatch):
    """A hung model call must not leave a distressed person on a spinner."""
    import time
    monkeypatch.setenv("CRISIS_TIMEOUT_SECONDS", "0.2")
    settings.cache_clear()

    def hangs(_context):
        time.sleep(30)
        return "clear"

    started = time.monotonic()
    verdict = guardrails.assess("ya no quiero vivir", classifier=hangs)
    elapsed = time.monotonic() - started

    assert elapsed < 5, f"assess() waited {elapsed:.1f}s on a hung classifier"
    assert verdict, "timeout must fail SAFE, not fail open"


def test_classifier_sees_recent_context_not_just_the_last_line():
    """'I don't want to be here anymore' is unreadable in isolation."""
    seen = {}

    def spy(context):
        seen["context"] = context
        return "clear"

    guardrails.assess("I don't want to be here anymore",
                      history=["human: I slept 3 hours", "ai: that's rough"],
                      classifier=spy)
    assert "slept 3 hours" in seen["context"]
    assert "LAST USER MESSAGE: I don't want to be here anymore" in seen["context"]


def test_context_is_capped():
    long_history = [f"human: message {i}" for i in range(50)]
    context = guardrails.build_context("hello", long_history)
    assert context.count("\n") <= guardrails.CONTEXT_TURNS


# ---------- API surface ----------

def _client(monkeypatch, graph):
    pytest.importorskip("langchain_google_vertexai")
    from fastapi.testclient import TestClient
    import vital.api as api
    monkeypatch.setattr(api, "graph", graph)
    return TestClient(api.app)


class RecordingGraph:
    def __init__(self):
        self.calls = 0

    async def astream_events(self, _inputs, config=None, version=None):
        self.calls += 1
        return
        yield

    def get_state(self, _config):
        from types import SimpleNamespace
        return SimpleNamespace(tasks=(), values={"messages": [],
                                                 "routing_history": []})


def test_indirect_phrasing_now_reaches_the_crisis_path(monkeypatch):
    """End-to-end: a message the old detector sailed past."""
    from vital import guardrails as g
    monkeypatch.setattr(g, "_default_classifier", lambda _c: "crisis")
    fake = RecordingGraph()
    client = _client(monkeypatch, fake)
    r = client.post("/chat", json={"message": "I don't see a way out of this"})
    assert r.status_code == 200
    assert "988" in r.text
    assert fake.calls == 0      # no routing, no tools


def test_approve_feedback_is_screened(monkeypatch):
    """/approve carries up to 1000 chars of free text and was never
    screened. Someone can say how they're really doing while editing a
    plan just as easily as while chatting."""
    from vital import guardrails as g
    monkeypatch.setattr(g, "_default_classifier", lambda _c: "crisis")
    fake = RecordingGraph()
    client = _client(monkeypatch, fake)
    r = client.post("/approve", json={"action": "edit",
                                      "feedback": "honestly I don't want to be here anymore"})
    assert r.status_code == 200
    assert "988" in r.text
    assert fake.calls == 0      # never reached the planner


def test_ordinary_approve_feedback_is_untouched(monkeypatch):
    """The screen must not turn plan edits into crisis responses."""
    fake = RecordingGraph()
    client = _client(monkeypatch, fake)
    r = client.post("/approve", json={"action": "edit",
                                      "feedback": "move the run to Sunday morning"})
    assert "988" not in r.text
