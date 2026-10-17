"""OpenWeather client, input validation and response parsing.

Only documented endpoints are used:
  * Current weather data   GET {base}/weather   https://openweathermap.org/current
  * 5 day / 3 hour forecast GET {base}/forecast  https://openweathermap.org/forecast5

Nothing here invents data: every number shown to the user comes from an
OpenWeather response, and anything missing is reported as missing.
"""
import hashlib
import logging
import math
import re
from datetime import datetime, timedelta, timezone

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

UNITS = {
    "metric": {"temp": "°C", "speed": "m/s", "distance": "km"},
    "imperial": {"temp": "°F", "speed": "mph", "distance": "mi"},
}
DEFAULT_UNITS = "metric"

MAX_CITY_LENGTH = 80
HOURLY_SLOTS = 8   # 8 x 3 h = the next 24 hours
DAILY_DAYS = 5
SLOTS_PER_DAY = 8

_CITY_RE = re.compile(r"^[\w\s.,'’()\-]+$", re.UNICODE)
_HAS_LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)
_COORD_RE = re.compile(r"^[+-]?\d{1,3}(\.\d{1,8})?$")
_COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------
class WeatherError(Exception):
    """An error with a message that is safe and useful to show to the user."""

    status_code = 502

    def __init__(self, message, *, kind="upstream"):
        super().__init__(message)
        self.message = message
        self.kind = kind


class InvalidInput(WeatherError):
    status_code = 400

    def __init__(self, message):
        super().__init__(message, kind="input")


class LocationNotFound(WeatherError):
    status_code = 404

    def __init__(self, message):
        super().__init__(message, kind="not_found")


class NotConfigured(WeatherError):
    status_code = 503

    def __init__(self):
        super().__init__(
            "The weather service isn't set up yet. Add OPENWEATHER_API_KEY to the "
            ".env file next to manage.py, then restart the server.",
            kind="config",
        )


# --------------------------------------------------------------------------
# Input validation
# --------------------------------------------------------------------------
class Query:
    """A validated location query: either a city name or a coordinate pair."""

    def __init__(self, *, city=None, lat=None, lon=None):
        self.city, self.lat, self.lon = city, lat, lon

    @property
    def by_coordinates(self):
        return self.city is None

    def params(self):
        if self.by_coordinates:
            return {"lat": self.lat, "lon": self.lon}
        return {"q": self.city}


def parse_city(raw):
    city = " ".join((raw or "").split())
    if not city:
        raise InvalidInput("Enter a city name to search.")
    if len(city) > MAX_CITY_LENGTH:
        raise InvalidInput(f"City names must be {MAX_CITY_LENGTH} characters or fewer.")
    if not _CITY_RE.match(city) or not _HAS_LETTER_RE.search(city):
        raise InvalidInput(
            "City names can only contain letters, numbers, spaces and . , ' ( ) - "
            "Try something like Cebu or Paris, FR."
        )
    return Query(city=city)


def parse_coordinates(raw_lat, raw_lon):
    values = []
    for raw, name, low, high in ((raw_lat, "Latitude", -90, 90), (raw_lon, "Longitude", -180, 180)):
        text = (raw or "").strip()
        if not text:
            raise InvalidInput("Both latitude and longitude are needed to search by location.")
        if not _COORD_RE.match(text):
            raise InvalidInput(f"{name} must be a plain number such as 10.75.")
        number = float(text)
        if not low <= number <= high:
            raise InvalidInput(f"{name} must be between {low} and {high}.")
        values.append(round(number, 6))
    return Query(lat=values[0], lon=values[1])


