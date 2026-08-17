"""Activity Buddy Board tests.

Layer 1: domain logic (vital.buddies) — ownership, matching, privacy scrubbing.
Layer 2: routes through the real FastAPI app — server-resolved identity owns
posts, fake ids in the body are ignored, and public payloads never leak the
session-bearing user_id.
Layer 3: the People Connector tool — structured matches, graceful failure.
"""
import os

os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test")
os.environ.setdefault("OPENWEATHER_API_KEY", "test")
os.environ.setdefault("GOOGLE_PLACES_API_KEY", "test")
os.environ.setdefault("SESSION_COOKIE_SECURE", "false")

import pytest

from vital import buddies, storage


def _post(user_id="owner", **over) -> dict:
    fields = {"display_name": "Swim Sam", "activity": "swimming",
              "city": "Albany", "area": "Guilderland", "time_window": "weekend",
              "vibe": "casual", "skill_level": "beginner", "budget": "low",
              "group_size": "2-4", "notes": "lap swimming, low pressure"}
    fields.update(over)
    return buddies.create_post(user_id, fields)


# ---------- Layer 1: domain ----------

def test_created_post_is_owned_and_publicly_safe():
    post = _post(user_id="anon-" + "a" * 32)
    assert post["mine"] is True and post["active"] is True
    assert "user_id" not in post
    assert "a" * 32 not in str(post)          # session secret never serialized
    assert len(post["owner_key"]) == 16


def test_contact_info_is_scrubbed_from_public_text():
    post = _post(notes="text me at 518-555-0123 or sam@example.com!")
    assert "518" not in post["notes"] and "@" not in post["notes"]
    assert "[removed]" in post["notes"]


def test_scrub_leaves_ordinary_text_alone():
    assert buddies.scrub_contact_info("2-4 people, 10am at the Y") == \
        "2-4 people, 10am at the Y"


def test_street_addresses_are_scrubbed_from_public_text():
    post = _post(notes="meet at 123 Main St, then coffee",
                 area="45 W Elm Street apartment 2")
    assert "Main" not in post["notes"] and "[removed]" in post["notes"]
    assert "Elm" not in post["area"]
    for text in ("14 Ocean Ave.", "9 Sunset Blvd", "12345 Long Meadow Lane"):
        assert "[removed]" in buddies.scrub_contact_info(text), text


def test_address_scrub_spares_addressless_numbers():
    for text in ("group of 2-4", "starts 10am", "route 5k loop", "level 2 class"):
        assert buddies.scrub_contact_info(text) == text, text


def test_search_returns_other_users_active_posts_only():
    _post(user_id="owner")
    _post(user_id="owner2", activity="swimming", display_name="Lane Pal")
    inactive = _post(user_id="owner3")
    buddies.update_post("owner3", inactive["id"], {"active": False})
    results = buddies.search_posts("searcher", activity="swimming")
    names = {p["display_name"] for p in results}
    assert names == {"Swim Sam", "Lane Pal"}   # inactive post absent


def test_search_excludes_own_posts_unless_asked():
    mine = _post(user_id="me")
    _post(user_id="other")
    assert all(p["id"] != mine["id"] for p in buddies.search_posts("me"))
    with_own = buddies.search_posts("me", include_own=True)
    assert any(p["id"] == mine["id"] and p["mine"] for p in with_own)


def test_search_payload_has_no_private_fields():
    _post(user_id="anon-" + "b" * 32)
    (result,) = buddies.search_posts("searcher", activity="swimming")
    assert set(result) <= set(buddies.PUBLIC_FIELDS) | {
        "owner_key", "mine", "match_score", "match_reasons"}
    assert "b" * 32 not in str(result)


def test_match_score_ranks_and_explains():
    _post(user_id="u1", activity="swimming", city="Albany",
          time_window="weekend", skill_level="beginner", budget="low")
    _post(user_id="u2", activity="open water swimming", city="Troy",
          display_name="River Swimmer")
    results = buddies.search_posts(
        "searcher", activity="swimming",
        time_window="weekend", skill_level="beginner", budget="low")
    assert results[0]["display_name"] == "Swim Sam"     # exact-everything first
    assert results[0]["match_score"] > results[1]["match_score"]
    assert any("same activity" in r for r in results[0]["match_reasons"])
    # naming a city makes it a hard filter, not just a ranking signal
    albany = buddies.search_posts("searcher", activity="swimming", city="Albany")
    assert {p["city"] for p in albany} == {"Albany"}
    assert any("nearby" in r for r in albany[0]["match_reasons"])


