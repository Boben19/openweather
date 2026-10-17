"""Tests for the weather app.

All tests here use MOCKED OpenWeather responses (requests.get is patched), so they run offline and
need no API key. They verify this app's behaviour, not OpenWeather's live service.
"""
import json
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import requests
from django.contrib.staticfiles import finders
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils.html import escape
from django.urls import reverse

from . import services

OFFSET = 28800  # Manila, UTC+8


def ts(day, hour, minute=0):
    return int(datetime(2026, 10, day, hour, minute, tzinfo=timezone.utc).timestamp())


def current_payload(**overrides):
    data = {
        "coord": {"lon": 120.9842, "lat": 14.6042},
        "weather": [{"id": 802, "main": "Clouds", "description": "scattered clouds", "icon": "03d"}],
        "main": {"temp": 30.46, "feels_like": 34.5, "pressure": 1009, "humidity": 70},
        "visibility": 8000,
        "wind": {"speed": 3.6, "deg": 200, "gust": 6.2},
        "clouds": {"all": 40},
        "dt": ts(17, 6, 10),                       # 14:10 in Manila
        "sys": {"country": "PH", "sunrise": ts(16, 21, 38), "sunset": ts(17, 9, 39)},
        "timezone": OFFSET,
        "name": "Manila",
        "cod": 200,
    }
    data.update(overrides)
    return data


def forecast_payload(slots=40, first=(17, 9)):
    """40 three-hour slots starting 17:00 local on 17 Oct (so 'today' has only 3 slots)."""
    start = ts(*first)
    items = []
    for i in range(slots):
        items.append({
            "dt": start + i * 10800,
            "main": {"temp": 25.0 + (i % 8), "feels_like": 27, "humidity": 80},
            "weather": [{"id": 500, "main": "Rain", "description": "light rain", "icon": "10n" if i % 8 in (0, 7) else "10d"}],
            "pop": 0.1 * (i % 8) / 2,
        })
    return {"cod": "200", "cnt": slots, "list": items, "city": {"name": "Manila", "timezone": OFFSET}}


def response(data=None, status=200, json_error=False):
    r = Mock()
    r.status_code = status
    if json_error:
        r.json.side_effect = ValueError("not json")
    else:
        r.json.return_value = data
    return r


def router(current=None, forecast=None):
    """Return a requests.get replacement that answers per endpoint and records calls."""
    answers = {"weather": current, "forecast": forecast}

    def fake_get(url, params=None, timeout=None):
        fake_get.calls.append((url, dict(params or {}), timeout))
        answer = answers["forecast" if url.endswith("/forecast") else "weather"]
        if isinstance(answer, Exception) or (isinstance(answer, type) and issubclass(answer, Exception)):
            raise answer
        return answer
    fake_get.calls = []
    return fake_get


def ok(**kw):
    return router(response(current_payload(**kw)), response(forecast_payload()))


@override_settings(OPENWEATHER_API_KEY="test-key-123", WEATHER_CACHE_SECONDS=600, DEFAULT_CITY="Puerto Princesa")
class ViewTestCase(TestCase):
    def setUp(self):
        cache.clear()

    def get(self, fake, **params):
        with patch("weather.services.requests.get", fake):
            return self.client.get(reverse("weather"), params)


