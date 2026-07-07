"""Calendar writers behind one interface (D6).

LocalCalendar: writes to our own table — works today, zero OAuth, and the
frontend can render it. GoogleCalendar arrives in Phase 5 with real per-user
OAuth; the planner graph won't change, only this adapter.
"""
from vital import storage


class LocalCalendar:
    def commit(self, user_id: str, plan: dict, plan_hash: str) -> int:
        """Idempotent: a plan hash commits at most once per user."""
        if storage.plan_already_committed(user_id, plan_hash):
            return 0
        items = plan.get("items", [])
        storage.save_calendar_events(user_id, plan_hash, items)
        return len(items)


class GoogleCalendar:
    """Phase 5: google-api-python-client + per-user OAuth refresh tokens
    (Secret Manager). Events tagged 'VITAL' so they're identifiable and
    bulk-deletable. Same .commit() signature."""
    def commit(self, user_id: str, plan: dict, plan_hash: str) -> int:
        raise NotImplementedError("GoogleCalendar lands in Phase 5 (needs OAuth)")