def normalize_units(value):
    return value if value in UNITS else DEFAULT_UNITS


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------
def _get_json(endpoint, params):
    api_key = getattr(settings, "OPENWEATHER_API_KEY", "")
    if not api_key:
        raise NotConfigured()
    url = f"{settings.OPENWEATHER_BASE_URL.rstrip('/')}/{endpoint}"
    try:
        response = requests.get(
            url,
            params={**params, "appid": api_key},
            timeout=settings.OPENWEATHER_TIMEOUT,
        )
    except requests.exceptions.Timeout:
        logger.warning("OpenWeather %s: timeout", endpoint)
        raise WeatherError("OpenWeather took too long to respond. Try again in a moment.", kind="timeout")
    except requests.exceptions.RequestException as exc:
        # Log only the exception class: the message contains the full URL, including the API key.
        logger.warning("OpenWeather %s: request failed (%s)", endpoint, type(exc).__name__)
        raise WeatherError(
            "Couldn't reach OpenWeather. Check your internet connection and try again.",
            kind="network",
        )

    status = response.status_code
    if status != 200:
        logger.warning("OpenWeather %s: HTTP %s", endpoint, status)
        raise _error_for_status(status)
    try:
        data = response.json()
    except ValueError:
        raise _malformed()
    if not isinstance(data, dict):
        raise _malformed()
    # The API sometimes reports failures inside a 200 body ("cod" may be int or str).
    if str(data.get("cod", "200")) != "200":
        raise _error_for_status(_to_int(data.get("cod")) or 502)
    return data


def _to_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _error_for_status(status):
    if status == 401:
        return WeatherError(
            "OpenWeather rejected the API key. Check OPENWEATHER_API_KEY in .env. "
            "New keys can take a while to activate.",
            kind="auth",
        )
    if status == 404:
        return LocationNotFound("OpenWeather has no match for that location.")
    if status == 400:
        return InvalidInput("OpenWeather couldn't understand that location. Check it and try again.")
    if status == 429:
        return WeatherError(
            "OpenWeather's request limit was reached. Wait a minute, then refresh.",
            kind="rate_limit",
        )
    return WeatherError(
        f"OpenWeather is having trouble right now (error {status}). Try again shortly.",
        kind="upstream",
    )


def _malformed():
    return WeatherError(
        "OpenWeather sent a response this app couldn't read. Try again in a moment.",
        kind="malformed",
    )


# --------------------------------------------------------------------------
# Parsing helpers
# --------------------------------------------------------------------------
def _num(value, *, required=True):
    """Return a finite float, None if optional and absent, else raise."""
    if value is None and not required:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise _malformed()
    return float(value)


def _round(value):
    return int(math.floor(value + 0.5))


def _fmt(value, digits=1):
    text = f"{value:.{digits}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def _clock(moment):
    """'3:07 PM' without platform-specific strftime flags."""
    return f"{moment.hour % 12 or 12}:{moment.minute:02d} {'AM' if moment.hour < 12 else 'PM'}"


def _hour(moment):
    return f"{moment.hour % 12 or 12} {'AM' if moment.hour < 12 else 'PM'}"


def _local(timestamp, offset):
    return datetime.fromtimestamp(timestamp, timezone(timedelta(seconds=offset)))


def _condition(entry):
    try:
        weather = entry["weather"][0]
        icon = str(weather["icon"])
        description = str(weather["description"]).strip()
    except (KeyError, IndexError, TypeError):
        raise _malformed()
    return description[:1].upper() + description[1:], icon


def icon_symbol(code, *, force_day=False):
    """Map an OpenWeather icon code (e.g. '10n') to an id in the SVG sprite."""
    match = re.fullmatch(r"(\d{2})([dn])", code or "")
    if not match:
        return "04"
    number, phase = match.groups()
    if force_day:
        phase = "d"
    if number in {"01", "02", "10"}:
        return f"{number}{phase}"
    return number if number in {"03", "04", "09", "11", "13", "50"} else "04"


def _sky(code):
    number = (code or "")[:2]
    return {"01": "clear", "02": "clouds", "03": "clouds", "04": "clouds", "09": "rain",
            "10": "rain", "11": "storm", "13": "snow", "50": "mist"}.get(number, "clouds")


def compass(degrees):
    return _COMPASS[int((degrees % 360) / 22.5 + 0.5) % 16]