# ----------------------------------------------------------------------------
# Input validation (pure functions)
# ----------------------------------------------------------------------------
class ValidationTests(SimpleTestCase):
    def test_city_is_trimmed_and_whitespace_collapsed(self):
        self.assertEqual(services.parse_city("  San   José  ").city, "San José")

    def test_city_accepts_country_code_and_punctuation(self):
        for value in ["Paris, FR", "O'Fallon", "Winston-Salem", "St. Louis", "Zürich", "São Paulo", "東京"]:
            self.assertEqual(services.parse_city(value).city, value)

    def test_city_rejects_bad_input(self):
        for value in ["", "   ", None, "x" * 81, "<script>alert(1)</script>", "Paris; DROP TABLE", "12345", "...", "a&b=c", "a\nb\x00"]:
            with self.subTest(value=value), self.assertRaises(services.InvalidInput):
                services.parse_city(value)

    def test_city_at_max_length_is_accepted(self):
        self.assertEqual(len(services.parse_city("a" * 80).city), 80)

    def test_coordinates_valid(self):
        q = services.parse_coordinates("10.3157", "123.8854")
        self.assertTrue(q.by_coordinates)
        self.assertEqual(q.params(), {"lat": 10.3157, "lon": 123.8854})

    def test_coordinate_boundaries(self):
        self.assertEqual(services.parse_coordinates("-90", "180").params(), {"lat": -90.0, "lon": 180.0})
        for lat, lon in [("90.01", "0"), ("-90.5", "0"), ("0", "180.1"), ("0", "-181")]:
            with self.subTest(lat=lat, lon=lon), self.assertRaises(services.InvalidInput):
                services.parse_coordinates(lat, lon)

    def test_coordinates_reject_non_numbers_and_missing(self):
        for lat, lon in [("abc", "1"), ("1", "x"), ("nan", "1"), ("1", "inf"), ("1e2", "1"), ("", "1"), ("1", ""), (None, "1"), ("1", None), ("1,5", "2")]:
            with self.subTest(lat=lat, lon=lon), self.assertRaises(services.InvalidInput):
                services.parse_coordinates(lat, lon)

    def test_units_fall_back_to_metric(self):
        self.assertEqual(services.normalize_units("imperial"), "imperial")
        for value in [None, "", "kelvin", "IMPERIAL", "metric; x"]:
            self.assertEqual(services.normalize_units(value), "metric")


