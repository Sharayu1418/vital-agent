"""What VITAL says while it is working.

The status line used to read `activity_scout: using search_places`. That is
the truth, and it is the wrong truth to tell: it names an internal node and
an internal function, it reads like machinery, and it leaks the shape of the
system to anyone watching the network tab.

This maps each tool to phrases describing what is happening FOR THE USER.
Around fifty of them, so a session does not repeat itself into wallpaper.

Two rules held throughout:

1. **Never claim more than the tool does.** "Reading your sleep history" is
   true; "understanding your sleep" is flattery. A status line that
   overstates is a small lie the user will eventually catch.
2. **Never name internals.** No tool names, no node names, no provider
   names. If a phrase would let someone reconstruct the architecture, it is
   the wrong phrase.

Selection is deterministic per call — seeded by the run id — so a status
line never flickers between renders while a tool is in flight, but two
calls to the same tool read differently.
"""
import hashlib

# Fallback for a tool with no entry. Deliberately vague rather than wrong:
# a new tool gets a sensible line the day it is added, and the test below
# makes sure someone notices it is missing.
DEFAULT = ["Working on it", "Looking into that", "Checking a few things"]

PHRASES: dict[str, list[str]] = {
    # --- weather ---
    "get_weather": [
        "Checking the sky where you are",
        "Seeing what the weather's doing",
        "Looking at conditions for later",
        "Checking whether you'll want to be outside",
    ],
    # --- places ---
    "search_places": [
        "Looking for places near you",
        "Scanning what's around",
        "Finding somewhere that fits",
        "Checking what's open nearby",
        "Looking for the right kind of spot",
        "Seeing what your area has",
    ],
    # --- events ---
    "search_events": [
        "Looking for what's on",
        "Checking upcoming events",
        "Seeing what's happening near you",
        "Scanning the calendar for your area",
    ],
    # --- people ---
    "find_activity_buddies": [
        "Looking for people up for this",
        "Checking who else is around",
        "Seeing who's looking for the same thing",
        "Finding people nearby with the same idea",
    ],
    "get_user_interests": [
        "Remembering what you're into",
        "Pulling up your interests",
        "Checking what you've told me you like",
    ],
    # --- sleep ---
    "log_sleep": [
        "Saving last night",
        "Writing that down",
        "Recording your sleep",
    ],
    "get_sleep_history": [
        "Reading your recent nights",
        "Looking back over your sleep",
        "Pulling up the last couple of weeks",
        "Checking how you've been sleeping",
    ],
    "analyze_sleep_data": [
        "Running the numbers on your sleep",
        "Working through your sleep data",
        "Doing the maths on your last few weeks",
        "Digging into the patterns",
        "This one takes a moment — crunching your data",
    ],
    # --- forecast ---
    "forecast_energy": [
        "Working out your energy curve",
        "Mapping how today should feel",
        "Predicting your peaks and dips",
        "Building your energy forecast",
        "Working out when you'll be sharpest",
        "Reading your rhythm for the day",
    ],
}

# What the app says between routing and the first token, keyed by which
# specialist picked the turn up. Sets expectation without naming the agent.
AGENT_OPENERS: dict[str, list[str]] = {
    "sleep_energy": [
        "Thinking about your energy",
        "Looking at how you've been sleeping",
        "Working out what today can hold",
    ],
    "activity_scout": [
        "Thinking about what you could do",
        "Looking for something that fits today",
        "Weighing up a few options",
    ],
    "idea_generator": [
        "Turning that over",
        "Thinking about where to point that energy",
        "Looking for something worth starting",
    ],
    "people_connector": [
        "Thinking about who you could do this with",
        "Looking for people and places",
    ],
    "planner": [
        "Shaping this into a plan",
        "Fitting the pieces into your week",
        "Building your schedule around your energy",
    ],
}


def _pick(options: list[str], seed: str) -> str:
    """Stable choice for a given seed.

    Not random: a status line that re-rolls on every React render flickers.
    Hashing the run id gives variety between calls and stillness within one.
    """
    if not options:
        return DEFAULT[0]
    digest = hashlib.md5(str(seed).encode()).digest()
    return options[digest[0] % len(options)]


def for_tool(tool_name: str, seed: str = "") -> str:
    """The line to show while `tool_name` is running."""
    return _pick(PHRASES.get(tool_name, DEFAULT), f"{tool_name}:{seed}")


def for_agent(node_name: str, seed: str = "") -> str:
    """The line to show once a specialist has picked up the turn."""
    options = AGENT_OPENERS.get(node_name)
    if not options:
        return ""
    return _pick(options, f"{node_name}:{seed}")


def word_count() -> int:
    """Total phrases available. Used by the test that keeps this varied."""
    return sum(len(v) for v in PHRASES.values()) + sum(
        len(v) for v in AGENT_OPENERS.values())
