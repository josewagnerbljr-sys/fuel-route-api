"""Clients for the free routing (OSRM) and geocoding (Nominatim) services."""

from __future__ import annotations

import hashlib
import threading
import time
from dataclasses import dataclass

import numpy as np
import requests
from django.conf import settings
from django.core.cache import cache
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .geo import Location, is_in_usa
from .geometry import METERS_PER_MILE


class ProviderError(Exception):
    """The routing or geocoding service failed or returned an invalid response."""


class RouteNotFound(Exception):
    """The routing service found no drivable route between the points."""


class PlaceNotFound(Exception):
    """The geocoding service has no US match for the text."""


@dataclass(frozen=True)
class RouteData:
    coords: np.ndarray       # (N, 2) lat, lon
    distance_miles: float
    duration_seconds: float


_session_lock = threading.Lock()
_session: requests.Session | None = None


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        with _session_lock:
            if _session is None:
                session = requests.Session()
                retry = Retry(
                    total=2,
                    backoff_factor=0.4,
                    status_forcelist=(502, 503, 504),
                    allowed_methods=("GET",),
                    raise_on_status=False,
                )
                adapter = HTTPAdapter(pool_connections=8, pool_maxsize=64, max_retries=retry)
                session.mount("https://", adapter)
                session.mount("http://", adapter)
                _session = session
    return _session


def reset_session() -> None:
    global _session
    with _session_lock:
        if _session is not None:
            _session.close()
        _session = None


def _count_call(stats: dict | None) -> None:
    if stats is not None:
        stats["external_calls"] = stats.get("external_calls", 0) + 1


def _cache_key(prefix: str, *parts: str) -> str:
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()
    return f"{prefix}:{digest}"


# Routing
def fetch_route(start: Location, finish: Location, stats: dict | None = None) -> tuple[RouteData, bool]:
    """Driving route between two points. One HTTP call, cached afterwards.

    Returns (route, served_from_cache).
    """
    key = _cache_key(
        "route", f"{start.lat:.4f},{start.lon:.4f}", f"{finish.lat:.4f},{finish.lon:.4f}"
    )
    cached = cache.get(key)
    if cached is not None:
        return cached, True

    url = (
        f"{settings.OSRM_BASE_URL.rstrip('/')}/route/v1/driving/"
        f"{start.lon:.6f},{start.lat:.6f};{finish.lon:.6f},{finish.lat:.6f}"
    )
    params = {"overview": "full", "geometries": "geojson", "steps": "false", "alternatives": "false"}
    _count_call(stats)
    try:
        resp = _get_session().get(url, params=params, timeout=settings.PROVIDER_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise ProviderError(f"Routing service unreachable: {exc.__class__.__name__}") from exc

    try:
        payload = resp.json()
    except ValueError:
        payload = None
    code = payload.get("code") if isinstance(payload, dict) else None

    if code in {"NoRoute", "NoSegment"}:
        raise RouteNotFound("No drivable route exists between the two locations.")
    if resp.status_code != 200 or code != "Ok":
        raise ProviderError(f"Routing service error (HTTP {resp.status_code}, code {code}).")

    try:
        route = payload["routes"][0]
        coords = np.asarray(route["geometry"]["coordinates"], dtype=float)
        if coords.ndim != 2 or coords.shape[0] < 2 or coords.shape[1] < 2:
            raise ValueError("geometry")
        data = RouteData(
            coords=coords[:, [1, 0]].copy(),  # GeoJSON is lon,lat
            distance_miles=float(route["distance"]) / METERS_PER_MILE,
            duration_seconds=float(route["duration"]),
        )
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ProviderError("Routing service returned an unexpected payload.") from exc

    cache.set(key, data, settings.ROUTE_CACHE_SECONDS)
    return data, False


# Geocoding (fallback for free-text places)
_geocode_lock = threading.Lock()
_next_geocode_at = 0.0
_MIN_GEOCODE_INTERVAL = 1.0  # public Nominatim allows one request per second
_MAX_GEOCODE_QUEUE_SECONDS = 5.0


def _wait_for_geocode_slot() -> None:
    """Pace geocoder calls to one per interval; refuse when the queue is too long."""
    global _next_geocode_at
    with _geocode_lock:
        now = time.monotonic()
        wait = _next_geocode_at - now
        if wait > _MAX_GEOCODE_QUEUE_SECONDS:
            raise ProviderError("Geocoding is busy, retry shortly.")
        _next_geocode_at = max(now, _next_geocode_at) + _MIN_GEOCODE_INTERVAL
    if wait > 0:
        time.sleep(wait)


def geocode(text: str, stats: dict | None = None) -> Location:
    """Resolve free text to a US location through Nominatim."""
    key = _cache_key("geocode", text.lower())
    cached = cache.get(key)
    if cached is not None:
        return cached

    _wait_for_geocode_slot()
    _count_call(stats)
    try:
        resp = _get_session().get(
            f"{settings.NOMINATIM_BASE_URL.rstrip('/')}/search",
            params={"q": text, "format": "jsonv2", "countrycodes": "us", "limit": 1},
            headers={"User-Agent": settings.NOMINATIM_USER_AGENT},
            timeout=settings.PROVIDER_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise ProviderError(f"Geocoding service unreachable: {exc.__class__.__name__}") from exc
    if resp.status_code != 200:
        raise ProviderError(f"Geocoding service error (HTTP {resp.status_code}).")
    try:
        results = resp.json()
        if not results:
            raise PlaceNotFound(f"No US match found for '{text}'.")
        lat, lon = float(results[0]["lat"]), float(results[0]["lon"])
        label = str(results[0].get("display_name", text))
    except PlaceNotFound:
        raise
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ProviderError("Geocoding service returned an unexpected payload.") from exc
    if not is_in_usa(lat, lon):
        raise PlaceNotFound(f"'{text}' is not in the USA.")

    location = Location(lat, lon, label, "geocoder")
    cache.set(key, location, settings.ROUTE_CACHE_SECONDS)
    return location