# ----------------------------------------------------------------------------
# Parsing (pure functions)
# ----------------------------------------------------------------------------
class ParsingTests(SimpleTestCase):
    def test_current_values_units_and_local_times(self):
        parsed = services.parse_current(current_payload(), "metric")
        c, loc = parsed["current"], parsed["location"]
        self.assertEqual(loc["label"], "Manila, PH")
        self.assertEqual(loc["tz_offset"], OFFSET)
        self.assertEqual((c["temp"], c["feels_like"], c["humidity"], c["pressure"]), (30, 35, 70, 1009))
        self.assertEqual(c["wind_speed"], "3.6")
        self.assertEqual(c["wind_dir"], "SSW")
        self.assertEqual(c["wind_gust"], "6.2")
        self.assertEqual(c["visibility"], "8")           # 8000 m -> 8 km
        self.assertEqual(c["description"], "Scattered clouds")
        self.assertEqual(c["observed_time"], "2:10 PM")   # 06:10 UTC + 8h
        self.assertEqual(c["sunrise"], "5:38 AM")         # 21:38 UTC + 8h, next day
        self.assertEqual(c["sunset"], "5:39 PM")
        self.assertFalse(c["is_night"])

    def test_imperial_visibility_is_converted_to_miles(self):
        c = services.parse_current(current_payload(visibility=8047), "imperial")["current"]
        self.assertEqual(c["visibility"], "5")
        self.assertEqual(services.parse_current(current_payload(visibility=10000), "metric")["current"]["visibility"], "10+")

    def test_temperature_rounds_half_up_and_never_shows_negative_zero(self):
        main = {"temp": 2.5, "feels_like": -0.4, "humidity": 50, "pressure": 1000}
        c = services.parse_current(current_payload(main=main), "metric")["current"]
        self.assertEqual((c["temp"], c["feels_like"]), (3, 0))

    def test_optional_fields_may_be_missing(self):
        data = current_payload()
        for key in ("visibility", "wind", "clouds"):
            data.pop(key)
        data["sys"] = {"country": "PH"}
        data["main"] = {"temp": 20, "feels_like": 20}
        c = services.parse_current(data, "metric")["current"]
        for field in ("visibility", "wind_speed", "wind_dir", "wind_gust", "clouds", "humidity", "pressure", "sunrise", "sunset"):
            self.assertIsNone(c[field], field)

    def test_unnamed_place_falls_back_to_coordinates(self):
        label = services.parse_current(current_payload(name="", sys={"country": ""}), "metric")["location"]["label"]
        self.assertEqual(label, "14.60°, 120.98°")

    def test_malformed_current_payloads_raise(self):
        bad = [
            {}, {"main": {"temp": 1}},
            current_payload(main={"temp": "hot", "feels_like": 1}),
            current_payload(main={"temp": float("nan"), "feels_like": 1}),
            current_payload(main={"temp": True, "feels_like": 1}),
            current_payload(weather=[]), current_payload(weather=None),
            current_payload(coord={"lat": 1}), current_payload(dt=None), current_payload(wind="fast"),
        ]
        for payload in bad:
            with self.subTest(payload=payload), self.assertRaises(services.WeatherError):
                services.parse_current(payload, "metric")

    def test_night_and_sky(self):
        parsed = services.parse_current(current_payload(weather=[{"description": "clear sky", "icon": "01n"}]), "metric")["current"]
        self.assertTrue(parsed["is_night"])
        self.assertEqual((parsed["sky"], parsed["icon"]), ("clear", "01n"))

    def test_compass_and_icon_mapping(self):
        self.assertEqual([services.compass(d) for d in (0, 90, 180, 270, 359, 22)], ["N", "E", "S", "W", "N", "NNE"])
        self.assertEqual(services.icon_symbol("03n"), "03")
        self.assertEqual(services.icon_symbol("10n"), "10n")
        self.assertEqual(services.icon_symbol("10n", force_day=True), "10d")
        self.assertEqual(services.icon_symbol("zzz"), "04")
        self.assertEqual(services.icon_symbol(None), "04")

    def _slots(self, **kw):
        slots, offset = services.parse_forecast(forecast_payload(**kw), OFFSET)
        self.assertEqual(offset, OFFSET)
        return slots

    def test_daily_groups_by_city_local_date(self):
        slots = self._slots()
        today = datetime(2026, 10, 17).date()
        daily = services.build_daily(slots, today)
        self.assertEqual(len(daily), 5)
        self.assertEqual([d["iso"] for d in daily], ["2026-10-17", "2026-10-18", "2026-10-19", "2026-10-20", "2026-10-21"])
        self.assertEqual([d["label"] for d in daily[:3]], ["Today", "Tomorrow", "Mon"])
        self.assertEqual(daily[1]["date"], "18 Oct")
        # 18 Oct holds 8 slots with temps 25..32 -> high 32, low 25
        self.assertEqual((daily[1]["high"], daily[1]["low"]), (32, 25))
        self.assertFalse(daily[1]["partial"])
        self.assertEqual(daily[1]["intervals"], 8)
        # today only has 3 intervals left
        self.assertTrue(daily[0]["partial"])
        self.assertEqual(daily[0]["intervals"], 3)

    def test_todays_range_includes_the_observed_temperature(self):
        slots = self._slots()
        today = datetime(2026, 10, 17).date()
        # remaining slots today are 25, 26, 27 -> a 35 C observation raises the high, 20 C lowers the low
        self.assertEqual((services.build_daily(slots, today)[0]["high"], services.build_daily(slots, today)[0]["low"]), (27, 25))
        hot = services.build_daily(slots, today, observed_temp=35.2)[0]
        cold = services.build_daily(slots, today, observed_temp=20.4)[0]
        self.assertEqual(hot["high"], 35)
        self.assertEqual(cold["low"], 20)
        self.assertEqual(services.build_daily(slots, today, observed_temp=35.2)[1]["high"], 32)  # other days unaffected

    def test_daily_icon_is_always_the_day_variant_and_pop_is_the_max(self):
        daily = services.build_daily(self._slots(), datetime(2026, 10, 17).date())
        self.assertTrue(all(d["icon"] == "10d" for d in daily))
        self.assertEqual(daily[1]["pop"], 35)

    def test_hourly_is_next_24_hours_with_day_marker_on_rollover(self):
        hourly = services.build_hourly(self._slots(), datetime(2026, 10, 17).date())
        self.assertEqual(len(hourly), 8)
        self.assertEqual(hourly[0]["time"], "5 PM")
        self.assertEqual(hourly[0]["day"], "")           # same day as today: no marker
        self.assertEqual(hourly[3]["time"], "2 AM")      # 02:00 on 18 Oct
        self.assertEqual(hourly[3]["day"], "Sun")
        self.assertEqual(hourly[4]["day"], "")           # marker only on the first slot of a new day

    def test_forecast_skips_damaged_slots_but_keeps_the_rest(self):
        data = forecast_payload()
        data["list"][1] = {"dt": data["list"][1]["dt"], "main": {}}
        data["list"][2]["weather"] = []
        data["list"][3] = "garbage"
        slots, _ = services.parse_forecast(data, OFFSET)
        self.assertEqual(len(slots), 37)

    def test_forecast_with_nothing_usable_raises(self):
        for data in ({"list": []}, {"list": "x"}, {}, {"list": [{"dt": 1}]}):
            with self.subTest(data=data), self.assertRaises(services.WeatherError):
                services.parse_forecast(data, OFFSET)


