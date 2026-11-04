"""In-memory station index, loaded once per process from the database."""

from __future__ import annotations

import threading

import numpy as np
from scipy.spatial import cKDTree

from .geometry import to_xyz
from .models import FuelStation


class StationIndex:
    def __init__(self, rows: list[tuple]):
        # rows: (id, opis_id, name, address, city, state, price, lat, lon)
        self.rows = rows
        self.count = len(rows)
        self.price = np.array([float(r[6]) for r in rows], dtype=float)
        self.lat = np.array([r[7] for r in rows], dtype=float)
        self.lon = np.array([r[8] for r in rows], dtype=float)
        self.xyz = to_xyz(self.lat, self.lon) if rows else np.empty((0, 3))

    @classmethod
    def from_db(cls) -> "StationIndex":
        rows = list(
            FuelStation.objects.order_by("id").values_list(
                "id", "opis_id", "name", "address", "city", "state",
                "retail_price", "latitude", "longitude",
            )
        )
        return cls(rows)

    def near_route(self, route_xyz: np.ndarray, route_miles: np.ndarray, max_offset: float):
        """Stations within ``max_offset`` miles of the route.

        Returns (station_indexes, offset_miles, mile_marker), where the mile
        marker is the position of the closest route sample.
        """
        if not self.count:
            empty = np.empty(0)
            return empty.astype(int), empty, empty
        tree = cKDTree(route_xyz)
        dist, nearest = tree.query(self.xyz, k=1, distance_upper_bound=max_offset)
        mask = np.isfinite(dist)
        idx = np.nonzero(mask)[0]
        return idx, dist[mask], route_miles[nearest[mask]]


_lock = threading.Lock()
_index: StationIndex | None = None


def get_index() -> StationIndex:
    global _index
    if _index is None:
        with _lock:
            if _index is None:
                _index = StationIndex.from_db()
    return _index


def reset_index() -> None:
    global _index
    with _lock:
        _index = None
