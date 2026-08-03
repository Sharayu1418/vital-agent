"""Side-panel endpoint tests: sleep merge, calendar, thread history."""
from types import SimpleNamespace

import pytest

from vital import ingest, storage


def _client(monkeypatch, graph=None):
    pytest.importorskip("langchain_google_vertexai")
    from fastapi.testclient import TestClient
    import vital.api as api

    class NullGraph:
        async def astream_events(self, *_a, **_k):
            return
            yield

        def get_state(self, _c):
            return SimpleNamespace(tasks=(), values={})

    monkeypatch.setattr(api, "graph", graph or NullGraph())
    return TestClient(api.app)


def _session_user(client):
    client.get("/memories")  # establish a session
    from vital import security
    return f"anon-{client.cookies[security.SESSION_COOKIE]}"


def test_sleep_recent_reads_manual_logs_for_the_caller_not_stale_context(monkeypatch):
    """P1 regression. Manual log on a date the upload does NOT cover — if the
    endpoint inherits a stale contextvar instead of setting identity itself,
    the manual night vanishes and this fails."""
    client = _client(monkeypatch)
    user_id = _session_user(client)

    storage.current_user_id.set(user_id)
    storage.log_sleep("23:00", "07:00", 3)          # today, manual, 480min
    ingest.save_sleep_data(user_id, [               # upload covers a DIFFERENT date
        {"date": "2026-07-01", "duration_min": 400, "quality": "4", "source": "csv_upload"},
    ])
    # poison the contextvar — the endpoint must set identity itself
    storage.current_user_id.set("someone-else-entirely")

    from datetime import date
    today = date.today().isoformat()
    body = client.get("/sleep/recent").json()
    assert body["target_min"] == 480
    by_date = {n["date"]: n for n in body["nights"]}
    assert by_date[today]["duration_min"] == 480    # manual night visible
    assert by_date[today]["source"] == "manual"
    assert by_date["2026-07-01"]["duration_min"] == 400
    assert len(body["nights"]) <= 14


def test_sleep_recent_upload_wins_on_date_conflict(monkeypatch):
    client = _client(monkeypatch)
    user_id = _session_user(client)
    storage.current_user_id.set(user_id)
    storage.log_sleep("23:00", "07:00", 3)          # today, manual, 480min
    from datetime import date
    today = date.today().isoformat()
    ingest.save_sleep_data(user_id, [
        {"date": today, "duration_min": 450, "quality": "", "source": "csv_upload"},
    ])
    body = client.get("/sleep/recent").json()
    by_date = {n["date"]: n for n in body["nights"]}
    assert by_date[today]["duration_min"] == 450    # upload wins


def test_calendar_returns_committed_events(monkeypatch):
    client = _client(monkeypatch)
    client.get("/memories")
    from vital import security
    user_id = f"anon-{client.cookies[security.SESSION_COOKIE]}"
    storage.save_calendar_events(user_id, "hash1", [
        {"day": "Saturday", "start": "10:00", "end": "12:00",
         "title": "Bouldering", "kind": "activity"}])

    body = client.get("/calendar").json()
    assert body["events"][0]["title"] == "Bouldering"


def test_thread_messages_returns_history_and_skips_tool_noise(monkeypatch):
    class HistoryGraph:
        def __init__(self):
            self.asked = None

        async def astream_events(self, *_a, **_k):
            return
            yield

        def get_state(self, config):
            self.asked = config["configurable"]["thread_id"]
            return SimpleNamespace(tasks=(), values={"messages": [
                SimpleNamespace(type="human", content="how did I sleep?"),
                SimpleNamespace(type="tool", content="raw tool json"),
                SimpleNamespace(type="ai", content=""),  # tool-call stub, no text
                SimpleNamespace(type="ai", content=[{"type": "text", "text": "Pretty well!"}]),
            ]})

    g = HistoryGraph()
    client = _client(monkeypatch, g)
    body = client.get("/threads/t1/messages").json()
    assert body["messages"] == [
        {"role": "human", "text": "how did I sleep?"},
        {"role": "ai", "text": "Pretty well!"},
    ]
    assert body["pending_approval"] is None
    assert g.asked.startswith("anon-") and g.asked.endswith(":t1")  # identity-scoped


def test_thread_messages_surfaces_pending_approval(monkeypatch):
    plan_payload = {"type": "plan_approval", "plan": {"items": [], "tradeoffs": "none"}}

    class PausedGraph:
        async def astream_events(self, *_a, **_k):
            return
            yield

        def get_state(self, _c):
            return SimpleNamespace(
                tasks=(SimpleNamespace(interrupts=(SimpleNamespace(value=plan_payload),)),),
                values={"messages": []})

    client = _client(monkeypatch, PausedGraph())
    body = client.get("/threads/t1/messages").json()
    assert body["pending_approval"] == plan_payload


def test_thread_messages_rejects_bad_thread_id(monkeypatch):
    client = _client(monkeypatch)
    assert client.get("/threads/" + "x" * 65 + "/messages").status_code == 422


# ---------- P0-1: sync handlers now run in a threadpool ----------

def test_sync_handlers_do_not_leak_identity_between_concurrent_callers(monkeypatch):
    """P0-1 regression guard.

    /sleep/recent and friends are now plain `def`, so FastAPI dispatches them
    to a threadpool instead of blocking the event loop. Each dispatch must
    still resolve identity from the CALLER's session and set the contextvar
    itself — if a worker thread ever inherited a previous request's
    current_user_id, two users hitting the panel at once would read each
    other's sleep data. Fire both concurrently and assert strict isolation.
    """
    import threading

    client_a = _client(monkeypatch)
    client_b = _client(monkeypatch)
    user_a = _session_user(client_a)
    user_b = _session_user(client_b)
    assert user_a != user_b

    # MANUAL logs, deliberately: storage.log_sleep() reads current_user_id,
    # so this is the path that actually breaks when the contextvar leaks.
    # (Uploaded rows are fetched by explicit user_id and would pass either way.)
    storage.current_user_id.set(user_a)
    storage.log_sleep("23:00", "07:00", 3)      # 480 min
    storage.current_user_id.set(user_b)
    storage.log_sleep("01:00", "06:00", 2)      # 300 min
    storage.current_user_id.set("someone-else-entirely")  # poison the parent context

    results = {"a": [], "b": []}

    def hit(name, client):
        for _ in range(5):  # repeat: thread reuse is what would expose a leak
            results[name].append(
                [n["duration_min"] for n in client.get("/sleep/recent").json()["nights"]])

    threads = [threading.Thread(target=hit, args=("a", client_a)),
               threading.Thread(target=hit, args=("b", client_b))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # every single response, on every worker thread, sees only its own night
    assert results["a"] == [[480]] * 5, results["a"]
    assert results["b"] == [[300]] * 5, results["b"]