# ----------------------------------------------------------------------------
# City search
# ----------------------------------------------------------------------------
class CitySearchTests(ViewTestCase):
    def test_city_search_renders_current_forecast_and_metrics(self):
        fake = ok()
        r = self.get(fake, city="Manila")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Manila, PH")
        self.assertContains(r, "Scattered clouds")
        self.assertContains(r, "Next 24 hours")
        self.assertContains(r, "5-day forecast")
        self.assertContains(r, "Feels like")
        self.assertContains(r, "Humidity")
        self.assertContains(r, "70<small>%</small>", html=False)
        self.assertEqual(len(r.context["report"]["daily"]), 5)
        self.assertEqual(len(r.context["report"]["hourly"]), 8)

    def test_city_goes_to_current_endpoint_as_q_with_key_and_units_and_timeout(self):
        fake = ok()
        self.get(fake, city="Tokyo", units="imperial")
        url, params, timeout = fake.calls[0]
        self.assertEqual(url, "https://api.openweathermap.org/data/2.5/weather")
        self.assertEqual(params, {"q": "Tokyo", "units": "imperial", "appid": "test-key-123"})
        self.assertEqual(timeout, (5, 8))

    def test_forecast_uses_the_coordinates_resolved_for_the_city(self):
        fake = ok()
        self.get(fake, city="Manila")
        url, params, _ = fake.calls[1]
        self.assertEqual(url, "https://api.openweathermap.org/data/2.5/forecast")
        self.assertEqual(params, {"lat": 14.6042, "lon": 120.9842, "units": "metric", "appid": "test-key-123"})
        self.assertNotIn("q", params)

    def test_default_city_is_used_on_first_visit(self):
        fake = ok()
        self.get(fake)
        self.assertEqual(fake.calls[0][1]["q"], "Puerto Princesa")

    @override_settings(DEFAULT_CITY="")
    def test_empty_state_when_nothing_requested_and_no_default(self):
        fake = ok()
        r = self.get(fake)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Where to?")
        self.assertEqual(fake.calls, [])

    def test_recent_search_payload_contains_exact_coordinates(self):
        r = self.get(ok(), city="Manila")
        self.assertContains(r, '<script id="recent-data" type="application/json">')
        self.assertContains(r, '"lat": 14.6042')
        self.assertContains(r, '"label": "Manila, PH"')

    def test_observed_and_forecast_are_labelled_differently(self):
        r = self.get(ok(), city="Manila")
        self.assertContains(r, "Observed 2:10 PM")
        self.assertContains(r, "tag-observed")
        self.assertContains(r, "tag-forecast")
        self.assertContains(r, 'data-fetched="')       # last-updated timestamp

    def test_local_time_uses_the_city_offset_not_the_server_zone(self):
        r = self.get(ok(timezone=-18000, dt=ts(17, 6, 10)), city="Manila")  # UTC-5 -> 1:10 AM
        self.assertContains(r, "Observed 1:10 AM")
        self.assertContains(r, 'data-offset="-18000"')

    def test_page_contains_exact_developer_credit(self):
        r = self.get(ok(), city="Manila")
        self.assertContains(r, "Developed by Jhon Grover Longsud and Allan Dexter P. Cuizon")

    def test_credit_shows_even_on_error_pages(self):
        r = self.get(router(response({}, 404)), city="Nowhere")
        self.assertContains(r, "Developed by Jhon Grover Longsud and Allan Dexter P. Cuizon", status_code=404)


