"""Offline US place resolution.

Station rows only carry "city, state", and most trip inputs are "City, ST" too,
so both are resolved from the ZIP code gazetteer bundled with the ``zipcodes``
package. No network call is needed for these. Free text that cannot be
resolved locally is left for the geocoding provider.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass

import numpy as np
import zipcodes
from scipy.spatial import cKDTree

from .geometry import to_xyz

STATE_NAMES = {
    "AL": "ALABAMA", "AK": "ALASKA", "AZ": "ARIZONA", "AR": "ARKANSAS", "CA": "CALIFORNIA",
    "CO": "COLORADO", "CT": "CONNECTICUT", "DE": "DELAWARE", "DC": "DISTRICT OF COLUMBIA",
    "FL": "FLORIDA", "GA": "GEORGIA", "HI": "HAWAII", "ID": "IDAHO", "IL": "ILLINOIS",
    "IN": "INDIANA", "IA": "IOWA", "KS": "KANSAS", "KY": "KENTUCKY", "LA": "LOUISIANA",
    "ME": "MAINE", "MD": "MARYLAND", "MA": "MASSACHUSETTS", "MI": "MICHIGAN", "MN": "MINNESOTA",
    "MS": "MISSISSIPPI", "MO": "MISSOURI", "MT": "MONTANA", "NE": "NEBRASKA", "NV": "NEVADA",
    "NH": "NEW HAMPSHIRE", "NJ": "NEW JERSEY", "NM": "NEW MEXICO", "NY": "NEW YORK",
    "NC": "NORTH CAROLINA", "ND": "NORTH DAKOTA", "OH": "OHIO", "OK": "OKLAHOMA", "OR": "OREGON",
    "PA": "PENNSYLVANIA", "RI": "RHODE ISLAND", "SC": "SOUTH CAROLINA", "SD": "SOUTH DAKOTA",
    "TN": "TENNESSEE", "TX": "TEXAS", "UT": "UTAH", "VT": "VERMONT", "VA": "VIRGINIA",
    "WA": "WASHINGTON", "WV": "WEST VIRGINIA", "WI": "WISCONSIN", "WY": "WYOMING",
}
US_STATE_CODES = frozenset(STATE_NAMES)
_NAME_TO_CODE = {name: code for code, name in STATE_NAMES.items()}

# Points farther than this from every US ZIP centroid are rejected as "not in the USA".
USA_MAX_DISTANCE_MILES = 60.0

_COUNTRY_SUFFIXES = {"USA", "US", "U S A", "U S", "UNITED STATES", "UNITED STATES OF AMERICA"}
_COORD_RE = re.compile(r"^\s*(-?\d{1,3}(?:\.\d+)?)\s*[,;\s]\s*(-?\d{1,3}(?:\.\d+)?)\s*$")
_ZIP_RE = re.compile(r"^\s*(\d{5})(?:-\d{4})?\s*$")
_STATE_ZIP_RE = re.compile(r"^([A-Za-z][A-Za-z .]*?)(?:\s+\d{5}(?:-\d{4})?)?$")
_WORD_FIXES = {"ST": "SAINT", "MT": "MOUNT", "FT": "FORT", "STE": "SAINTE"}


class LocationError(ValueError):
    """The input cannot be interpreted as a place in the USA."""


@dataclass(frozen=True)
class Location:
    lat: float
    lon: float
    label: str
    source: str  # "coordinates" | "gazetteer" | "geocoder"


def normalize_city(name: str) -> str:
    text = re.sub(r"[^A-Z0-9 ]", " ", name.upper().replace("'", ""))
    words = [_WORD_FIXES.get(w, w) for w in text.split()]
    return " ".join(words)


def state_code(text: str) -> str | None:
    cleaned = re.sub(r"[^A-Za-z ]", "", text).strip().upper()
    cleaned = " ".join(cleaned.split())
    if cleaned in US_STATE_CODES:
        return cleaned
    return _NAME_TO_CODE.get(cleaned)


class _Gazetteer:
    def __init__(self) -> None:
        primary: dict[tuple[str, str], list[tuple[float, float]]] = {}
        secondary: dict[tuple[str, str], list[tuple[float, float]]] = {}
        by_zip: dict[str, tuple[float, float]] = {}
        points: list[tuple[float, float]] = []
        for rec in zipcodes.list_all():
            if rec.get("zip_code_type") == "MILITARY" or not rec.get("lat") or not rec.get("long"):
                continue
            try:
                lat, lon = float(rec["lat"]), float(rec["long"])
            except ValueError:
                continue
            state = rec["state"]
            by_zip[rec["zip_code"]] = (lat, lon)
            points.append((lat, lon))
            primary.setdefault((normalize_city(rec["city"]), state), []).append((lat, lon))
            for alt in rec.get("acceptable_cities") or []:
                secondary.setdefault((normalize_city(alt), state), []).append((lat, lon))

        self._cities: dict[tuple[str, str], tuple[float, float]] = {}
        for table in (secondary, primary):  # primary overrides secondary
            for key, pts in table.items():
                arr = np.asarray(pts)
                self._cities[key] = (float(arr[:, 0].mean()), float(arr[:, 1].mean()))
        # Spelling variants such as "De Forest" / "DeForest".
        self._compact: dict[tuple[str, str], tuple[float, float]] = {}
        for (city, state), value in self._cities.items():
            self._compact.setdefault((city.replace(" ", ""), state), value)
        self._zips = by_zip
        self._tree = cKDTree(to_xyz(*np.asarray(points).T))

    def city(self, city: str, state: str) -> tuple[float, float] | None:
        key = (normalize_city(city), state)
        hit = self._cities.get(key)
        if hit is None:
            hit = self._compact.get((key[0].replace(" ", ""), state))
        return hit

    def zip(self, code: str) -> tuple[float, float] | None:
        return self._zips.get(code)

    def distance_to_nearest_zip(self, lat: float, lon: float) -> float:
        dist, _ = self._tree.query(to_xyz([lat], [lon])[0])
        return float(dist)


_lock = threading.Lock()
_gazetteer: _Gazetteer | None = None


def gazetteer() -> _Gazetteer:
    global _gazetteer
    if _gazetteer is None:
        with _lock:
            if _gazetteer is None:
                _gazetteer = _Gazetteer()
    return _gazetteer


def city_centroid(city: str, state: str) -> tuple[float, float] | None:
    """Centroid of the ZIP codes of a city, or None when unknown."""
    return gazetteer().city(city, state.upper())


def is_in_usa(lat: float, lon: float) -> bool:
    if not (17.0 <= lat <= 72.0 and -180.0 <= lon <= -64.0):
        return False
    return gazetteer().distance_to_nearest_zip(lat, lon) <= USA_MAX_DISTANCE_MILES


def _split_city_state(text: str) -> tuple[str, str] | None:
    parts = [p.strip() for p in text.split(",") if p.strip()]
    while parts and " ".join(re.sub(r"[^A-Za-z ]", " ", parts[-1]).upper().split()) in _COUNTRY_SUFFIXES:
        parts.pop()
    if not parts:
        return None
    if len(parts) >= 2:
        match = _STATE_ZIP_RE.match(parts[-1])
        code = state_code(match.group(1)) if match else None
        if code:
            return parts[-2], code
        return None
    tokens = parts[0].replace(".", " ").split()
    for take in (1, 2, 3):
        if len(tokens) > take:
            code = state_code(" ".join(tokens[-take:]))
            if code:
                return " ".join(tokens[:-take]), code
    return None


def _check_coordinates(lat: float, lon: float, label: str) -> None:
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        raise LocationError(f"Coordinates out of range: {label}")
    if not is_in_usa(lat, lon):
        raise LocationError(f"Location is not in the USA: {label}")


def resolve_offline(value) -> Location | None:
    """Resolve coordinates, ZIP codes and "City, ST" without any network call.

    Returns None when the text needs the geocoding provider. Raises
    LocationError for malformed or non-US coordinates.
    """
    if isinstance(value, dict):
        try:
            lat = float(value.get("lat", value.get("latitude")))
            lon = float(value.get("lon", value.get("lng", value.get("longitude"))))
        except (TypeError, ValueError):
            raise LocationError("Location object needs numeric 'lat' and 'lon'.") from None
        _check_coordinates(lat, lon, f"{lat},{lon}")
        return Location(lat, lon, f"{lat:.5f},{lon:.5f}", "coordinates")

    if not isinstance(value, str):
        raise LocationError("Location must be a string or an object with 'lat' and 'lon'.")
    text = " ".join(value.split())
    if not text:
        raise LocationError("Location is empty.")
    if len(text) > 200:
        raise LocationError("Location is too long.")

    match = _COORD_RE.match(text)
    if match:
        lat, lon = float(match.group(1)), float(match.group(2))
        _check_coordinates(lat, lon, text)
        return Location(lat, lon, f"{lat:.5f},{lon:.5f}", "coordinates")

    match = _ZIP_RE.match(text)
    if match:
        hit = gazetteer().zip(match.group(1))
        if hit is None:
            raise LocationError(f"Unknown ZIP code: {match.group(1)}")
        return Location(hit[0], hit[1], match.group(1), "gazetteer")

    split = _split_city_state(text)
    if split:
        city, code = split
        hit = gazetteer().city(city, code)
        if hit:
            return Location(hit[0], hit[1], f"{city.strip().title()}, {code}", "gazetteer")
    return None