# --------------------------------------------------------------------------
# Current conditions
# --------------------------------------------------------------------------
def parse_current(data, units):
    try:
        main, wind = data["main"], data.get("wind") or {}
        coord = data["coord"]
        offset = _to_int(data.get("timezone", 0))
        observed = _to_int(data["dt"])
        if offset is None or observed is None or not isinstance(main, dict) or not isinstance(wind, dict):
            raise _malformed()
        description, icon = _condition(data)
        sys_part = data.get("sys") or {}
        lat, lon = _num(coord["lat"]), _num(coord["lon"])
        temp, feels = _num(main["temp"]), _num(main["feels_like"])
        humidity = _num(main.get("humidity"), required=False)
        pressure = _num(main.get("pressure"), required=False)
        speed = _num(wind.get("speed"), required=False)
        degrees = _num(wind.get("deg"), required=False)
        gust = _num(wind.get("gust"), required=False)
        clouds = _num((data.get("clouds") or {}).get("all"), required=False)
        visibility = _num(data.get("visibility"), required=False)
    except (KeyError, TypeError, AttributeError):
        raise _malformed()

    name = str(data.get("name") or "").strip()
    country = str(sys_part.get("country") or "").strip()
    observed_at = _local(observed, offset)
    sunrise, sunset = _to_int(sys_part.get("sunrise")), _to_int(sys_part.get("sunset"))

    if visibility is None:
        visibility_text = None
    else:
        distance = visibility / 1000 if units == "metric" else visibility / 1609.344
        visibility_text = ("10+" if visibility >= 10000 else _fmt(distance))

    label = f"{name}, {country}" if name and country else (name or f"{lat:.2f}°, {lon:.2f}°")
    return {
        "location": {"name": name, "country": country, "label": label, "lat": lat, "lon": lon,
                     "tz_offset": offset},
        "current": {
            "temp": _round(temp),
            "temp_raw": temp,
            "feels_like": _round(feels),
            "description": description,
            "icon": icon_symbol(icon),
            "sky": _sky(icon),
            "is_night": icon.endswith("n"),
            "humidity": None if humidity is None else _round(humidity),
            "pressure": None if pressure is None else _round(pressure),
            "wind_speed": None if speed is None else _fmt(speed),
            "wind_dir": None if degrees is None else compass(degrees),
            "wind_gust": None if gust is None else _fmt(gust),
            "clouds": None if clouds is None else _round(clouds),
            "visibility": visibility_text,
            "sunrise": _clock(_local(sunrise, offset)) if sunrise else None,
            "sunset": _clock(_local(sunset, offset)) if sunset else None,
            "observed_time": _clock(observed_at),
            "observed_date": f"{observed_at:%a} {observed_at.day} {observed_at:%b}",
            "observed_ts": observed,
        },
    }


# --------------------------------------------------------------------------
# Forecast
# --------------------------------------------------------------------------
def parse_forecast(data, offset):
    """Turn the 3-hourly forecast list into an hourly strip and daily summaries."""
    items = data.get("list")
    if not isinstance(items, list):
        raise _malformed()
    city_offset = _to_int((data.get("city") or {}).get("timezone")) if isinstance(data.get("city"), dict) else None
    offset = offset if city_offset is None else city_offset

    slots = []
    for item in items:
        try:
            moment = _local(_to_int(item["dt"]), offset)
            temp = _num(item["main"]["temp"])
            description, icon = _condition(item)
            pop = _num(item.get("pop"), required=False)
        except (KeyError, TypeError, AttributeError, WeatherError):
            continue  # skip a damaged slot, keep the rest
        slots.append({"moment": moment, "temp": temp, "description": description, "icon": icon,
                      "pop": None if pop is None else _round(min(max(pop, 0), 1) * 100)})
    if not slots:
        raise _malformed()
    slots.sort(key=lambda s: s["moment"])
    return slots, offset


