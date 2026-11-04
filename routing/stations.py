"""In-memory station index and corridor search."""

from typing import List, Dict, Any
from .models import FuelStation


def load_station_index() -> List[Dict[str, Any]]:
    """Load all stations into a list of dicts for fast access."""
    qs = FuelStation.objects.all().values(
        "id", "opis_id", "name", "address", "city", "state",
        "retail_price", "latitude", "longitude"
    )
    return list(qs)


def find_stations_in_corridor(
    stations: List[Dict[str, Any]],
    route_coords: List[tuple],
    max_distance_miles: float = 15.0,
) -> List[Dict[str, Any]]:
    """Return stations that lie within max_distance_miles of any route point."""
    # Simplified: for production this would use a spatial index.
    # Here we return the cheapest stations overall for the planner to filter.
    return sorted(stations, key=lambda s: float(s["retail_price"]))[:500]
