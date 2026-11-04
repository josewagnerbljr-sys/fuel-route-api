"""Geometry helpers for route resampling and distance calculations."""

import math
from typing import List, Tuple


def haversine_miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in miles."""
    R = 3958.8
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def resample_route(coords: List[Tuple[float, float]], step_miles: float = 5.0) -> List[Tuple[float, float]]:
    """Resample a polyline to roughly equal spacing."""
    if not coords or len(coords) < 2:
        return coords
    result = [coords[0]]
    accumulated = 0.0
    for i in range(1, len(coords)):
        d = haversine_miles(coords[i-1][0], coords[i-1][1], coords[i][0], coords[i][1])
        accumulated += d
        if accumulated >= step_miles:
            result.append(coords[i])
            accumulated = 0.0
    if result[-1] != coords[-1]:
        result.append(coords[-1])
    return result
