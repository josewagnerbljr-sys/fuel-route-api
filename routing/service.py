"""Trip planning: locations -> route -> stations on the route -> cheapest fuel plan."""

from __future__ import annotations

import time
from decimal import ROUND_HALF_UP, Decimal

import numpy as np
from django.conf import settings

from . import geo, geometry, providers
from .planner import InfeasibleRoute, plan_purchases
from .stations import get_index


class NoFuelPlan(Exception):
    """No sequence of stations lets the vehicle reach the destination."""


def _cents(value: float) -> Decimal:
    return Decimal(repr(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def resolve_location(value, stats: dict) -> geo.Location:
    location = geo.resolve_offline(value)
    if location is not None:
        return location
    return providers.geocode(" ".join(str(value).split()), stats)


def _station_payload(index, i: int) -> dict:
    row = index.rows[i]
    return {
        "station_id": row[0],
        "opis_id": row[1],
        "name": row[2],
        "address": row[3],
        "city": row[4],
        "state": row[5],
        "latitude": round(row[7], 5),
        "longitude": round(row[8], 5),
    }


def _plan_with_widening(index, route_xyz, route_miles, total_miles, rng, mpg, start_fuel):
    last_error: InfeasibleRoute | None = None
    corridor_mean = None
    steps = sorted(settings.CORRIDOR_MILES)
    idx, offsets, marks = index.near_route(route_xyz, route_miles, steps[-1])
    for corridor in steps:
        keep = offsets <= corridor
        st, off, mk = idx[keep], offsets[keep], marks[keep]
        # Sort along the route; for the same mile marker, highest price first so that
        # the cheaper station of a tie is the one that gets used.
        order = np.lexsort((-index.price[st], mk))
        st, off, mk = st[order], off[order], mk[order]
        try:
            purchases = plan_purchases(
                mk.tolist(), index.price[st].tolist(), total_miles, rng, mpg, start_fuel
            )
        except InfeasibleRoute as exc:
            last_error = exc
            continue
        start_price = None
        if len(st):
            corridor_mean = float(index.price[st].mean())
            window = mk <= mk.min() + settings.START_PRICE_WINDOW_MILES
            start_price = float(index.price[st][window].min())
        return purchases, st, off, corridor, start_price, corridor_mean
    raise NoFuelPlan(str(last_error))


def plan_trip(start_input, finish_input, start_fuel_gallons: float | None = None) -> dict:
    began = time.perf_counter()
    stats: dict = {"external_calls": 0}

    start = resolve_location(start_input, stats)
    finish = resolve_location(finish_input, stats)
    route, from_cache = providers.fetch_route(start, finish, stats)

    rng = float(settings.VEHICLE_RANGE_MILES)
    mpg = float(settings.VEHICLE_MPG)
    capacity = rng / mpg
    if start_fuel_gallons is None:
        start_fuel_gallons = settings.VEHICLE_START_FUEL_GALLONS
    start_tank = capacity if start_fuel_gallons is None else min(float(start_fuel_gallons), capacity)

    coords, cum = geometry.clean_polyline(route.coords)
    if len(coords) < 2 or cum[-1] <= 0:
        coords = np.vstack((coords[:1], coords[:1] + 1e-6)) if len(coords) else np.zeros((2, 2))
        cum = np.array([0.0, 1e-6])
    # Report OSRM's own distance; scale the polyline so mile markers agree with it.
    total_miles = route.distance_miles
    cum_scaled = cum * (total_miles / cum[-1]) if cum[-1] > 0 and total_miles > 0 else cum

    stops: list[dict] = []
    corridor_used = None
    start_price = None
    corridor_mean = None
    if total_miles > 0.05:
        sample, sample_miles = geometry.resample(coords, cum_scaled, settings.ROUTE_SAMPLE_MILES)
        route_xyz = geometry.to_xyz(sample[:, 0], sample[:, 1])
        index = get_index()
        try:
            purchases, st, off, corridor_used, start_price, corridor_mean = _plan_with_widening(
                index, route_xyz, sample_miles, total_miles, rng, mpg, start_tank
            )
        except NoFuelPlan as exc:
            raise NoFuelPlan(
                "No sequence of fuel stations keeps the vehicle within its "
                f"{rng:g}-mile range on this route. {exc}"
            ) from None
        for order, p in enumerate(purchases, start=1):
            station = _station_payload(index, int(st[p.index]))
            cost = _cents(p.gallons * p.price)
            stops.append({
                "order": order,
                **station,
                "price_per_gallon": round(p.price, 4),
                "miles_from_start": round(p.mile, 2),
                "distance_from_route_miles": round(float(off[p.index]), 2),
                "tank_gallons_on_arrival": round(p.tank_on_arrival, 3),
                "gallons_purchased": round(p.gallons, 3),
                "cost_usd": float(cost),
            })

    trip_gallons = total_miles / mpg
    # The fuel in the tank at departure was bought at a station near the origin;
    # only the part burned on this trip is billed.
    start_used = min(start_tank, trip_gallons)
    start_cost = _cents(start_used * start_price) if start_price is not None else Decimal("0.00")
    stops_cost = sum((Decimal(str(s["cost_usd"])) for s in stops), Decimal("0.00"))
    total_cost = start_cost + stops_cost
    gallons_bought = sum(s["gallons_purchased"] for s in stops)

    savings = _savings(trip_gallons, corridor_mean, total_cost)

    keep = geometry.thin_indices(cum_scaled, settings.GEOMETRY_THIN_MILES)
    line = [[round(float(coords[i, 1]), 5), round(float(coords[i, 0]), 5)] for i in keep]

    features = [
        {
            "type": "Feature",
            "properties": {
                "kind": "route",
                "distance_miles": round(total_miles, 2),
                "duration_hours": round(route.duration_seconds / 3600.0, 2),
            },
            "geometry": {"type": "LineString", "coordinates": line},
        },
        _point_feature("start", start),
        _point_feature("finish", finish),
    ]
    for s in stops:
        features.append({
            "type": "Feature",
            "properties": {
                "kind": "fuel_stop",
                "order": s["order"],
                "name": s["name"],
                "city": s["city"],
                "state": s["state"],
                "price_per_gallon": s["price_per_gallon"],
                "gallons_purchased": s["gallons_purchased"],
                "cost_usd": s["cost_usd"],
                "miles_from_start": s["miles_from_start"],
            },
            "geometry": {"type": "Point", "coordinates": [s["longitude"], s["latitude"]]},
        })

    return {
        "start": _location_payload(start_input, start),
        "finish": _location_payload(finish_input, finish),
        "route": {
            "distance_miles": round(total_miles, 2),
            "duration_hours": round(route.duration_seconds / 3600.0, 2),
        },
        "fuel_stops": stops,
        "fuel": {
            "vehicle_range_miles": rng,
            "miles_per_gallon": mpg,
            "tank_capacity_gallons": round(capacity, 3),
            "trip_gallons_required": round(trip_gallons, 3),
            "starting_tank_gallons": round(start_tank, 3),
            "starting_tank_price_per_gallon": round(start_price, 4) if start_price is not None else None,
            "starting_tank_cost_usd": float(start_cost),
            "gallons_purchased": round(gallons_bought, 3),
            "stops_cost_usd": float(stops_cost),
            "total_cost_usd": float(total_cost),
            "average_price_per_gallon": round(float(total_cost) / trip_gallons, 4) if trip_gallons else None,
        },
        "savings": savings,
        "map": {"type": "FeatureCollection", "features": features},
        "meta": {
            "external_calls": stats["external_calls"],
            "route_cached": from_cache,
            "station_search_radius_miles": corridor_used,
            "elapsed_ms": round((time.perf_counter() - began) * 1000.0, 1),
        },
    }


def _savings(trip_gallons: float, mean_price, total_cost: Decimal) -> dict:
    """Compare the plan with buying every gallon at the average price along the route."""
    if mean_price is None or trip_gallons <= 0:
        return {"baseline_price_per_gallon": None, "baseline_cost_usd": None,
                "saved_usd": None, "saved_percent": None}
    baseline = _cents(trip_gallons * mean_price)
    saved = baseline - total_cost
    percent = float(saved / baseline * 100) if baseline > 0 else None
    return {
        "baseline_price_per_gallon": round(mean_price, 4),
        "baseline_cost_usd": float(baseline),
        "saved_usd": float(saved),
        "saved_percent": round(percent, 2) if percent is not None else None,
    }


def _point_feature(kind: str, loc: geo.Location) -> dict:
    return {
        "type": "Feature",
        "properties": {"kind": kind, "label": loc.label},
        "geometry": {"type": "Point", "coordinates": [round(loc.lon, 5), round(loc.lat, 5)]},
    }


def _location_payload(raw, loc: geo.Location) -> dict:
    query = raw if isinstance(raw, str) else loc.label
    return {"query": " ".join(query.split()), "label": loc.label,
            "lat": round(loc.lat, 5), "lon": round(loc.lon, 5), "resolved_by": loc.source}
