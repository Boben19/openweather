import logging

from django.conf import settings
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_safe

from . import services

logger = logging.getLogger(__name__)

UNITS_COOKIE = "ow_units"


def _units_for(request):
    """?units= wins, then the saved cookie, then metric."""
    requested = request.GET.get("units") or request.COOKIES.get(UNITS_COOKIE)
    return services.normalize_units(requested)


def _query_for(request):
    """Validate the request and return a Query, or None for the empty state."""
    params = request.GET
    if "lat" in params or "lon" in params:
        # Browser geolocation sends coordinates; they are looked up by lat/lon, never by name.
        return services.parse_coordinates(params.get("lat"), params.get("lon"))
    if "city" in params:
        return services.parse_city(params.get("city"))
    if settings.DEFAULT_CITY:
        return services.parse_city(settings.DEFAULT_CITY)
    return None


@require_safe
@never_cache
def weather_view(request):
    units = _units_for(request)
    context = {
        "units": units,
        "unit_labels": services.UNITS[units],
        "city_value": request.GET.get("city", "") if "lat" not in request.GET else "",
        "report": None,
        "error": None,
        "error_kind": None,
    }
    status = 200

    try:
        query = _query_for(request)
        if query is not None:
            context["report"] = services.get_report(
                query, units, refresh=request.GET.get("refresh") == "1"
            )
            location = context["report"]["location"]
            context["recent"] = {"label": location["label"], "lat": location["lat"], "lon": location["lon"]}
    except services.LocationNotFound as exc:
        context["error"], context["error_kind"], status = exc.message, exc.kind, exc.status_code
        if "city" in request.GET and "lat" not in request.GET:
            context["error"] = (
                f"No match for “{context['city_value'].strip()}”. Check the spelling, "
                "or add a country code, like Paris, FR."
            )
    except services.WeatherError as exc:
        context["error"], context["error_kind"], status = exc.message, exc.kind, exc.status_code

    return render(request, "weather/weather.html", context, status=status)
