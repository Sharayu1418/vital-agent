"""Activity Buddy Board — safe, opt-in "find people to do things with".

Design (mirrors the rest of the app):
- Identity is ALWAYS the server-resolved user_id, passed in explicitly by
  api.py (or read from the contextvar by the agent tool). Client-supplied
  user ids never reach this module.
- Anonymous user ids embed the session secret (``anon-<session>``), so a
  post's user_id must NEVER appear in any public payload. Public views
  carry an opaque ``owner_key`` (a truncated hash) instead — enough for
  block/report, useless for session hijacking.
- Location granularity is city/area only; there is nowhere to store an
  exact address, and free-text fields are scrubbed of emails/phone numbers
  before they are persisted.
- Matching is deterministic scoring (no embeddings): activity and city
  filter, the softer preferences (time window, skill, budget, vibe) rank.

Persistence caveat (prototype): buddy tables live in the app-specific
SQLite store (``/tmp/vital.db`` on Cloud Run), like feedback/calendar data.
Posts and requests can reset on redeploys or instance restarts until the
app tables migrate to Postgres — the UI says so; don't overclaim.
"""
import hashlib
import re
from datetime import datetime, timezone

from vital.storage import _conn

# fields safe to show to OTHER users (never user_id / updated_at internals)
PUBLIC_FIELDS = ("id", "display_name", "activity", "city", "area", "time_window",
                 "vibe", "skill_level", "budget", "group_size", "notes", "created_at")

# Honest copy: area/notes ARE public (that's the point of a board), so say
# exactly what is visible instead of promising location privacy we can't
# fully enforce — the scrubber below is a backstop, not a guarantee.
SAFETY_NOTE = ("Meet in public places and tell someone where you're going. Only "
               "the city, area, and notes you choose to post are visible — don't "
               "include exact addresses or contact details (VITAL removes any it "
               "spots).")

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
# 7+ digits with optional separators — catches phone numbers without eating
# ordinary text like "2-4 people" or "10am"
_PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\s().-]{0,3}){7,}\d(?!\w)")
# street addresses: house number + up to three words + a street suffix
# ("123 Main St", "45 W Elm Street"). A backstop, not a guarantee — the
# safety copy tells users not to post exact locations in the first place.
_ADDRESS_RE = re.compile(
    r"(?<!\w)\d{1,5}\s+(?:[A-Za-z.]+\s+){0,3}"
    r"(?:st|street|ave|avenue|rd|road|blvd|boulevard|ln|lane|dr|drive|"
    r"ct|court|pl|place|way|ter|terrace|cir|circle|hwy|highway)\.?(?!\w)",
    re.IGNORECASE)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def public_user_key(user_id: str) -> str:
    """Opaque, stable public handle for a user — safe to expose, cannot be
    reversed into the session-bearing user_id."""
    return hashlib.sha256(f"vital-buddy:{user_id}".encode()).hexdigest()[:16]


def scrub_contact_info(text: str) -> str:
    """Public free-text never carries emails, phone numbers, or street
    addresses (address matching is best-effort — the UI copy tells users
    not to post exact locations at all)."""
    if not text:
        return ""
    out = _EMAIL_RE.sub("[removed]", text)
    out = _PHONE_RE.sub("[removed]", out)
    out = _ADDRESS_RE.sub("[removed]", out)
    return out.strip()


def _public_view(row: dict) -> dict:
    out = {k: row.get(k) for k in PUBLIC_FIELDS}
    out["owner_key"] = public_user_key(row["user_id"])
    return out


# ---------- posts ----------

_EDITABLE = ("display_name", "activity", "city", "area", "time_window", "vibe",
             "skill_level", "budget", "group_size", "notes")
_SCRUBBED = ("display_name", "activity", "city", "area", "time_window", "vibe",
             "skill_level", "budget", "group_size", "notes")