# ----------------------------------------------------------------------------
# Coordinate search (browser geolocation)
# ----------------------------------------------------------------------------
class CoordinateSearchTests(ViewTestCase):
    def test_coordinates_are_sent_as_lat_lon_to_both_endpoints_and_never_as_q(self):
        fake = ok(name="Cebu City", coord={"lat": 10.3157, "lon": 123.8854})
        r = self.get(fake, lat="10.3157", lon="123.8854")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(fake.calls), 2)
        for url, params, _ in fake.calls:
            self.assertEqual((params["lat"], params["lon"]), (10.3157, 123.8854), url)
            self.assertNotIn("q", params)
        self.assertContains(r, "Cebu City")

    def test_coordinates_take_priority_over_a_city_parameter(self):
        fake = ok()
        self.get(fake, lat="1.5", lon="2.5", city="Paris")
        self.assertTrue(all("q" not in p for _, p, _ in fake.calls))

    def test_search_box_is_empty_in_coordinate_mode(self):
        r = self.get(ok(), lat="10", lon="123", city="Paris")
        self.assertNotContains(r, 'value="Paris"')

    def test_negative_coordinates(self):
        fake = ok()
        self.get(fake, lat="-33.8688", lon="-151.2093")
        self.assertEqual((fake.calls[0][1]["lat"], fake.calls[0][1]["lon"]), (-33.8688, -151.2093))

    def test_unnamed_location_still_renders_with_coordinate_label(self):
        r = self.get(ok(name="", sys={"country": ""}), lat="0.5", lon="-30.25")
        self.assertContains(r, "14.60°, 120.98°")

    def test_invalid_coordinates_are_rejected_without_calling_the_api(self):
        for params in [{"lat": "abc", "lon": "1"}, {"lat": "91", "lon": "1"}, {"lat": "1", "lon": "181"},
                       {"lat": "10"}, {"lon": "10"}, {"lat": "", "lon": ""}, {"lat": "nan", "lon": "0"}]:
            with self.subTest(params=params):
                fake = ok()
                r = self.get(fake, **params)
                self.assertEqual(r.status_code, 400)
                self.assertEqual(fake.calls, [])
                self.assertContains(r, "Check your search", status_code=400)


# ----------------------------------------------------------------------------
# Units
# ----------------------------------------------------------------------------
class UnitsTests(ViewTestCase):
    def test_metric_labels(self):
        r = self.get(ok(), city="Manila")
        self.assertContains(r, "°C")
        self.assertContains(r, "m/s")
        self.assertEqual(r.context["unit_labels"], {"temp": "°C", "speed": "m/s", "distance": "km"})

    def test_imperial_labels_and_api_units(self):
        fake = ok()
        r = self.get(fake, city="Manila", units="imperial")
        self.assertEqual(fake.calls[0][1]["units"], "imperial")
        self.assertEqual(fake.calls[1][1]["units"], "imperial")
        self.assertContains(r, "°F")
        self.assertContains(r, "mph")
        self.assertContains(r, 'data-unit="imperial" aria-pressed="true"')

    def test_invalid_units_fall_back_to_metric(self):
        fake = ok()
        self.get(fake, city="Manila", units="kelvin")
        self.assertEqual(fake.calls[0][1]["units"], "metric")

    def test_saved_units_cookie_is_used_when_no_param(self):
        fake = ok()
        self.client.cookies["ow_units"] = "imperial"
        self.get(fake, city="Manila")
        self.assertEqual(fake.calls[0][1]["units"], "imperial")

    def test_param_beats_cookie(self):
        fake = ok()
        self.client.cookies["ow_units"] = "imperial"
        self.get(fake, city="Manila", units="metric")
        self.assertEqual(fake.calls[0][1]["units"], "metric")

    def test_units_are_cached_separately(self):
        fake = ok()
        self.get(fake, city="Manila", units="metric")
        self.get(fake, city="Manila", units="imperial")
        self.assertEqual(len(fake.calls), 4)


# ----------------------------------------------------------------------------
# Invalid input
# ----------------------------------------------------------------------------
class InvalidInputTests(ViewTestCase):
    def test_bad_cities_are_rejected_before_any_api_call(self):
        for city in ["", "   ", "<script>alert(1)</script>", "x" * 81, "1234", "a;b"]:
            with self.subTest(city=city):
                fake = ok()
                r = self.get(fake, city=city)
                self.assertEqual(r.status_code, 400)
                self.assertEqual(fake.calls, [])
                self.assertNotContains(r, "<script>alert", status_code=400)

    def test_user_text_is_escaped_in_the_page(self):
        r = self.get(router(response({}, 404)), city="Bad'Name")
        self.assertEqual(r.status_code, 404)
        self.assertContains(r, "No match for", status_code=404)
        self.assertNotContains(r, "Bad'Name\"", status_code=404, html=False)  # apostrophe must be escaped

    def test_post_is_not_allowed(self):
        self.assertEqual(self.client.post(reverse("weather"), {"city": "Manila"}).status_code, 405)

    def test_very_long_query_string_values_are_safe(self):
        self.assertEqual(self.get(ok(), city="a" * 5000).status_code, 400)