def build_hourly(slots, today):
    hourly, previous_day = [], None
    for slot in slots[:HOURLY_SLOTS]:
        moment = slot["moment"]
        new_day = moment.date() != previous_day and moment.date() != today
        hourly.append({
            "time": _hour(moment),
            "day": f"{moment:%a}" if new_day else "",
            "temp": _round(slot["temp"]),
            "description": slot["description"],
            "icon": icon_symbol(slot["icon"]),
            "pop": slot["pop"],
        })
        previous_day = moment.date()
    return hourly


def build_daily(slots, today, observed_temp=None):
    """Daily summaries. Today's range also includes the observed temperature, because the
    forecast only covers the hours that are still to come."""
    days = {}
    for slot in slots:
        if slot["moment"].date() >= today:
            days.setdefault(slot["moment"].date(), []).append(slot)

    daily = []
    for day in sorted(days)[:DAILY_DAYS]:
        group = days[day]
        noon = min(group, key=lambda s: abs(s["moment"].hour + s["moment"].minute / 60 - 12))
        pops = [s["pop"] for s in group if s["pop"] is not None]
        offset_days = (day - today).days
        extra = [observed_temp] if offset_days == 0 and observed_temp is not None else []
        daily.append({
            "label": "Today" if offset_days == 0 else ("Tomorrow" if offset_days == 1 else f"{day:%a}"),
            "date": f"{day.day} {day:%b}",
            "iso": day.isoformat(),
            "high": _round(max([s["temp"] for s in group] + extra)),
            "low": _round(min([s["temp"] for s in group] + extra)),
            "description": noon["description"],
            "icon": icon_symbol(noon["icon"], force_day=True),
            "pop": max(pops) if pops else None,
            "intervals": len(group),
            "partial": len(group) < SLOTS_PER_DAY,
        })
    return daily


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------
def _cache_key(kind, params, units):
    raw = f"{kind}|{sorted(params.items())}|{units}"
    return "ow:" + hashlib.sha256(raw.encode()).hexdigest()


def _cached(kind, params, units, loader, refresh):
    ttl = settings.WEATHER_CACHE_SECONDS
    key = _cache_key(kind, params, units)
    if ttl and not refresh:
        hit = cache.get(key)
        if hit is not None:
            return hit
    value = loader()
    value["fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if ttl:
        cache.set(key, value, ttl)
    return value


def get_report(query, units, *, refresh=False):
    """Fetch current conditions and forecast for a validated Query.

    Raises WeatherError when current conditions are unavailable. If only the forecast fails,
    the report is still returned with `forecast_error` set.
    """
    units = normalize_units(units)
    base = {**query.params(), "units": units}

    current = _cached(
        "current", base, units, refresh=refresh,
        loader=lambda: parse_current(_get_json("weather", base), units),
    )
    location = current["location"]

    # Forecast by coordinates: for a city search, use the coordinates OpenWeather resolved it to,
    # so both panels always describe exactly the same place.
    if query.by_coordinates:
        forecast_params = {"lat": query.lat, "lon": query.lon, "units": units}
    else:
        forecast_params = {"lat": location["lat"], "lon": location["lon"], "units": units}

    offset = location["tz_offset"]
    today = _local(current["current"]["observed_ts"], offset).date()
    forecast, forecast_error = None, None
    try:
        def load_forecast():
            slots, real_offset = parse_forecast(_get_json("forecast", forecast_params), offset)
            return {"slots": slots, "offset": real_offset}
        forecast = _cached("forecast", forecast_params, units, load_forecast, refresh)
    except WeatherError as exc:
        forecast_error = exc.message

    hourly, daily = [], []
    if forecast:
        hourly = build_hourly(forecast["slots"], today)
        daily = build_daily(forecast["slots"], today, current["current"]["temp_raw"])

    return {
        "location": location,
        "current": current["current"],
        "current_fetched_at": current["fetched_at"],
        "hourly": hourly,
        "daily": daily,
        "forecast_fetched_at": forecast["fetched_at"] if forecast else None,
        "forecast_error": forecast_error,
        "units": units,
        "unit_labels": UNITS[units],
    }
