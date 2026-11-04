import json
import logging

from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET, require_http_methods

from . import service
from .geo import LocationError
from .providers import PlaceNotFound, ProviderError, RouteNotFound
from .stations import get_index

logger = logging.getLogger(__name__)

MAX_BODY_BYTES = 8 * 1024


class BadRequest(Exception):
    pass


def _error(status: int, code: str, message: str) -> JsonResponse:
    return JsonResponse({"error": {"code": code, "message": message}}, status=status)


def _read_params(request) -> dict:
    if request.method == "GET":
        data = request.GET.dict()
    else:
        if len(request.body) > MAX_BODY_BYTES:
            raise BadRequest("Request body is too large.")
        content_type = request.content_type or ""
        if content_type == "application/json" or not content_type:
            try:
                data = json.loads(request.body.decode("utf-8") or "{}")
            except (ValueError, UnicodeDecodeError):
                raise BadRequest("Body must be valid JSON.") from None
        else:
            data = request.POST.dict()
    if not isinstance(data, dict):
        raise BadRequest("Body must be a JSON object.")

    start, finish = data.get("start"), data.get("finish")
    if start in (None, "") or finish in (None, ""):
        raise BadRequest("Both 'start' and 'finish' are required.")

    fuel = data.get("starting_fuel_gallons")
    if fuel in (None, ""):
        fuel = None
    else:
        try:
            fuel = float(fuel)
        except (TypeError, ValueError):
            raise BadRequest("'starting_fuel_gallons' must be a number.") from None
        if not 0 <= fuel <= 10_000 or fuel != fuel:
            raise BadRequest("'starting_fuel_gallons' must be zero or positive.")
    return {"start": start, "finish": finish, "starting_fuel_gallons": fuel}


def _compute(request):
    """Return (result, None) or (None, error_response)."""
    try:
        params = _read_params(request)
        result = service.plan_trip(
            params["start"], params["finish"], params["starting_fuel_gallons"]
        )
        return result, None
    except BadRequest as exc:
        return None, _error(400, "invalid_request", str(exc))
    except LocationError as exc:
        return None, _error(400, "invalid_location", str(exc))
    except PlaceNotFound as exc:
        return None, _error(422, "location_not_found", str(exc))
    except RouteNotFound as exc:
        return None, _error(422, "route_not_found", str(exc))
    except service.NoFuelPlan as exc:
        return None, _error(422, "no_fuel_plan", str(exc))
    except ProviderError as exc:
        logger.warning("provider failure: %s", exc)
        return None, _error(502, "upstream_error", str(exc))
    except Exception:
        logger.exception("unhandled error while planning a route")
        return None, _error(500, "internal_error", "Unexpected error while planning the route.")


@require_http_methods(["GET", "POST"])
def route_view(request):
    result, error = _compute(request)
    if error is not None:
        return error
    result["map"]["html_url"] = request.build_absolute_uri(
        "/api/route/map/?"
        + _query_string(result["start"]["query"], result["finish"]["query"], request)
    )
    return JsonResponse(result, json_dumps_params={"separators": (",", ":")})


def _query_string(start: str, finish: str, request) -> str:
    from urllib.parse import urlencode

    pairs = {"start": start, "finish": finish}
    fuel = None
    if request.method == "GET":
        fuel = request.GET.get("starting_fuel_gallons")
    else:
        try:
            body = json.loads(request.body.decode("utf-8") or "{}")
            fuel = body.get("starting_fuel_gallons") if isinstance(body, dict) else None
        except (ValueError, UnicodeDecodeError):
            fuel = None
    if fuel not in (None, ""):
        pairs["starting_fuel_gallons"] = str(fuel)
    return urlencode(pairs)


@require_GET
def map_view(request):
    result, error = _compute(request)
    if error is not None:
        return error
    context = {
        "plan": {
            "route": result["route"],
            "fuel": result["fuel"],
            "fuel_stops": result["fuel_stops"],
            "map": result["map"],
            "start": result["start"],
            "finish": result["finish"],
        }
    }
    return render(request, "routing/map.html", context)


@require_GET
def health_view(request):
    try:
        count = get_index().count
    except Exception:
        return _error(503, "database_unavailable", "Station data is not available.")
    return JsonResponse({"status": "ok", "stations_loaded": count})
