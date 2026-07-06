"""Shared graph state (D1: agents communicate ONLY through this schema)."""
from langgraph.graph import MessagesState


class VitalState(MessagesState):
    # messages: inherited, with add_messages reducer
    user_id: str
    routing_history: list[str]  # supervisor hops this turn — loop guard + debugging