def create_post(user_id: str, fields: dict) -> dict:
    """Insert a post owned by the RESOLVED identity; returns the owner's view.
    Raises ValueError if a required field is blank AFTER scrubbing/trimming —
    pydantic can't catch whitespace-only values, and a post with no name,
    activity, or city is unusable and unsearchable."""
    clean = {k: scrub_contact_info(str(fields.get(k) or "")) for k in _SCRUBBED}
    blank = [k for k in ("display_name", "activity", "city") if not clean[k]]
    if blank:
        raise ValueError(f"required fields are blank: {', '.join(blank)}")
    now = _now()
    with _conn() as c:
        cur = c.execute(
            """INSERT INTO activity_posts
               (user_id, display_name, activity, city, area, time_window, vibe,
                skill_level, budget, group_size, notes, active, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, clean["display_name"], clean["activity"], clean["city"],
             clean["area"], clean["time_window"], clean["vibe"], clean["skill_level"],
             clean["budget"], clean["group_size"], clean["notes"],
             int(bool(fields.get("active", True))), now, now))
        post_id = cur.lastrowid
    return get_own_post(user_id, post_id)


def _post_row(c, post_id: int) -> dict | None:
    row = c.execute("SELECT * FROM activity_posts WHERE id = ?", (post_id,)).fetchone()
    return dict(row) if row else None


def get_own_post(user_id: str, post_id: int) -> dict:
    with _conn() as c:
        row = _post_row(c, post_id)
    if row is None or row["user_id"] != user_id:
        raise LookupError("post not found")
    view = _public_view(row)
    view["active"] = bool(row["active"])
    view["mine"] = True
    return view


def my_posts(user_id: str) -> list[dict]:
    with _conn() as c:
        rows = c.execute("SELECT * FROM activity_posts WHERE user_id = ? "
                         "ORDER BY created_at DESC", (user_id,)).fetchall()
        pending = {r["post_id"]: r["n"] for r in c.execute(
            """SELECT post_id, COUNT(*) AS n FROM activity_requests
               WHERE status = 'pending' AND post_id IN
                 (SELECT id FROM activity_posts WHERE user_id = ?)
               GROUP BY post_id""", (user_id,)).fetchall()}
    out = []
    for r in rows:
        view = _public_view(dict(r))
        view["active"] = bool(r["active"])
        view["mine"] = True
        view["pending_requests"] = pending.get(r["id"], 0)
        out.append(view)
    return out


def update_post(user_id: str, post_id: int, fields: dict) -> dict:
    """Owner-only update; unknown fields are ignored, text is scrubbed.
    Required fields, when present, get the same blank-after-scrub check
    as create_post — a PATCH must not hollow out a post."""
    blank = [k for k in ("display_name", "activity", "city")
             if fields.get(k) is not None and not scrub_contact_info(str(fields[k]))]
    if blank:
        raise ValueError(f"required fields are blank: {', '.join(blank)}")
    with _conn() as c:
        row = _post_row(c, post_id)
        if row is None:
            raise LookupError("post not found")
        if row["user_id"] != user_id:
            raise PermissionError("only the owner can update a post")
        sets, vals = [], []
        for k in _EDITABLE:
            if fields.get(k) is not None:
                sets.append(f"{k} = ?")
                vals.append(scrub_contact_info(str(fields[k])))
        if fields.get("active") is not None:
            sets.append("active = ?")
            vals.append(int(bool(fields["active"])))
        if sets:
            sets.append("updated_at = ?")
            vals.append(_now())
            c.execute(f"UPDATE activity_posts SET {', '.join(sets)} WHERE id = ?",
                      (*vals, post_id))
    return get_own_post(user_id, post_id)


# ---------- matching ----------

def match_score(post: dict, query: dict) -> tuple[int, list[str]]:
    """Deterministic compatibility score + human-readable reasons.
    Pure function of (post, query) — no I/O, unit-testable."""
    score, reasons = 0, []

    q = {k: (query.get(k) or "").strip().lower() for k in
         ("activity", "city", "time_window", "skill_level", "budget", "vibe")}
    p = {k: (post.get(k) or "").strip().lower() for k in
         ("activity", "city", "area", "time_window", "skill_level", "budget", "vibe")}

    if q["activity"] and p["activity"]:
        if q["activity"] == p["activity"]:
            score += 3
            reasons.append(f"same activity: {post['activity']}")
        elif q["activity"] in p["activity"] or p["activity"] in q["activity"]:
            score += 2
            reasons.append(f"similar activity: {post['activity']}")
    if q["city"] and (q["city"] == p["city"] or q["city"] in p["area"]):
        score += 2
        reasons.append(f"nearby: {post['city']}")
    if q["time_window"] and p["time_window"] and (
            q["time_window"] in p["time_window"] or p["time_window"] in q["time_window"]):
        score += 1
        reasons.append(f"works {post['time_window']}")
    if q["skill_level"] and q["skill_level"] == p["skill_level"]:
        score += 1
        reasons.append(f"{post['skill_level']} level too")
    if q["budget"] and q["budget"] == p["budget"]:
        score += 1
        reasons.append(f"{post['budget']} budget")
    if q["vibe"] and q["vibe"] == p["vibe"]:
        score += 1
        reasons.append(f"{post['vibe']} vibe")
    return score, reasons


def search_posts(user_id: str, activity: str | None = None, city: str | None = None,
                 time_window: str | None = None, skill_level: str | None = None,
                 budget: str | None = None, vibe: str | None = None,
                 include_own: bool = False, limit: int = 20) -> list[dict]:
    """Active posts, scored and ranked. Own posts excluded unless asked;
    blocked users excluded in both directions. Only public fields returned."""
    query = {"activity": activity, "city": city, "time_window": time_window,
             "skill_level": skill_level, "budget": budget, "vibe": vibe}
    my_key = public_user_key(user_id)
    with _conn() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM activity_posts WHERE active = 1 "
            "ORDER BY created_at DESC LIMIT 500").fetchall()]
        i_blocked = {r["blocked_key"] for r in c.execute(
            "SELECT blocked_key FROM user_blocks WHERE user_id = ?", (user_id,)).fetchall()}
        blocked_me = {public_user_key(r["user_id"]) for r in c.execute(
            "SELECT user_id FROM user_blocks WHERE blocked_key = ?", (my_key,)).fetchall()}

    out = []
    for row in rows:
        if row["user_id"] == user_id and not include_own:
            continue
        owner_key = public_user_key(row["user_id"])
        if owner_key in i_blocked or owner_key in blocked_me:
            continue
        score, reasons = match_score(row, query)
        # hard filters: a named activity/city must actually match the post;
        # softer preferences (time/skill/budget/vibe) only affect ranking
        if activity and not any("activity" in r for r in reasons):
            continue
        if city and not any(r.startswith("nearby") for r in reasons):
            continue
        view = _public_view(row)
        view["mine"] = row["user_id"] == user_id
        view["match_score"] = score
        view["match_reasons"] = reasons
        out.append(view)
    out.sort(key=lambda v: v["created_at"], reverse=True)   # newest first…
    out.sort(key=lambda v: v["match_score"], reverse=True)  # …within score rank
    return out[:limit]


# ---------- requests ----------

def create_request(user_id: str, post_id: int, message: str = "",
                   requester_name: str = "") -> dict:
    now = _now()
    with _conn() as c:
        post = _post_row(c, post_id)
        if post is None or not post["active"]:
            raise LookupError("post not found")
        if post["user_id"] == user_id:
            raise ValueError("you can't request to join your own post")
        # blocks work both ways, and BEFORE anything is inserted. The owner's
        # block reads as a plain 404 so blocking is never disclosed.
        owner_key = public_user_key(post["user_id"])
        my_key = public_user_key(user_id)
        blocked = c.execute(
            """SELECT user_id, blocked_key FROM user_blocks
               WHERE (user_id = ? AND blocked_key = ?)
                  OR (user_id = ? AND blocked_key = ?)""",
            (user_id, owner_key, post["user_id"], my_key)).fetchone()
        if blocked:
            if blocked["user_id"] == user_id:
                raise ValueError("you've blocked this member")
            raise LookupError("post not found")
        dup = c.execute(
            "SELECT 1 FROM activity_requests WHERE post_id = ? AND "
            "requester_user_id = ? AND status = 'pending'", (post_id, user_id)).fetchone()
        if dup:
            raise ValueError("you already have a pending request for this post")
        cur = c.execute(
            """INSERT INTO activity_requests
               (post_id, requester_user_id, requester_name, message, status,
                created_at, updated_at) VALUES (?, ?, ?, ?, 'pending', ?, ?)""",
            (post_id, user_id, scrub_contact_info(requester_name) or "A VITAL member",
             scrub_contact_info(message), now, now))
        req_id = cur.lastrowid
    return {"id": req_id, "post_id": post_id, "status": "pending"}


def my_requests(user_id: str) -> dict:
    """Incoming = requests on my posts; outgoing = requests I sent.
    Neither direction ever includes a raw user_id."""
    with _conn() as c:
        incoming = [dict(r) for r in c.execute(
            """SELECT q.id, q.post_id, q.requester_name, q.message, q.status,
                      q.created_at, p.activity, p.display_name AS post_display_name
               FROM activity_requests q JOIN activity_posts p ON p.id = q.post_id
               WHERE p.user_id = ? ORDER BY q.created_at DESC""", (user_id,)).fetchall()]
        outgoing = [dict(r) for r in c.execute(
            """SELECT q.id, q.post_id, q.message, q.status, q.created_at,
                      p.activity, p.display_name, p.city
               FROM activity_requests q JOIN activity_posts p ON p.id = q.post_id
               WHERE q.requester_user_id = ? ORDER BY q.created_at DESC""",
            (user_id,)).fetchall()]
    return {"incoming": incoming, "outgoing": outgoing}


def decide_request(user_id: str, request_id: int, status: str) -> dict:
    """Accept/reject — only the owner of the TARGET POST may decide."""
    if status not in ("accepted", "rejected"):
        raise ValueError("status must be accepted or rejected")
    with _conn() as c:
        row = c.execute(
            """SELECT q.id, q.post_id, q.status, p.user_id AS owner_id
               FROM activity_requests q JOIN activity_posts p ON p.id = q.post_id
               WHERE q.id = ?""", (request_id,)).fetchone()
        if row is None:
            raise LookupError("request not found")
        if row["owner_id"] != user_id:
            raise PermissionError("only the post owner can decide this request")
        c.execute("UPDATE activity_requests SET status = ?, updated_at = ? WHERE id = ?",
                  (status, _now(), request_id))
    return {"id": request_id, "post_id": row["post_id"], "status": status}


# ---------- moderation placeholders ----------

def report_post(user_id: str, post_id: int, reason: str = "") -> dict:
    with _conn() as c:
        if _post_row(c, post_id) is None:
            raise LookupError("post not found")
        c.execute("INSERT INTO activity_reports (post_id, reporter_user_id, reason, "
                  "created_at) VALUES (?, ?, ?, ?)",
                  (post_id, user_id, reason[:500], _now()))
    return {"reported": post_id}


def block_user(user_id: str, blocked_key: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{16}", blocked_key or ""):
        raise ValueError("invalid user key")
    if blocked_key == public_user_key(user_id):
        raise ValueError("you can't block yourself")
    with _conn() as c:
        c.execute("INSERT OR IGNORE INTO user_blocks VALUES (?, ?, ?)",
                  (user_id, blocked_key, _now()))
    return {"blocked": blocked_key}
