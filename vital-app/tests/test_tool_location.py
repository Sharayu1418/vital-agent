"""The tools use the coordinates we already have, not a name the model typed.

THE BUG THIS CLOSES
-------------------
test_location_context.py closed half of this. Location now travels with every
request and is stated to the agent as authoritative, so the model is TOLD
where the user is.

But the tools took `city: str`. So the pipeline was:

    browser  ->  lat 42.6526, lng -73.7562   (4dp, about 11 m)
             ->  stringified into a system message
    model    ->  get_weather(city="Albany")
             ->  OpenWeather ?q=Albany
    result   ->  the weather at the city centroid

Three separate losses in one round trip:

  PRECISION. A city name resolves to a centroid. Albany is 56 km2; someone
    on the edge of it gets conditions from somewhere else, and venue search
    gets biased at the wrong point by several kilometres.

  RELIABILITY. `set_location` falls back to "42.65, -73.76" as the label when
    the geocoder returns nothing. The prompt then says the user's location is
    that string, the model passes it as `city`, and OpenWeather's q= accepts
    only names. That path cannot succeed — it is an error, not a degradation.

  AUTHORITY. The model chooses the argument. Memory legitimately holds "User
    is in Albany" from March; the device says London today. The prompt states
    the precedence, but a prompt is a request and a signature is a guarantee.

The fix is not to forbid the model naming a city — "what's the weather in
Tokyo?" is a real question. It is to make OMITTING the city mean "where they
actually are", and to treat a city that merely echoes our own label as the
same request.
"""
import os

os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "test")
os.environ.setdefault("OPENWEATHER_API_KEY", "test")
os.environ.setdefault("GOOGLE_PLACES_API_KEY", "test")

import httpx
import pytest
import respx

from vital import storage
from vital.tools.places import search_places
from vital.tools.weather import get_weather


@pytest.fixture(autouse=True)
def clear_location():
    token = storage.current_location.set(None)
    yield
    storage.current_location.reset(token)


def _mock_weather():
    """A minimal valid OpenWeather pair. `name` is what the provider calls
    the point we asked about — with coordinates it is the neighbourhood,
    which is more specific than anything we could have sent it."""
    respx.get(url__regex=r".*/weather.*").mock(return_value=httpx.Response(
        200, json={"main": {"temp": 18.0, "feels_like": 17.0},
                   "weather": [{"description": "clear sky"}],
                   "name": "Pine Hills"}))
    respx.get(url__regex=r".*/forecast.*").mock(return_value=httpx.Response(
        200, json={"list": [{"pop": 0.1}]}))


def _sent(route):
    """Query parameters of the first request matched by a respx route."""
    return dict(route.calls[0].request.url.params)


# ---------- weather goes to the point, not the placename ----------

@respx.mock
def test_weather_with_no_city_uses_the_device_coordinates():
    """THE test. The model should be able to say nothing about location and
    still get the right answer, because the server already knows."""
    storage.set_location("42.6526", "-73.7562", "Albany, New York")
    route = respx.get(url__regex=r".*/weather.*")
    _mock_weather()

    out = get_weather.invoke({})

    assert "error" not in out
    params = _sent(route)
    assert params["lat"] == "42.6526" and params["lon"] == "-73.7562"
    assert "q" not in params, "sent a placename while holding coordinates"


@respx.mock
def test_a_city_that_echoes_our_own_label_still_uses_coordinates():
    """The model is told 'your location is Albany, New York' and transcribes
    it back into the call. That is correct behaviour and it is still lossy,
    so the tool upgrades it rather than honouring it literally."""
    storage.set_location("42.6526", "-73.7562", "Albany, New York")
    route = respx.get(url__regex=r".*/weather.*")
    _mock_weather()

    for echo in ["Albany", "albany", "Albany, New York", "Albany, NY"]:
        route.reset()
        get_weather.invoke({"city": echo})
        assert "lat" in _sent(route), f"{echo!r} was treated as somewhere else"