# ----------------------------------------------------------------------------
# API failures
# ----------------------------------------------------------------------------
class ApiFailureTests(ViewTestCase):
    def assertFailure(self, fake, status, text, heading=None):
        r = self.get(fake, city="Manila")
        self.assertEqual(r.status_code, status)
        self.assertContains(r, escape(text), status_code=status)   # page output is HTML-escaped
        if heading:
            self.assertContains(r, heading, status_code=status)
        self.assertNotContains(r, "test-key-123", status_code=status)
        self.assertNotContains(r, "Traceback", status_code=status)
        return r

    def test_city_not_found(self):
        self.assertFailure(router(response({"cod": "404"}, 404)), 404, "No match for “Manila”", "Location not found")

    def test_bad_api_key(self):
        self.assertFailure(router(response({"cod": 401}, 401)), 502, "rejected the API key", "API key problem")

    def test_rate_limited(self):
        self.assertFailure(router(response({}, 429)), 502, "request limit was reached", "Too many requests")

    def test_server_error(self):
        self.assertFailure(router(response({}, 503)), 502, "error 503")

    def test_timeout(self):
        self.assertFailure(router(requests.exceptions.Timeout), 502, "took too long")

    def test_connection_error(self):
        self.assertFailure(router(requests.exceptions.ConnectionError), 502, "Couldn't reach OpenWeather")

    def test_body_is_not_json(self):
        self.assertFailure(router(response(json_error=True)), 502, "couldn't read")

    def test_body_is_not_an_object(self):
        self.assertFailure(router(response([1, 2, 3])), 502, "couldn't read")

    def test_missing_fields(self):
        self.assertFailure(router(response({"name": "Manila"})), 502, "couldn't read")

    def test_error_reported_inside_a_200_body(self):
        self.assertFailure(router(response({"cod": "404", "message": "city not found"})), 404, "No match")
        self.assertFailure(router(response({"cod": 401, "message": "Invalid API key"})), 502, "rejected the API key")

    def test_api_key_is_not_logged_or_shown_when_the_exception_message_contains_the_url(self):
        boom = requests.exceptions.ConnectionError(
            "HTTPSConnectionPool: Max retries exceeded with url: /data/2.5/weather?q=Manila&appid=test-key-123")
        with self.assertLogs("weather.services", level="WARNING") as logs:
            r = self.assertFailure(router(boom), 502, "Couldn't reach OpenWeather")
        self.assertNotIn("test-key-123", "\n".join(logs.output))
        self.assertNotIn("appid", r.content.decode())

    @override_settings(OPENWEATHER_API_KEY="")
    def test_missing_api_key(self):
        fake = ok()
        r = self.get(fake, city="Manila")
        self.assertEqual(r.status_code, 503)
        self.assertContains(r, "OPENWEATHER_API_KEY", status_code=503)
        self.assertContains(r, "Weather service not set up", status_code=503)
        self.assertEqual(fake.calls, [])

    def test_forecast_failure_keeps_current_weather(self):
        for failure in (response({}, 500), response({}, 429), requests.exceptions.Timeout,
                        requests.exceptions.ConnectionError, response(json_error=True), response({"list": []})):
            with self.subTest(failure=failure):
                cache.clear()
                r = self.get(router(response(current_payload()), failure), city="Manila")
                self.assertEqual(r.status_code, 200)
                self.assertContains(r, "Manila, PH")
                self.assertContains(r, "Forecast unavailable")
                self.assertNotContains(r, "Next 24 hours")
                self.assertEqual(r.context["report"]["daily"], [])

    def test_partially_damaged_forecast_still_renders(self):
        data = forecast_payload()
        data["list"][5] = None
        r = self.get(router(response(current_payload()), response(data)), city="Manila")
        self.assertContains(r, "5-day forecast")
        self.assertEqual(len(r.context["report"]["daily"]), 5)

    def test_no_third_party_requests_in_the_page(self):
        html = self.get(ok(), city="Manila").content.decode()
        self.assertNotIn("openweathermap.org/img", html)
        self.assertNotIn("fonts.googleapis", html)
        self.assertNotIn("test-key-123", html)


