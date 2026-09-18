"""Deciding where a tool should look, when the model and the device disagree.

Every location-taking tool needs the same three-way judgment, and getting it
slightly different in each one is how the two halves of this app came to
disagree about the user's city in the first place. So it lives here once.

THE PROBLEM
-----------
The browser sends coordinates with every request and `storage.current_location`
holds them. The agent is also TOLD the location in a system message, because
it needs to talk about it. That second copy is the dangerous one: the model
reads a placename and types a placename back into the tool call, and a
placename resolves to a city centroid — Albany is 56 km2 — so an 11-metre fix
becomes a several-kilometre one on the way through.

But "always ignore the model" is wrong too. "What's the weather in Tokyo?" is
a real question and answering it about Albany is the same error, pointing the
other way, and harder to notice.

THE RULE
--------
    no city given          -> the device's coordinates
    city echoes our label  -> the device's coordinates   (an upgrade)
    city is coordinates    -> those coordinates, parsed  (never as a name)
    city names elsewhere   -> that name
    nothing at all         -> unknown, and the tool must say so

The echo case is the one that does the work. The model has been told "your
location is Albany, New York" and transcribing it into the call is CORRECT
behaviour on its part — it is just lossy, so we recover the precision instead
of punishing it. In practice this is the branch that fires almost every time.

The coordinate case is not hypothetical: `storage.set_location` falls back to
a "42.65, -73.76" label whenever the geocoder returns nothing, and a
name-only endpoint cannot do anything with that but fail.
"""
import re
from dataclasses import dataclass

from vital import storage

# "42.65, -73.76" and friends. Deliberately strict: two signed decimals and
# nothing else, so a real placename containing a comma cannot match.
_COORD_PAIR = re.compile(r"^\s*(-?\d{1,3}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)\s*$")

# Everything a placename can carry that says nothing about which place it is.
_NOISE = re.compile(r"[^a-z0-9 ]+")
_STATE_WORDS = {"usa", "us", "united states", "uk", "united kingdom"}


def _key(name: str) -> list[str]:
    """The comparable core of a placename, as tokens.

    'Albany, NY', 'Albany, New York' and 'albany' are the same request as far
    as this decision is concerned — they differ only in how much of the
    administrative tail the geocoder or the model chose to include. So the
    tail is what gets dropped.

    Order matters here and got it wrong once: stripping punctuation BEFORE
    splitting on the comma removes the comma, so 'Albany, NY' kept its tail
    as 'albany ny' and stopped matching 'Albany, New York'. Split first.
    """
    head = (name or "").split(",")[0].lower()
    return _NOISE.sub(" ", head).split()


@dataclass(frozen=True)
class Where:
    """A resolved place. Exactly one of (lat, lng) or name is authoritative."""

    lat: float | None
    lng: float | None
    name: str
    source: str          # "device" | "named" | "unknown"

    @property
    def known(self) -> bool:
        return self.source != "unknown"

    @property
    def precise(self) -> bool:
        return self.lat is not None


UNKNOWN = Where(None, None, "", "unknown")

# Half of the 2dp the fallback label is printed at. Two points agreeing this
# closely are the same fix rendered at different precision, not two places.
_SAME_POINT_DEG = 0.005


def _near(lat: float, lng: float, here: dict) -> bool:
    return (abs(lat - here["lat"]) <= _SAME_POINT_DEG
            and abs(lng - here["lng"]) <= _SAME_POINT_DEG)


def resolve(city: str | None = None) -> Where:
    """Where this call is actually about. See the module docstring for the rule."""
    here = storage.current_location.get()
    asked = str(city or "").strip()

    # A coordinate pair is never a name, whoever sent it and whyever.
    pair = _COORD_PAIR.match(asked)
    if pair:
        lat, lng = float(pair.group(1)), float(pair.group(2))
        if -90 <= lat <= 90 and -180 <= lng <= 180:
            # Prefer the stored fix when this is plainly the same point. The
            # fallback label is printed at 2dp (~1.1 km) while the contextvar
            # holds 4dp (~11 m), so parsing the label back would quietly
            # discard two decimal places we already had in hand.
            if here and _near(lat, lng, here):
                return Where(here["lat"], here["lng"], here["label"], "device")
            return Where(lat, lng, here["label"] if here else asked, "device")
        asked = ""      # out of range: treat as if nothing was said

    if here:
        if not asked or _same_place(asked, here["label"]):
            return Where(here["lat"], here["lng"], here["label"], "device")
        return Where(None, None, asked, "named")

    return Where(None, None, asked, "named") if asked else UNKNOWN


def _same_place(asked: str, label: str) -> bool:
    """Is the model echoing the location we gave it, rather than naming another?

    Compared as a token PREFIX, not a substring. 'Albany' echoes 'Albany, New
    York'. 'York' does not echo 'New York' — that is a different city on a
    different continent, and a plain substring test would have merged them.

    A false positive here answers a question about somewhere else using the
    user's own coordinates, silently. So this stays deliberately tight: one
    name must begin with the whole of the other.
    """
    a, b = _key(asked), _key(label)
    if not a or not b:
        return False
    if " ".join(a) in _STATE_WORDS or " ".join(b) in _STATE_WORDS:
        return False        # "USA" is not a place you take the weather of
    short, long = sorted((a, b), key=len)
    return long[:len(short)] == short