def test_match_score_is_deterministic_pure_function():
    post = {"activity": "swimming", "city": "Albany", "area": "",
            "time_window": "weekend", "skill_level": "beginner",
            "budget": "low", "vibe": "casual"}
    query = {"activity": "swimming", "city": "albany", "time_window": "weekend",
             "skill_level": "beginner", "budget": "low", "vibe": "casual"}
    score, reasons = buddies.match_score(post, query)
    assert score == 9 and len(reasons) == 6
    assert buddies.match_score(post, query) == (score, reasons)


def test_activity_filter_is_hard():
    _post(user_id="u1", activity="knitting", city="Albany")
    assert buddies.search_posts("searcher", activity="swimming") == []


def test_cannot_request_own_post():
    post = _post(user_id="me")
    with pytest.raises(ValueError):
        buddies.create_request("me", post["id"], "let me in")


def test_duplicate_pending_request_rejected():
    post = _post(user_id="owner")
    buddies.create_request("joiner", post["id"], "hi")
    with pytest.raises(ValueError):
        buddies.create_request("joiner", post["id"], "hi again")


def test_only_post_owner_can_decide_request():
    post = _post(user_id="owner")
    req = buddies.create_request("joiner", post["id"], "hi")
    with pytest.raises(PermissionError):
        buddies.decide_request("joiner", req["id"], "accepted")   # requester
    with pytest.raises(PermissionError):
        buddies.decide_request("random", req["id"], "accepted")   # bystander
    out = buddies.decide_request("owner", req["id"], "accepted")
    assert out["status"] == "accepted"


def test_only_owner_can_update_post():
    post = _post(user_id="owner")
    with pytest.raises(PermissionError):
        buddies.update_post("intruder", post["id"], {"active": False})
    updated = buddies.update_post("owner", post["id"], {"active": False})
    assert updated["active"] is False


def test_requests_views_never_leak_user_ids():
    post = _post(user_id="anon-" + "c" * 32)
    buddies.create_request("anon-" + "d" * 32, post["id"], "hello",
                           requester_name="Morning swimmer")
    incoming = buddies.my_requests("anon-" + "c" * 32)["incoming"]
    outgoing = buddies.my_requests("anon-" + "d" * 32)["outgoing"]
    blob = str(incoming) + str(outgoing)
    assert "c" * 32 not in blob and "d" * 32 not in blob
    assert incoming[0]["requester_name"] == "Morning swimmer"


def test_blocked_users_vanish_from_search_both_directions():
    poster = _post(user_id="poster")
    buddies.block_user("searcher", poster["owner_key"])
    assert buddies.search_posts("searcher", activity="swimming") == []
    # reverse: the poster blocks the searcher → searcher no longer sees them
    _post(user_id="poster2", display_name="Other")
    searcher_key = buddies.public_user_key("searcher2")
    buddies.block_user("poster2", searcher_key)
    names = {p["display_name"] for p in
             buddies.search_posts("searcher2", activity="swimming")}
    assert "Other" not in names


def test_requester_who_blocked_owner_cannot_request():
    post = _post(user_id="owner")
    buddies.block_user("joiner", post["owner_key"])
    with pytest.raises(ValueError):
        buddies.create_request("joiner", post["id"], "hi")
    assert buddies.my_requests("owner")["incoming"] == []   # nothing inserted


def test_owner_block_makes_request_a_plain_404():
    post = _post(user_id="owner")
    buddies.block_user("owner", buddies.public_user_key("joiner"))
    with pytest.raises(LookupError):   # not PermissionError: block undisclosed
        buddies.create_request("joiner", post["id"], "hi")
    assert buddies.my_requests("owner")["incoming"] == []