# ----------------------------------------------------------------------------
# Caching
# ----------------------------------------------------------------------------
class CachingTests(ViewTestCase):
    def test_repeat_request_is_served_from_cache(self):
        fake = ok()
        self.get(fake, city="Manila")
        self.get(fake, city="Manila")
        self.assertEqual(len(fake.calls), 2)   # one current + one forecast, total

    def test_refresh_bypasses_the_cache(self):
        fake = ok()
        self.get(fake, city="Manila")
        self.get(fake, city="Manila", refresh="1")
        self.assertEqual(len(fake.calls), 4)

    def test_failures_are_not_cached(self):
        self.get(router(response({}, 500)), city="Manila")
        fake = ok()
        r = self.get(fake, city="Manila")
        self.assertContains(r, "Manila, PH")
        self.assertEqual(len(fake.calls), 2)

    @override_settings(WEATHER_CACHE_SECONDS=0)
    def test_cache_can_be_turned_off(self):
        fake = ok()
        self.get(fake, city="Manila")
        self.get(fake, city="Manila")
        self.assertEqual(len(fake.calls), 4)

    def test_cache_key_never_contains_the_api_key(self):
        key = services._cache_key("current", {"q": "Manila", "units": "metric"}, "metric")
        self.assertNotIn("test-key", key)
        self.assertTrue(key.startswith("ow:"))

    def test_last_updated_is_the_fetch_time_not_the_request_time(self):
        fake = ok()
        first = self.get(fake, city="Manila").context["report"]["current_fetched_at"]
        second = self.get(fake, city="Manila").context["report"]["current_fetched_at"]
        self.assertEqual(first, second)


# ----------------------------------------------------------------------------
# Routing, templates, static files
# ----------------------------------------------------------------------------
class RoutingAndStaticTests(ViewTestCase):
    def test_root_url_resolves(self):
        self.assertEqual(reverse("weather"), "/")

    def test_uses_the_app_template_only(self):
        r = self.get(ok(), city="Manila")
        self.assertEqual([t.name for t in r.templates if t.name][0], "weather/weather.html")

    def test_static_assets_exist_and_are_referenced(self):
        r = self.get(ok(), city="Manila")
        for name in ("style.css", "app.js", "theme-init.js"):
            self.assertIsNotNone(finders.find(f"weather/{name}"), name)
            self.assertContains(r, f"/static/weather/{name}")

    def test_theme_controls_and_geolocation_control_are_present(self):
        r = self.get(ok(), city="Manila")
        for needle in ('data-theme-choice="system"', 'data-theme-choice="light"', 'data-theme-choice="dark"',
                       'id="location-button"', 'id="refresh-button"', 'data-unit="metric"', 'id="recent"'):
            self.assertContains(r, needle)

    def test_every_icon_used_exists_in_the_sprite(self):
        html = self.get(ok(), city="Manila").content.decode()
        import re
        used = set(re.findall(r'<use href="#(i-[0-9a-z]+)"', html))
        defined = set(re.findall(r'<symbol id="(i-[0-9a-z]+)"', html))
        self.assertTrue(used and used <= defined, used - defined)

    def test_sprite_covers_every_icon_code_openweather_documents(self):
        html = self.get(ok(), city="Manila").content.decode()
        for code in ["01d", "01n", "02d", "02n", "03d", "03n", "04d", "04n", "09d", "09n", "10d", "10n", "11d", "11n", "13d", "13n", "50d", "50n"]:
            self.assertIn(f'<symbol id="i-{services.icon_symbol(code)}"', html, code)

    def test_json_script_is_safe_for_hostile_place_names(self):
        r = self.get(ok(name='</script><b>"x"'), city="Manila")
        self.assertNotContains(r, "</script><b>")
        data = json.loads(r.content.decode().split('id="recent-data" type="application/json">')[1].split("</script>")[0])
        self.assertIn("label", data)
