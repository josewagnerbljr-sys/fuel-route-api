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
      "tank_gallons_on_arrival": 22.652, "gallons_purchased": 27.348
    }
  ],
  "cost": {
    "total_miles": 800.94,
    "total_gallons": 80.094,
    "total_cost_usd": 234.12,
    "average_price_per_gallon": 2.923,
    "mpg": 10,
    "stops_used": 3
  },
  "vehicle": {"max_range_miles": 500, "mpg": 10, "starting_fuel_gallons": 50},
  "meta": {"external_api_calls": 1, "cached": false}
}
```

### `GET /api/route/map/`

Returns an HTML page with an interactive map (Leaflet + OpenStreetMap tiles) showing the route and fuel stops.

### `GET /api/health/`

Simple health check with station count.

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
