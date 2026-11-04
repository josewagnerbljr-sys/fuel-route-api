"""Spherical geometry helpers shared by the routing code (numpy only)."""

from __future__ import annotations

import math

import numpy as np

EARTH_RADIUS_MILES = 3958.7613
METERS_PER_MILE = 1609.344


def haversine_miles(lat1, lon1, lat2, lon2):
    """Great-circle distance in miles. Accepts scalars or numpy arrays."""
    phi1 = np.radians(lat1)
    phi2 = np.radians(lat2)
    dphi = phi2 - phi1
    dlmb = np.radians(np.asarray(lon2, dtype=float) - np.asarray(lon1, dtype=float))
    a = np.sin(dphi / 2.0) ** 2 + np.cos(phi1) * np.cos(phi2) * np.sin(dlmb / 2.0) ** 2
    return 2.0 * EARTH_RADIUS_MILES * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def to_xyz(lat, lon) -> np.ndarray:
    """Convert degrees to 3-D points on a sphere of Earth radius (miles).

    Euclidean distance between two such points is the chord length, which is
    within 0.01% of the arc length for distances below 100 miles.
    """
    phi = np.radians(np.asarray(lat, dtype=float))
    lmb = np.radians(np.asarray(lon, dtype=float))
    cos_phi = np.cos(phi)
    return EARTH_RADIUS_MILES * np.column_stack(
        (cos_phi * np.cos(lmb), cos_phi * np.sin(lmb), np.sin(phi))
    )


def clean_polyline(coords: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Drop repeated vertices and return (coords, cumulative_miles).

    ``coords`` is an (N, 2) array of (lat, lon). The result has strictly
    increasing cumulative distances, starting at 0.
    """
    coords = np.asarray(coords, dtype=float)
    if len(coords) < 2:
        return coords, np.zeros(len(coords))
    steps = haversine_miles(coords[:-1, 0], coords[:-1, 1], coords[1:, 0], coords[1:, 1])
    keep = np.concatenate(([True], steps > 1e-9))
    coords = coords[keep]
    steps = steps[keep[1:]]
    cum = np.concatenate(([0.0], np.cumsum(steps)))
    return coords, cum


def resample(coords: np.ndarray, cum: np.ndarray, step_miles: float) -> tuple[np.ndarray, np.ndarray]:
    """Return points spaced evenly (about ``step_miles``) along the polyline."""
    total = float(cum[-1])
    count = max(2, int(math.ceil(total / step_miles)) + 1)
    grid = np.linspace(0.0, total, count)
    lat = np.interp(grid, cum, coords[:, 0])
    lon = np.interp(grid, cum, coords[:, 1])
    return np.column_stack((lat, lon)), grid


def thin_indices(cum: np.ndarray, spacing_miles: float) -> np.ndarray:
    """Indices of vertices to keep so that kept points are ~spacing apart."""
    if len(cum) <= 2:
        return np.arange(len(cum))
    bins = np.floor(cum / spacing_miles).astype(np.int64)
    _, first = np.unique(bins, return_index=True)
    idx = np.unique(np.concatenate((first, [len(cum) - 1])))
    return idx