def test_create_post_rejects_blank_required_fields_after_scrub():
    with pytest.raises(ValueError, match="display_name"):
        _post(display_name="   ")
    with pytest.raises(ValueError, match="activity"):
        _post(activity="  ")
    with pytest.raises(ValueError, match="city"):
        _post(city="\t ")


def test_update_post_rejects_blank_required_fields_after_scrub():
    post = _post(user_id="owner")
    for field in ("display_name", "activity", "city"):
        with pytest.raises(ValueError, match=field):
            buddies.update_post("owner", post["id"], {field: "   "})
    # the post is untouched, and optional fields may still be cleared
    unchanged = buddies.get_own_post("owner", post["id"])
    assert unchanged["display_name"] == "Swim Sam"
    cleared = buddies.update_post("owner", post["id"], {"notes": "  "})
    assert cleared["notes"] == ""


def test_report_records_and_validates_post():
    post = _post(user_id="owner")
    assert buddies.report_post("someone", post["id"], "spam")["reported"] == post["id"]
    with pytest.raises(LookupError):
        buddies.report_post("someone", 99999, "spam")


def test_block_rejects_bad_keys_and_self():
    with pytest.raises(ValueError):
        buddies.block_user("me", "../../etc/passwd")
    with pytest.raises(ValueError):
        buddies.block_user("me", buddies.public_user_key("me"))


# ---------- Layer 2: through the real app ----------

def _client():
    pytest.importorskip("langchain_google_vertexai")
    from fastapi.testclient import TestClient
    import vital.api as api
    return TestClient(api.app)


BODY = {"display_name": "Swim Sam", "activity": "swimming", "city": "Albany",
        "time_window": "weekend", "vibe": "casual", "skill_level": "beginner",
        "budget": "low", "group_size": "2-4", "notes": "lap swims"}


def test_anonymous_identity_owns_created_post_and_fake_user_id_is_ignored():
    client = _client()
    r = client.post("/activity-posts", json={**BODY, "user_id": "victim"})
    assert r.status_code == 200
    post = r.json()["post"]
    assert post["mine"] is True
    # same session sees it under /mine; the body's user_id changed nothing
    mine = client.get("/activity-posts/mine").json()["posts"]
    assert [p["id"] for p in mine] == [post["id"]]
    assert "victim" not in str(mine) and "user_id" not in str(mine)


def test_other_session_cannot_update_or_see_someone_elses_post_as_own():
    client_a, client_b = _client(), _client()
    post = client_a.post("/activity-posts", json=BODY).json()["post"]
    assert client_b.get("/activity-posts/mine").json()["posts"] == []
    r = client_b.patch(f"/activity-posts/{post['id']}", json={"active": False})
    assert r.status_code == 403
    # and the post is still active for searchers
    found = client_b.get("/activity-posts", params={"activity": "swimming"}).json()
    assert [p["id"] for p in found["posts"]] == [post["id"]]


def test_search_route_returns_others_posts_without_private_data():
    client_a, client_b = _client(), _client()
    client_a.post("/activity-posts",
                  json={**BODY, "notes": "call 518-555-0123"})
    body = client_b.get("/activity-posts", params={"activity": "swim"}).json()
    assert len(body["posts"]) == 1
    assert "safety_note" in body and "public places" in body["safety_note"]
    blob = str(body)
    assert "user_id" not in blob and "anon-" not in blob
    # Match the whole number, not the "518" fragment: owner_key is 16 hex
    # chars, so ~0.3% of runs contain "518" by chance and the old assertion
    # failed at random (roughly 1 CI run in 300).
    assert "518-555-0123" not in blob and "5185550123" not in blob
    assert "518-555" not in body["posts"][0].get("notes", "")


def test_inactive_posts_hidden_from_search_route():
    client_a, client_b = _client(), _client()
    post = client_a.post("/activity-posts", json=BODY).json()["post"]
    client_a.patch(f"/activity-posts/{post['id']}", json={"active": False})
    assert client_b.get("/activity-posts").json()["posts"] == []


