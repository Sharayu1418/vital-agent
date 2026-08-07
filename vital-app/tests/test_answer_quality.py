"""Answer quality — the eval that did not exist.

WHAT WAS MISSING
----------------
Routing has 26 cases and a 90% gate. Crisis has a labelled set with recall
and precision. Memory has a live semantic eval. Nothing measured whether
the ANSWERS are any good — so VITAL could regress from sharp, grounded and
specific to vague and generic with every test still green.

Correctness was covered. Quality was not measurable, and what is not
measurable does not get improved; it only gets argued about.

HOW IT WORKS
------------
Each case in answer_cases.py carries rubric questions phrased so a NO is
unambiguous. A grader model answers each with YES or NO and a short
reason. The score is the fraction of YES.

    ANSWER_QUALITY_EVAL=1 uv run pytest tests/test_answer_quality.py -s

Live and explicit, like the other evals: it calls real models, costs real
money, and is exactly as flaky as anything talking to a hosted model. A CI
job that goes red for reasons nobody controls gets ignored, and then it
protects nothing.

THE LIMITS, STATED
------------------
A model grading a model is a proxy, not truth. It is used because the
alternative — reading ten answers by hand after every prompt change — does
not happen, and an imperfect number that exists beats a perfect one that
does not.

Two things keep it honest. Rubrics ask about the answer's CONTENT, never
its tone: "was this warm" is unfalsifiable and would drift the score for
no reason. And the grader is shown the rubric question alone, not the
case's intent, so it cannot infer the answer it is supposed to give.
"""
import json
import os

os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test")
os.environ.setdefault("OPENWEATHER_API_KEY", "test")
os.environ.setdefault("GOOGLE_PLACES_API_KEY", "test")
os.environ.setdefault("SESSION_COOKIE_SECURE", "false")

import pytest

from answer_cases import CASES

LIVE = os.environ.get("ANSWER_QUALITY_EVAL") == "1"

# Below this, a prompt change has made answers worse and should not ship.
# Starts deliberately modest: a gate nobody can pass gets lowered until it
# means nothing, and the point is to catch REGRESSION, not to assert the
# answers are already excellent.
GATE = 0.75

GRADER_PROMPT = """You are grading one answer from a wellness assistant \
against one specific question.

Answer ONLY with a JSON object: {{"verdict": "YES" or "NO", "why": "<10 words"}}

Judge exactly the question asked. Do not reward or punish anything else \
about the answer — not its tone, not its length, not whether you would \
have written it differently.

USER ASKED:
{message}

THE ASSISTANT ANSWERED:
{answer}

QUESTION TO JUDGE:
{question}"""


def _grader():
    from langchain_google_vertexai import ChatVertexAI

    from vital.config import settings
    cfg = settings()
    # temperature=0: a grader that varies between runs turns every score
    # change into a coin flip and the gate into noise.
    return ChatVertexAI(model=cfg.vital_model, temperature=0.0,
                        project=cfg.google_cloud_project,
                        location=cfg.google_cloud_location)


def grade(llm, message: str, answer: str, question: str) -> tuple[bool, str]:
    raw = llm.invoke(GRADER_PROMPT.format(
        message=message, answer=answer, question=question)).content
    text = str(raw).strip().removeprefix("```json").removeprefix("```").removesuffix("```")
    try:
        parsed = json.loads(text)
        return str(parsed.get("verdict", "")).upper() == "YES", parsed.get("why", "")
    except json.JSONDecodeError:
        # Unparseable grade counts as a FAIL, never a pass. A grader that
        # silently returns pass on malformed output would inflate every
        # score and hide the thing it exists to find.
        return False, f"unparseable: {text[:40]}"


# ---------- the set itself, checked offline on every CI run ----------

def test_every_case_is_well_formed():
    """Cheap structural checks so a malformed case fails in CI rather than
    halfway through a paid eval run."""
    seen = set()
    for case in CASES:
        assert case["id"] not in seen, f"duplicate case id {case['id']}"
        seen.add(case["id"])
        assert case["message"].strip()
        assert case["rubric"], f"{case['id']} has no rubric"
        for question in case["rubric"]:
            assert question.strip().endswith("?"), (
                f"{case['id']}: rubric items must be questions — {question!r}")


def test_rubrics_ask_about_content_not_tone():
    """'Was this warm?' is unfalsifiable and drifts the score for no
    reason. Rubrics have to be answerable from the text."""
    banned = ["warm", "friendly", "nice", "pleasant", "engaging", "well written"]
    for case in CASES:
        for question in case["rubric"]:
            lowered = question.lower()
            for word in banned:
                assert word not in lowered, (
                    f"{case['id']} grades tone with {word!r}, which cannot be "
                    "judged consistently")


def test_the_set_covers_the_things_that_matter():
    """Guards against the set drifting into whatever was easy to write."""
    ids = {c["id"] for c in CASES}
    assert len(CASES) >= 10
    for required in ["venues-must-be-real", "low-confidence-honesty",
                     "uses-what-it-knows", "permission-to-rest"]:
        assert required in ids, f"missing coverage: {required}"


def test_grading_failures_count_against_the_score():
    """A grader returning junk must not read as a pass — that would
    inflate every score and hide exactly what this exists to catch."""
    class Junk:
        def invoke(self, _prompt):
            return type("R", (), {"content": "not json at all"})()

    passed, why = grade(Junk(), "m", "a", "q?")
    assert passed is False and "unparseable" in why


# ---------- the live run ----------

@pytest.mark.skipif(not LIVE, reason="set ANSWER_QUALITY_EVAL=1")
def test_answer_quality_meets_the_gate():
    from langchain_core.messages import HumanMessage, SystemMessage

    from vital.agents.sleep_energy import SYSTEM_PROMPT

    llm = _grader()
    total = passed = 0
    failures = []

    for case in CASES:
        context = case.get("context", "")
        answer = llm.invoke([
            SystemMessage(content=SYSTEM_PROMPT
                          + (f"\n\nContext: {context}" if context else "")),
            HumanMessage(content=case["message"]),
        ]).content

        for question in case["rubric"]:
            total += 1
            ok, why = grade(llm, case["message"], str(answer), question)
            if ok:
                passed += 1
            else:
                failures.append(f"{case['id']}: {question[:60]}… — {why}")

    score = passed / total if total else 0.0
    print(f"\n  answer quality: {passed}/{total} = {score:.0%}  (gate {GATE:.0%})")
    for failure in failures:
        print(f"    FAIL {failure}")

    assert score >= GATE, (
        f"{score:.0%} is below the {GATE:.0%} gate — answers got worse")