@respx.mock
def test_a_genuinely_different_city_is_honoured():
    """Not everything is a question about here. 'What's the weather in
    Tokyo' must not be silently answered about Albany — that is the same
    class of error in the other direction, and worse because it is invisible."""
    storage.set_location("42.6526", "-73.7562", "Albany, New York")
    route = respx.get(url__regex=r".*/weather.*")
    _mock_weather()

    get_weather.invoke({"city": "Tokyo"})

    params = _sent(route)
    assert params.get("q") == "Tokyo"
    assert "lat" not in params


@respx.mock
def test_a_coordinate_label_is_never_sent_as_a_placename():
    """set_location's fallback label IS a coordinate string. Passing it to
    q= cannot work, so this path was a guaranteed failure whenever the
    geocoder returned nothing."""
    storage.set_location("42.6526", "-73.7562", "")
    assert "," in storage.current_location.get()["label"]  # the fallback
    route = respx.get(url__regex=r".*/weather.*")
    _mock_weather()

    get_weather.invoke({"city": storage.current_location.get()["label"]})

    params = _sent(route)
    assert "q" not in params, "sent a lat/lng pair to a name-only endpoint"
    assert params["lat"] == "42.6526"


@respx.mock
def test_the_report_names_the_place_the_provider_resolved():
    """With coordinates the provider knows the neighbourhood. Echoing our own
    label back would throw that away and would also relabel Tokyo's weather
    with the user's city if the two ever got crossed."""
    storage.set_location("42.6526", "-73.7562", "Albany, New York")
    _mock_weather()

    assert get_weather.invoke({})["city"] == "Pine Hills"


@respx.mock
def test_no_location_and_no_city_asks_rather_than_guessing():
    """The old signature made `city` required, so the model always supplied
    something — including, sometimes, a city it had inferred. An explicit
    'I don't know where you are' is a better input to the next turn than a
    confident answer about the wrong place."""
    out = get_weather.invoke({})
    assert "error" in out
    assert "location" in out["error"].lower()


@respx.mock
def test_a_named_city_works_with_no_device_location_at_all():
    """Geolocation denied, or a question about somewhere else entirely. The
    name path has to keep working — this is the pre-existing behaviour and
    nothing about coordinates should remove it."""
    route = respx.get(url__regex=r".*/weather.*")
    _mock_weather()

    out = get_weather.invoke({"city": "Brooklyn"})

    assert "error" not in out
    assert _sent(route).get("q") == "Brooklyn"


# ---------- venue search is biased at the point, not the centroid ----------

def _mock_places():
    return respx.post(url__regex=r".*searchText.*").mock(
        return_value=httpx.Response(200, json={"places": [{
            "displayName": {"text": "Albany Indoor Rock Gym"},
            "rating": 4.6,
            "formattedAddress": "4C Vatrano Rd, Albany, NY",
            "googleMapsUri": "https://maps.google.com/?cid=1",
            "priceLevel": "PRICE_LEVEL_MODERATE",
        }]}))


@respx.mock
def test_places_with_no_city_biases_at_the_device_coordinates():
    storage.set_location("42.6526", "-73.7562", "Albany, New York")
    route = _mock_places()

    out = search_places.invoke({"query": "bouldering gym"})

    assert "error" not in out
    body = route.calls[0].request.read().decode()
    assert '"latitude":42.6526' in body.replace(" ", "")
    assert "in Albany" not in body, "pasted a placename into the text query"


@respx.mock
def test_places_honours_a_genuinely_different_city():
    storage.set_location("42.6526", "-73.7562", "Albany, New York")
    route = _mock_places()

    search_places.invoke({"query": "ramen", "city": "Tokyo"})

    body = route.calls[0].request.read().decode()
    assert "Tokyo" in body
    assert "latitude" not in body


@respx.mock
def test_places_still_works_from_a_name_alone():
    route = _mock_places()
    out = search_places.invoke({"query": "bouldering gym", "city": "Brooklyn"})
    assert out["venues"][0]["name"] == "Albany Indoor Rock Gym"
    assert "Brooklyn" in route.calls[0].request.read().decode()