def test_request_flow_owner_decides_requester_cannot():
    client_a, client_b = _client(), _client()
    post = client_a.post("/activity-posts", json=BODY).json()["post"]
    own = client_a.post(f"/activity-posts/{post['id']}/request", json={"message": "me!"})
    assert own.status_code == 409          # can't request own post
    r = client_b.post(f"/activity-posts/{post['id']}/request",
                      json={"message": "up for laps", "requester_name": "Lane 4"})
    assert r.status_code == 200
    req_id = r.json()["request"]["id"]
    assert client_b.patch(f"/activity-requests/{req_id}",
                          json={"status": "accepted"}).status_code == 403
    ok = client_a.patch(f"/activity-requests/{req_id}", json={"status": "accepted"})
    assert ok.json()["request"]["status"] == "accepted"
    outgoing = client_b.get("/activity-requests/mine").json()["outgoing"]
    assert outgoing[0]["status"] == "accepted"


def test_create_route_rejects_whitespace_only_required_fields():
    # pydantic min_length can't catch "   "; the domain check must, as HTTP
    client = _client()
    r = client.post("/activity-posts", json={**BODY, "city": "   "})
    assert r.status_code == 409
    assert "city" in r.json()["detail"]
    assert client.get("/activity-posts/mine").json()["posts"] == []


def test_update_route_rejects_whitespace_only_required_fields():
    client = _client()
    post = client.post("/activity-posts", json=BODY).json()["post"]
    r = client.patch(f"/activity-posts/{post['id']}", json={"activity": "   "})
    assert r.status_code == 409
    assert "activity" in r.json()["detail"]
    (mine,) = client.get("/activity-posts/mine").json()["posts"]
    assert mine["activity"] == "swimming"   # PATCH changed nothing


def test_street_address_never_reaches_search_payload():
    client_a, client_b = _client(), _client()
    client_a.post("/activity-posts",
                  json={**BODY, "notes": "I live at 123 Main St, come by"})
    body = client_b.get("/activity-posts", params={"activity": "swim"}).json()
    assert "Main St" not in str(body)


def test_request_route_respects_blocks_both_ways():
    from vital.security import SESSION_COOKIE

    client_a, client_b = _client(), _client()
    post = client_a.post("/activity-posts", json=BODY).json()["post"]
    client_b.get("/activity-posts")            # issues b's session cookie
    b_key = buddies.public_user_key(f"anon-{client_b.cookies[SESSION_COOKIE]}")

    # owner blocks the would-be requester → request reads as a plain 404
    assert client_a.post(f"/users/{b_key}/block", json={}).status_code == 200
    r = client_b.post(f"/activity-posts/{post['id']}/request", json={"message": "hi"})
    assert r.status_code == 404
    assert client_a.get("/activity-requests/mine").json()["incoming"] == []

    # requester blocks the owner → their own request is refused as 409
    client_c = _client()
    client_c.get("/activity-posts")
    assert client_c.post(f"/users/{post['owner_key']}/block", json={}).status_code == 200
    r2 = client_c.post(f"/activity-posts/{post['id']}/request", json={"message": "hi"})
    assert r2.status_code == 409
    assert client_a.get("/activity-requests/mine").json()["incoming"] == []


def test_report_and_block_routes():
    client_a, client_b = _client(), _client()
    post = client_a.post("/activity-posts", json=BODY).json()["post"]
    assert client_b.post(f"/activity-posts/{post['id']}/report",
                         json={"reason": "spam"}).status_code == 200
    assert client_b.post(f"/users/{post['owner_key']}/block", json={}).status_code == 200
    assert client_b.get("/activity-posts").json()["posts"] == []


# ---------- Layer 3: the agent tool ----------

def test_tool_returns_structured_matches_only_from_storage():
    from vital import storage
    from vital.agents.people_connector import find_activity_buddies

    _post(user_id="other-user")
    storage.current_user_id.set("tool-user")
    out = find_activity_buddies.invoke({"activity": "swimming", "city": "Albany"})
    assert out["count"] == 1
    (m,) = out["matches"]
    assert m["display_name"] == "Swim Sam" and m["match_reasons"]
    assert "user_id" not in m
    # empty board → empty matches, never invented people
    out2 = find_activity_buddies.invoke({"activity": "fencing"})
    assert out2["matches"] == [] and out2["count"] == 0


