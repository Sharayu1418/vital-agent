"""Self-correcting code-gen loop tests — fake LLM + fake runner, no E2B/GCP.
Covers: happy path, repair-then-success, give-up after max attempts,
and safety-gate rejection feeding the repair loop."""
import os

os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test")
os.environ.setdefault("OPENWEATHER_API_KEY", "test")
os.environ.setdefault("GOOGLE_PLACES_API_KEY", "test")

import pytest

from vital import storage
from vital.analysis import _strip_fences, build_analysis_graph


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    from vital.config import settings
    settings.cache_clear()
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "test.db"))
    storage.current_user_id.set("test-user")
    yield
    settings.cache_clear()


class Msg:
    def __init__(self, content):
        self.content = content


class ScriptedLLM:
    """Returns canned responses in order; records prompts it saw."""
    def __init__(self, responses):
        self.responses = list(responses)
        self.prompts = []

    def invoke(self, prompt):
        self.prompts.append(str(prompt))
        return Msg(self.responses.pop(0))


GOOD_CODE = "import pandas as pd\nprint('debt: 120 min')"
BAD_CODE = "import pandas as pd\nprint(df['nope'])"      # runtime error (fake)
UNSAFE_CODE = "import os\nprint(os.listdir('/'))"


def runner_ok(code):
    return {"stdout": "debt: 120 min", "error": None}


def runner_fail_then_ok():
    calls = {"n": 0}
    def run(code):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"stdout": "", "error": "KeyError: 'nope'"}
        return {"stdout": "debt: 120 min", "error": None}
    return run


def runner_always_fails(code):
    return {"stdout": "", "error": "KeyError: 'nope'"}


STATE = {"task": "sleep debt", "preview": "date,duration_min\n2026-07-01,400"}


def test_happy_path_produces_insight():
    llm = ScriptedLLM([GOOD_CODE, "You are 2 hours short this week."])
    out = build_analysis_graph(llm, runner_ok).invoke(STATE)
    assert out["insight"] == "You are 2 hours short this week."
    assert out["attempts"] == 0


def test_repair_loop_recovers():
    llm = ScriptedLLM([BAD_CODE, GOOD_CODE, "Fixed insight."])
    out = build_analysis_graph(llm, runner_fail_then_ok()).invoke(STATE)
    assert out["insight"] == "Fixed insight."
    assert out["attempts"] == 1
    assert "KeyError" in llm.prompts[1]          # repair prompt saw the error
    assert "duration_min" in llm.prompts[1]      # ...and the real columns


def test_gives_up_after_max_attempts():
    llm = ScriptedLLM([BAD_CODE, BAD_CODE, BAD_CODE, BAD_CODE])
    out = build_analysis_graph(llm, runner_always_fails, max_attempts=3).invoke(STATE)
    assert "couldn't complete" in out["insight"]
    assert out["attempts"] == 2  # write + 2 repairs = 3 executions


def test_unsafe_code_never_reaches_runner():
    executed = []
    def spy_runner(code):
        executed.append(code)
        return {"stdout": "x", "error": None}

    llm = ScriptedLLM([UNSAFE_CODE, GOOD_CODE, "insight"])
    out = build_analysis_graph(llm, spy_runner).invoke(STATE)
    assert UNSAFE_CODE not in executed           # gate blocked it
    assert executed == [GOOD_CODE]               # only the repaired code ran
    assert out["insight"] == "insight"


def test_every_execution_attempt_is_audited():
    llm = ScriptedLLM([BAD_CODE, GOOD_CODE, "insight"])
    build_analysis_graph(llm, runner_fail_then_ok()).invoke(STATE)
    audit = storage.sandbox_audit()
    assert len(audit) == 2
    assert {row["ok"] for row in audit} == {0, 1}


def test_strip_fences():
    assert _strip_fences("```python\nprint(1)\n```") == "print(1)"
    assert _strip_fences("print(1)") == "print(1)"