@respx.mock
def test_places_with_neither_asks_rather_than_searching_the_planet():
    """An unbiased global text search for 'bouldering gym' returns somewhere
    in another hemisphere, presented with the same confidence as a good
    answer. Saying we don't know is the honest failure."""
    out = search_places.invoke({"query": "bouldering gym"})
    assert "error" in out and "venues" not in out


# ---------- the same-place rule, which decides everything above ----------

def test_the_echo_rule_matches_administrative_tails_but_not_neighbours():
    """The whole design rests on this one predicate, and both directions of
    error are silent: too loose answers about Tokyo using Albany's
    coordinates, too tight quietly loses the precision the fix exists for."""
    from vital.tools.where import _same_place

    label = "Albany, New York"
    for echo in ["Albany", "albany", "ALBANY", "Albany, NY", "Albany, New York",
                 "Albany, New York, USA", " Albany "]:
        assert _same_place(echo, label), f"{echo!r} should echo {label!r}"

    for other in ["Tokyo", "New York", "Albion", "Los Angeles", "", "USA"]:
        assert not _same_place(other, label), f"{other!r} should NOT echo {label!r}"


def test_a_shared_word_is_not_a_shared_place():
    """'York' is inside 'New York' as a substring and is a different city on
    a different continent. A plain `in` test merged them; the comparison is
    token-prefix for exactly this case."""
    from vital.tools.where import _same_place

    assert not _same_place("York", "New York")
    assert not _same_place("York, England", "New York, NY")
    assert _same_place("New York", "New York, NY")


def test_resolve_prefers_the_stored_fix_over_a_reparsed_label():
    """The fallback label prints 2dp (~1.1 km); the contextvar holds 4dp
    (~11 m). Parsing the label back would throw away two decimal places we
    never actually lost."""
    from vital.tools import where as where_mod

    storage.set_location("42.6526", "-73.7562", "")
    place = where_mod.resolve(storage.current_location.get()["label"])
    assert (place.lat, place.lng) == (42.6526, -73.7562)


def test_an_out_of_range_coordinate_string_is_not_searched_for():
    """'999, 999' is neither a place nor a point. With a device fix present
    the honest reading is 'the model said something useless', so fall back to
    the fix rather than sending the string onward."""
    from vital.tools import where as where_mod

    storage.set_location("42.6526", "-73.7562", "Albany, New York")
    place = where_mod.resolve("999, 999")
    assert place.source == "device" and place.lat == 42.6526


# ---------- the ranked path's own no-location fallback ----------

@respx.mock
def test_unranked_fallback_does_not_invent_a_place_called_nearby():
    """recommend.find_activities passed `city or "nearby"`, directly under a
    comment promising it would not invent a location. Places has no idea what
    "nearby" is, so the text query "<query> in nearby" ran an unbiased global
    search and the results came back looking local."""
    from vital import recommend

    route = _mock_places()
    out = recommend.find_activities(
        "bouldering gym", user_id="u1", predicted_energy=0.6,
        lat=None, lng=None, city="")

    assert "error" in out, "searched the planet instead of admitting it cannot"
    assert not route.calls, "a request was sent with no location at all"


# ---------- the model must be told what omitting the city means ----------

def test_the_docstrings_tell_the_model_to_omit_the_city_when_its_here():
    """A parameter that is optional in the signature and undocumented in the
    description is a parameter the model will fill in anyway. The docstring
    IS the spec it sees; this is the only place the new behaviour exists as
    far as the model is concerned."""
    for tool in (get_weather, search_places):
        text = " ".join(tool.description.lower().split())
        assert "omit" in text, (
            f"{tool.name}: nothing tells the model that leaving city out "
            "means 'where the user actually is'")
        assert "different" in text or "another" in text or "else" in text, (
            f"{tool.name}: nothing tells the model when passing city IS right")