def test_tool_excludes_own_posts():
    from vital import storage
    from vital.agents.people_connector import find_activity_buddies

    storage.current_user_id.set("self-poster")
    _post(user_id="self-poster")
    out = find_activity_buddies.invoke({"activity": "swimming"})
    assert out["matches"] == []


def test_tool_degrades_gracefully_on_storage_failure(monkeypatch):
    from vital.agents import people_connector

    def boom(*_a, **_k):
        raise RuntimeError("db is gone")
    monkeypatch.setattr(people_connector.buddies, "search_posts", boom)
    out = people_connector.find_activity_buddies.invoke({"activity": "swimming"})
    assert "error" in out and "unavailable" in out["error"]
    assert "matches" not in out


# ---------- connections: the record that outlives the post ----------

def test_accepting_creates_a_connection():
    post = _post(user_id="owner", activity="swimming")
    req = buddies.create_request("joiner", post["id"], "keen!",
                                 requester_name="Sharayu")
    buddies.decide_request("owner", req["id"], "accepted")

    for who, expect in (("owner", "Sharayu"), ("joiner", "Swim Sam")):
        found = buddies.my_connections(who)
        assert len(found) == 1, f"{who} has no connection"
        assert found[0]["name"] == expect      # each side sees the OTHER
        assert found[0]["activity"] == "swimming"
        assert found[0]["status"] == "active"


def test_rejecting_does_not_create_a_connection():
    post = _post(user_id="owner")
    req = buddies.create_request("joiner", post["id"], "hi")
    buddies.decide_request("owner", req["id"], "rejected")
    assert buddies.my_connections("owner") == []
    assert buddies.my_connections("joiner") == []


def test_a_connection_survives_the_post_being_deleted():
    """THE reason this table exists.

    An accepted match used to be a row in activity_requests pointing at a
    post. Deactivate or delete that post and the connection went with it —
    the activity, the other person's name, the fact it ever happened. You
    could agree to swim with somebody on Monday and by Friday have no record
    that either of you existed to the other.

    The columns are snapshots, not joins, so this passes with the post row
    physically gone. If someone later "optimises" them into a JOIN back onto
    activity_posts, this is the test that stops it.
    """
    post = _post(user_id="owner", activity="swimming")
    req = buddies.create_request("joiner", post["id"], "keen!",
                                 requester_name="Sharayu")
    buddies.decide_request("owner", req["id"], "accepted")

    with storage._conn() as c:                       # the harshest case
        c.execute("DELETE FROM activity_posts WHERE id = ?", (post["id"],))
        c.execute("DELETE FROM activity_requests WHERE id = ?", (req["id"],))

    still_there = buddies.my_connections("joiner")
    assert len(still_there) == 1
    assert still_there[0]["name"] == "Swim Sam"
    assert still_there[0]["activity"] == "swimming"


def test_one_pair_is_one_connection_whichever_way_round_it_started():
    """Canonical ordering. Without it the same two people produce two rows
    depending on who posted and who asked, and every 'are we already
    connected' check has to be written twice."""
    first = _post(user_id="alice", activity="swimming")
    req = buddies.create_request("bob", first["id"], "hi")
    buddies.decide_request("alice", req["id"], "accepted")

    # now the other way round, same activity
    second = _post(user_id="bob", activity="swimming")
    req2 = buddies.create_request("alice", second["id"], "hi back")
    buddies.decide_request("bob", req2["id"], "accepted")

    assert len(buddies.my_connections("alice")) == 1
    assert len(buddies.my_connections("bob")) == 1


def test_the_same_pair_can_connect_over_a_different_activity():
    """Distinct relationships, not a duplicate. Swimming with someone says
    nothing about climbing with them."""
    swim = _post(user_id="alice", activity="swimming")
    buddies.decide_request(
        "alice", buddies.create_request("bob", swim["id"], "hi")["id"], "accepted")
    climb = _post(user_id="alice", activity="climbing")
    buddies.decide_request(
        "alice", buddies.create_request("bob", climb["id"], "hi")["id"], "accepted")

    activities = sorted(c["activity"] for c in buddies.my_connections("alice"))
    assert activities == ["climbing", "swimming"]


