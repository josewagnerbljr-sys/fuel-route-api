# Fuel Route API

Django API that takes a start and a finish location in the USA and returns:

- the driving route as GeoJSON, plus an HTML map page of the route,
- the cheapest places to fuel along that route for a vehicle with a 500-mile range,
- the total money spent on fuel at 10 miles per gallon.

Fuel prices come from the supplied CSV (`fuel-prices-for-be-assessment.csv`). Routing uses the free
public [OSRM](https://project-osrm.org/) server. Free-text places that cannot be resolved locally
use the free [Nominatim](https://nominatim.org/) geocoder.

## Setup

Python 3.12 or newer.

```bash
python -m venv venv
source venv/bin/activate            # Windows: venv\Scripts\activate
pip install -r requirements.txt
python manage.py migrate
python manage.py load_stations
python manage.py runserver
```

`load_stations` reads the CSV in the project root (about two seconds).

Example request:

```bash
curl -X POST http://127.0.0.1:8000/api/route/ \
  -H "Content-Type: application/json" \
  -d '{"start": "Chicago, IL", "finish": "Dallas, TX"}'
```

Map page:
`http://127.0.0.1:8000/api/route/map/?start=Chicago,%20IL&finish=Dallas,%20TX`

Gunicorn: `gunicorn fuelroute.wsgi:application -w 4 --threads 8`.

## Endpoints

### `POST /api/route/` (also `GET` with query parameters)

| Field | Required | Description |
| --- | --- | --- |
| `start` | yes | Start location. |
| `finish` | yes | Finish location. |
| `starting_fuel_gallons` | no | Fuel in the tank at departure. Default is a full tank (50 gal). |

A location can be any of:

- `"City, ST"` or `"City, State name"` (street prefixes and `USA` suffixes are ignored),
- a 5-digit ZIP code,
- `"lat,lon"` or `{"lat": 41.88, "lon": -87.63}`,
- free text such as `"Space Needle, Seattle"` (resolved by Nominatim).

Response (geometry shortened):

```json
{
  "start":  {"query": "Chicago, IL", "label": "Chicago, IL", "lat": 41.85453, "lon": -87.67469, "resolved_by": "gazetteer"},
  "finish": {"query": "Dallas, TX", "label": "Dallas, TX", "lat": 32.78941, "lon": -96.7896, "resolved_by": "gazetteer"},
  "route":  {"distance_miles": 800.94, "duration_hours": 13.81},
  "fuel_stops": [
    {
      "order": 1, "station_id": 1320, "opis_id": 52326, "name": "MR FUEL #715",
      "address": "I-70, EXIT 203", "city": "Foristell", "state": "MO",
      "latitude": 38.7626, "longitude": -90.9343,
      "price_per_gallon": 2.9257, "miles_from_start": 273.48, "distance_from_route_miles": 8.55,
      "tank_gallons_on_arrival": 22.652, "gallons_purchased": 26.644, "cost_usd": 77.95
    }
  ],
  "fuel": {
    "vehicle_range_miles": 500.0, "miles_per_gallon": 10.0, "tank_capacity_gallons": 50.0,
    "trip_gallons_required": 80.094,
    "starting_tank_gallons": 50.0, "starting_tank_price_per_gallon": 3.059, "starting_tank_cost_usd": 152.95,
    "gallons_purchased": 30.094, "stops_cost_usd": 87.61,
    "total_cost_usd": 240.56, "average_price_per_gallon": 3.0035
  },
  "map": {
    "type": "FeatureCollection",
    "features": ["route LineString", "start Point", "finish Point", "one Point per fuel stop"],
    "html_url": "http://127.0.0.1:8000/api/route/map/?start=Chicago%2C+IL&finish=Dallas%2C+TX"
  },
  "meta": {"external_calls": 1, "route_cached": false, "station_search_radius_miles": 15.0, "elapsed_ms": 41.2}
}
```

`map` is standard GeoJSON and works with Leaflet, Mapbox or geojson.io. The route line has about one point every 0.3 miles.

Errors are JSON: `{"error": {"code": "...", "message": "..."}}`.

| Status | Code | Cause |
| --- | --- | --- |
| 400 | `invalid_request` | Missing field, bad JSON, bad number. |
| 400 | `invalid_location` | Malformed coordinates or a point that is not in the USA. |
| 422 | `location_not_found` | Free text with no US match. |
| 422 | `route_not_found` | No drivable route between the two points. |
| 422 | `no_fuel_plan` | No chain of stations keeps the vehicle within range. |
| 502 | `upstream_error` | The routing or geocoding service failed or timed out. |

### `GET /api/route/map/`

Same parameters as the route endpoint. Returns an HTML page with the route, the fuel stops and a summary table (Leaflet and OpenStreetMap tiles, loaded from public CDNs).

### `GET /api/health/`

Returns `{"status": "ok", "stations_loaded": 6626}`.

## How it works

1. Locations. `"City, ST"`, ZIP and coordinates are resolved offline from the ZIP gazetteer in the `zipcodes` package. Only free text reaches Nominatim.
2. Route. One OSRM call (`overview=full`, GeoJSON geometry). Results are cached per origin/destination pair, so a repeated request makes no external call. A normal request makes one external call; each free-text place adds one geocoder call.
3. Stations on the route. The route is resampled every 0.5 miles and a KD-tree finds every station within 15 miles of it, together with its mile marker. If no feasible plan exists, the radius widens to 30 and then 60 miles. The radius used is reported in `meta`.
4. Cheapest plan. For stations ordered along the route the planner applies a greedy rule that gives the minimum cost: at each station, if a strictly cheaper station is reachable on a full tank, buy only enough to get there; otherwise fill the tank. The destination counts as free fuel, so the last stop buys only what is needed. The tests compare the planner with a brute-force dynamic program on 1,000 random instances.
5. Cost. Each stop reports gallons, price and cost rounded to cents. `stops_cost_usd` is their sum.

Everything after the routing call runs in memory (stations are loaded once per process) and takes a few milliseconds.

## Assumptions

- Range is 500 miles and consumption is 10 mpg, so the tank holds 50 gallons. Both are settings.
- The vehicle leaves with a full tank. That fuel was bought at a station near the origin, so the part burned on the trip is billed at the cheapest price found within 25 miles of the first station on the route (`starting_tank_price_per_gallon`). `total_cost_usd` is that amount plus `stops_cost_usd`, and covers every gallon the trip burns. Pass `starting_fuel_gallons` to change the starting fuel.
- The price file has no coordinates. Each station is placed at the centre of its city (mean of the city's ZIP code centroids), so station positions are accurate to a few miles. That is why the default search radius is 15 miles.
- The file lists some stations several times with different prices. They are merged by OPIS id and the lowest price is kept. Rows in Canadian provinces are skipped. Result: 8,151 rows become 6,626 US stations.
- Detours are not priced; a station is a candidate when it is inside the search radius.
- A location counts as being in the USA when it is within 60 miles of a US ZIP code centroid.

## Configuration

Environment variables (all optional):

| Variable | Default | Purpose |
| --- | --- | --- |
| `DJANGO_SECRET_KEY` | development value | Set it outside local development. |
| `DJANGO_DEBUG` | `0` | Debug mode. |
| `DJANGO_ALLOWED_HOSTS` | `localhost,127.0.0.1,[::1]` | Comma-separated hosts. |
| `FUELROUTE_DB_PATH` | `db.sqlite3` | SQLite file location. |
| `OSRM_BASE_URL` | `https://router.project-osrm.org` | Routing server. |
| `NOMINATIM_BASE_URL` | `https://nominatim.openstreetmap.org` | Geocoder. |
| `NOMINATIM_USER_AGENT` | `fuel-route-api/1.0` | Sent to Nominatim. |
| `PROVIDER_TIMEOUT_SECONDS` | `20` | Timeout for each external call. |
| `ROUTE_CACHE_SECONDS` | `86400` | Route and geocode cache lifetime. |
| `VEHICLE_RANGE_MILES` | `500` | Range on a full tank. |
| `VEHICLE_MPG` | `10` | Fuel economy. |
| `VEHICLE_START_FUEL_GALLONS` | full tank | Fuel at departure. |

The public OSRM and Nominatim servers are rate limited and meant for light use. Use your own instances for sustained traffic. Geocoder calls are paced to one per second.

## Tests

```bash
python manage.py test routing
```

The suite starts a local server that returns OSRM and Nominatim style responses, so the HTTP client code runs without network access. It covers:

- planner rules and failure cases, plus 1,000 random instances checked against an exact solver,
- location parsing, including every city in the price file,
- the station loader (Canadian rows, duplicates, bad rows, reloads),
- corridor matching against brute force on 1,000 random routes,
- endpoint behaviour, input validation, upstream failures, timeouts and caching,
- 1,000 sequential requests across 48 cities, 1,000 malformed inputs (no 5xx), and 400 requests from 16 threads. Every successful answer is checked for the same invariants: the tank never runs dry or overflows, stops are in order and within range, and the costs add up.

## Project layout

```
fuelroute/                  settings, urls, wsgi/asgi
routing/
  geo.py                    offline place resolution
  geometry.py               distance, resampling, thinning
  stations.py               in-memory station index and corridor search
  planner.py                cheapest fuel purchase plan
  providers.py              OSRM and Nominatim clients, caching
  service.py                request orchestration and response building
  views.py, urls.py         endpoints
  templates/routing/map.html
  management/commands/load_stations.py
  tests/
fuel-prices-for-be-assessment.csv
```