def test_either_side_can_end_a_connection_and_a_stranger_cannot():
    post = _post(user_id="owner", activity="swimming")
    req = buddies.create_request("joiner", post["id"], "hi")
    buddies.decide_request("owner", req["id"], "accepted")
    connection = buddies.my_connections("joiner")[0]

    with pytest.raises(LookupError):                 # not part of this pair
        buddies.end_connection("bystander", connection["id"])

    buddies.end_connection("joiner", connection["id"])   # the REQUESTER ends it
    assert buddies.my_connections("joiner") == []
    assert buddies.my_connections("owner") == []         # gone for both sides
    assert len(buddies.my_connections("owner", include_ended=True)) == 1


def test_reconnecting_revives_the_relationship_rather_than_duplicating_it():
    """Two people who stopped and later started again are the same
    relationship. A second row would make the history unreadable and let
    somebody accumulate connections by cycling one."""
    post = _post(user_id="owner", activity="swimming")
    req = buddies.create_request("joiner", post["id"], "hi")
    buddies.decide_request("owner", req["id"], "accepted")
    buddies.end_connection("joiner", buddies.my_connections("joiner")[0]["id"])

    again = _post(user_id="owner", activity="swimming")
    req2 = buddies.create_request("joiner", again["id"], "back again")
    buddies.decide_request("owner", req2["id"], "accepted")

    revived = buddies.my_connections("joiner")
    assert len(revived) == 1 and revived[0]["status"] == "active"
    assert len(buddies.my_connections("joiner", include_ended=True)) == 1


def test_a_connection_never_exposes_a_raw_user_id():
    post = _post(user_id="owner-session-token", activity="swimming")
    req = buddies.create_request("joiner-session-token", post["id"], "hi")
    buddies.decide_request("owner-session-token", req["id"], "accepted")

    shown = buddies.my_connections("owner-session-token")[0]
    assert "joiner-session-token" not in repr(shown)
    assert shown["member_key"] == buddies.public_user_key("joiner-session-token")


def test_blocking_removes_a_connection_from_both_directions():
    """Blocking has to clear every surface. A block that hides somebody from
    search but leaves them in your connections list is a setting, not a
    protection."""
    post = _post(user_id="owner", activity="swimming")
    req = buddies.create_request("joiner", post["id"], "hi")
    buddies.decide_request("owner", req["id"], "accepted")

    buddies.block_user("owner", buddies.public_user_key("joiner"))
    assert buddies.my_connections("owner") == []      # blocker cannot see them
    assert buddies.my_connections("joiner") == []     # nor they the blocker


def test_accepting_over_the_api_gives_both_sides_a_connection():
    """End to end through the routes the panel actually calls."""
    owner, joiner = _client(), _client()
    post = owner.post("/activity-posts", json=BODY).json()["post"]
    joiner.post(f"/activity-posts/{post['id']}/request",
                json={"message": "keen!", "requester_name": "Sharayu"})

    request_id = owner.get("/activity-requests/mine").json()["incoming"][0]["id"]
    owner.patch(f"/activity-requests/{request_id}", json={"status": "accepted"})

    for client, expect in ((owner, "Sharayu"), (joiner, "Swim Sam")):
        found = client.get("/buddy-connections").json()["connections"]
        assert [c["name"] for c in found] == [expect]
        assert found[0]["activity"] == "swimming"


def test_a_stranger_cannot_end_someone_elses_connection():
    owner, joiner, stranger = _client(), _client(), _client()
    post = owner.post("/activity-posts", json=BODY).json()["post"]
    joiner.post(f"/activity-posts/{post['id']}/request", json={"message": "hi"})
    request_id = owner.get("/activity-requests/mine").json()["incoming"][0]["id"]
    owner.patch(f"/activity-requests/{request_id}", json={"status": "accepted"})
    connection_id = owner.get("/buddy-connections").json()["connections"][0]["id"]

    stranger.get("/activity-posts")                      # establish a session
    assert stranger.delete(f"/buddy-connections/{connection_id}").status_code == 404
    assert owner.get("/buddy-connections").json()["connections"] != []

    assert joiner.delete(f"/buddy-connections/{connection_id}").status_code == 200
    assert owner.get("/buddy-connections").json()["connections"] == []
