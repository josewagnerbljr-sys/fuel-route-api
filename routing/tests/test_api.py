import json
import random
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

from django.test import Client, override_settings

from routing import providers

from .base import CITIES, ProviderTestCase


def post(client, body, **extra):
    return client.post("/api/route/", json.dumps(body), content_type="application/json", **extra)


def check_plan(case, data, rng=500.0, mpg=10.0):
    """Invariants every successful answer must satisfy."""
    cap = rng / mpg
    total = data["route"]["distance_miles"]
    fuel = data["fuel"]
    stops = data["fuel_stops"]
    case.assertEqual(fuel["vehicle_range_miles"], rng)
    case.assertEqual(fuel["miles_per_gallon"], mpg)
    case.assertAlmostEqual(fuel["trip_gallons_required"], total / mpg, delta=0.01)

    tank, pos = fuel["starting_tank_gallons"], 0.0
    cents = Decimal("0.00")
    for k, s in enumerate(stops, start=1):
        case.assertEqual(s["order"], k)
        case.assertGreaterEqual(s["miles_from_start"], pos)
        tank -= (s["miles_from_start"] - pos) / mpg
        case.assertGreaterEqual(tank, -0.01, "vehicle ran out of fuel")
        case.assertAlmostEqual(tank, s["tank_gallons_on_arrival"], delta=0.01)
        tank += s["gallons_purchased"]
        case.assertLessEqual(tank, cap + 0.01, "tank overflow")
        case.assertGreater(s["gallons_purchased"], 0)
        case.assertAlmostEqual(s["gallons_purchased"] * s["price_per_gallon"], s["cost_usd"], delta=0.02)
        case.assertLessEqual(s["distance_from_route_miles"], data["meta"]["station_search_radius_miles"] + 1e-6)
        cents += Decimal(str(s["cost_usd"]))
        pos = s["miles_from_start"]
    tank -= (total - pos) / mpg
    case.assertGreaterEqual(tank, -0.02, "vehicle did not reach the destination")
    case.assertEqual(float(cents), fuel["stops_cost_usd"])
    case.assertEqual(float(cents + Decimal(str(fuel["starting_tank_cost_usd"]))), fuel["total_cost_usd"])
    used = min(fuel["starting_tank_gallons"], fuel["trip_gallons_required"])
    if fuel["starting_tank_price_per_gallon"] is not None:
        case.assertAlmostEqual(fuel["starting_tank_cost_usd"], used * fuel["starting_tank_price_per_gallon"], delta=0.02)
    case.assertAlmostEqual(sum(s["gallons_purchased"] for s in stops), fuel["gallons_purchased"], delta=0.01)

    fc = data["map"]
    case.assertEqual(fc["type"], "FeatureCollection")
    kinds = Counter(f["properties"]["kind"] for f in fc["features"])
    case.assertEqual((kinds["route"], kinds["start"], kinds["finish"], kinds["fuel_stop"]), (1, 1, 1, len(stops)))
    line = fc["features"][0]["geometry"]["coordinates"]
    case.assertGreaterEqual(len(line), 2)


class RouteEndpoint(ProviderTestCase):
    def setUp(self):
        super().setUp()
        self.client = Client()

    def test_health(self):
        r = self.client.get("/api/health/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"status": "ok", "stations_loaded": 6626})

    def test_cross_country_plan(self):
        r = post(self.client, {"start": "Chicago, IL", "finish": "Dallas, TX"})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        check_plan(self, data)
        self.assertGreater(data["route"]["distance_miles"], 700)
        self.assertGreaterEqual(len(data["fuel_stops"]), 1)
        self.assertEqual(data["meta"]["external_calls"], 1)
        self.assertEqual(self.stub.calls["route"], 1)
        self.assertEqual(data["start"]["resolved_by"], "gazetteer")
        self.assertIn("/api/route/map/?", data["map"]["html_url"])

    def test_long_trip_has_several_stops_within_range(self):
        data = post(self.client, {"start": "Miami, FL", "finish": "Seattle, WA"}).json()
        check_plan(self, data)
        self.assertGreaterEqual(len(data["fuel_stops"]), 5)
        marks = [0.0] + [s["miles_from_start"] for s in data["fuel_stops"]] + [data["route"]["distance_miles"]]
        self.assertLessEqual(max(b - a for a, b in zip(marks, marks[1:])), 500.01)

    def test_short_trip_needs_no_stop(self):
        data = post(self.client, {"start": "Chicago, IL", "finish": "Milwaukee, WI"}).json()
        check_plan(self, data)
        self.assertEqual(data["fuel_stops"], [])
        self.assertEqual(data["fuel"]["stops_cost_usd"], 0.0)
        fuel = data["fuel"]
        self.assertAlmostEqual(fuel["total_cost_usd"], fuel["trip_gallons_required"] * fuel["starting_tank_price_per_gallon"], delta=0.02)
        self.assertGreater(fuel["total_cost_usd"], 0)

    def test_start_fuel_option_changes_cost(self):
        full = post(self.client, {"start": "Chicago, IL", "finish": "Milwaukee, WI", "starting_fuel_gallons": 2}).json()
        check_plan(self, full)
        self.assertGreaterEqual(len(full["fuel_stops"]), 1)
        self.assertGreater(full["fuel"]["stops_cost_usd"], 0)

    def test_empty_start_tank_is_reported_as_unplannable(self):
        # No station sits within 2 miles of this point in rural Wyoming.
        with override_settings(CORRIDOR_MILES=(2.0,)):
            r = post(self.client, {"start": "43.2,-107.9", "finish": "Billings, MT", "starting_fuel_gallons": 0})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["error"]["code"], "no_fuel_plan")
        # With a station at the origin the same request works and buys at mile 0.
        r = post(self.client, {"start": "Chicago, IL", "finish": "Milwaukee, WI", "starting_fuel_gallons": 0})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        check_plan(self, data)
        self.assertLess(data["fuel_stops"][0]["miles_from_start"], 1.0)

    def test_cheaper_than_naive_refuel_when_full(self):
        data = post(self.client, {"start": "New York, NY", "finish": "Los Angeles, CA"}).json()
        check_plan(self, data)
        naive = data["fuel"]["trip_gallons_required"] * 3.499  # dataset mean is about $3.50
        self.assertLess(data["fuel"]["total_cost_usd"], naive)
        self.assertAlmostEqual(data["fuel"]["average_price_per_gallon"] * data["fuel"]["trip_gallons_required"],
                               data["fuel"]["total_cost_usd"], delta=0.05)

    def test_second_identical_request_uses_cache(self):
        body = {"start": "Denver, CO", "finish": "Omaha, NE"}
        first = post(self.client, body).json()
        second = post(self.client, body).json()
        self.assertEqual(first["meta"]["external_calls"], 1)
        self.assertEqual(second["meta"]["external_calls"], 0)
        self.assertTrue(second["meta"]["route_cached"])
        self.assertEqual(first["fuel_stops"], second["fuel_stops"])
        self.assertEqual(self.stub.calls["route"], 1)

    def test_get_with_query_string(self):
        r = self.client.get("/api/route/", {"start": "Denver, CO", "finish": "Omaha, NE"})
        self.assertEqual(r.status_code, 200)
        check_plan(self, r.json())

    def test_form_encoded_post(self):
        r = self.client.post("/api/route/", {"start": "Denver, CO", "finish": "Omaha, NE"})
        self.assertEqual(r.status_code, 200)

    def test_coordinate_zip_and_object_inputs(self):
        for start, finish in [
            ("41.8781,-87.6298", "32.7767,-96.7970"),
            ("60601", "75201"),
            ({"lat": 41.8781, "lon": -87.6298}, {"lat": 32.7767, "lon": -96.797}),
            ("Chicago, Illinois", "Dallas, Texas"),
        ]:
            r = post(self.client, {"start": start, "finish": finish})
            self.assertEqual(r.status_code, 200, (start, finish))
            check_plan(self, r.json())

    def test_free_text_falls_back_to_geocoder(self):
        providers._MIN_GEOCODE_INTERVAL = 0.0
        try:
            data = post(self.client, {"start": "Space Needle, Seattle", "finish": "Times Square"}).json()
        finally:
            providers._MIN_GEOCODE_INTERVAL = 1.0
        check_plan(self, data)
        self.assertEqual(data["meta"]["external_calls"], 3)
        self.assertEqual(data["start"]["resolved_by"], "geocoder")

    def test_geocoder_results_are_cached(self):
        providers._MIN_GEOCODE_INTERVAL = 0.0
        try:
            post(self.client, {"start": "Space Needle, Seattle", "finish": "Boise, ID"})
            second = post(self.client, {"start": "Space Needle, Seattle", "finish": "Boise, ID"}).json()
        finally:
            providers._MIN_GEOCODE_INTERVAL = 1.0
        self.assertEqual(second["meta"]["external_calls"], 0)

    def test_map_page(self):
        r = self.client.get("/api/route/map/", {"start": "Denver, CO", "finish": "Omaha, NE"})
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        self.assertIn("leaflet", html)
        self.assertIn('id="plan-data"', html)
        self.assertIn("FeatureCollection", html)
        bad = self.client.get("/api/route/map/", {"start": "Denver, CO"})
        self.assertEqual(bad.status_code, 400)

    def test_planner_page_and_tutorial(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        for needle in ('id="route-form"', 'id="plan"', 'id="tour-card"', "Legend", 'translate="no"'):
            self.assertIn(needle, html)
        self.assertEqual(self.client.post("/").status_code, 405)

    def test_map_page_has_legend(self):
        r = self.client.get("/api/route/map/", {"start": "Denver, CO", "finish": "Omaha, NE"})
        self.assertIn("Legend", r.content.decode())

    def test_map_url_round_trips(self):
        data = post(self.client, {"start": "Denver, CO", "finish": "Omaha, NE"}).json()
        url = data["map"]["html_url"].split("testserver", 1)[1]
        self.assertEqual(self.client.get(url).status_code, 200)


class ErrorHandling(ProviderTestCase):
    def setUp(self):
        super().setUp()
        self.client = Client()

    def error(self, response, status, code):
        self.assertEqual(response.status_code, status, response.content[:200])
        self.assertEqual(response.json()["error"]["code"], code)

    def test_missing_and_malformed_input(self):
        self.error(post(self.client, {}), 400, "invalid_request")
        self.error(post(self.client, {"start": "Chicago, IL"}), 400, "invalid_request")
        self.error(post(self.client, {"start": "", "finish": "Dallas, TX"}), 400, "invalid_request")
        self.error(self.client.post("/api/route/", "{not json", content_type="application/json"), 400, "invalid_request")
        self.error(self.client.post("/api/route/", "[1,2]", content_type="application/json"), 400, "invalid_request")
        self.error(self.client.post("/api/route/", "x" * 20000, content_type="application/json"), 400, "invalid_request")
        self.error(post(self.client, {"start": "A, IL", "finish": "B", "starting_fuel_gallons": "lots"}), 400, "invalid_request")
        self.error(post(self.client, {"start": "A", "finish": "B", "starting_fuel_gallons": -1}), 400, "invalid_request")
        self.error(post(self.client, {"start": "A", "finish": "B", "starting_fuel_gallons": "nan"}), 400, "invalid_request")

    def test_locations_outside_usa(self):
        self.error(post(self.client, {"start": "51.05,-114.07", "finish": "Dallas, TX"}), 400, "invalid_location")
        self.error(post(self.client, {"start": 12, "finish": "Dallas, TX"}), 400, "invalid_location")
        self.error(post(self.client, {"start": "Eiffel Tower", "finish": "Dallas, TX"}), 422, "location_not_found")
        self.error(post(self.client, {"start": "Nowhereville, TX", "finish": "Dallas, TX"}), 422, "location_not_found")

    def test_method_not_allowed(self):
        self.assertEqual(self.client.put("/api/route/").status_code, 405)
        self.assertEqual(self.client.delete("/api/route/").status_code, 405)
        self.assertEqual(self.client.post("/api/health/").status_code, 405)

    def test_router_failures(self):
        body = {"start": "Chicago, IL", "finish": "Dallas, TX"}
        for mode, status, code in [
            ("noroute", 422, "route_not_found"),
            ("noroute200", 422, "route_not_found"),
            ("error500", 502, "upstream_error"),
            ("badjson", 502, "upstream_error"),
            ("badshape", 502, "upstream_error"),
        ]:
            self.stub.mode = mode
            providers.reset_session()
            self.error(post(self.client, body), status, code)

    @override_settings(PROVIDER_TIMEOUT_SECONDS=0.3)
    def test_router_timeout(self):
        self.stub.mode = "slow"
        self.stub.delay = 1.5
        self.error(post(self.client, {"start": "Chicago, IL", "finish": "Dallas, TX"}), 502, "upstream_error")

    def test_router_down(self):
        with override_settings(OSRM_BASE_URL="http://127.0.0.1:9"):
            providers.reset_session()
            self.error(post(self.client, {"start": "Chicago, IL", "finish": "Dallas, TX"}), 502, "upstream_error")

    def test_failures_are_not_cached(self):
        body = {"start": "Chicago, IL", "finish": "Dallas, TX"}
        self.stub.mode = "error500"
        providers.reset_session()
        self.error(post(self.client, body), 502, "upstream_error")
        self.stub.mode = "ok"
        self.assertEqual(post(self.client, body).status_code, 200)

    def test_geocoder_failure(self):
        providers._MIN_GEOCODE_INTERVAL = 0.0
        try:
            self.stub.mode = "geocode500"
            providers.reset_session()
            self.error(post(self.client, {"start": "Times Square", "finish": "Dallas, TX"}), 502, "upstream_error")
        finally:
            providers._MIN_GEOCODE_INTERVAL = 1.0

    @override_settings(VEHICLE_RANGE_MILES=40.0, CORRIDOR_MILES=(5.0,))
    def test_no_feasible_plan(self):
        self.error(post(self.client, {"start": "Chicago, IL", "finish": "Dallas, TX"}), 422, "no_fuel_plan")

    def test_corridor_widening_is_reported(self):
        with override_settings(CORRIDOR_MILES=(0.5, 15.0, 60.0)):
            data = post(self.client, {"start": "Chicago, IL", "finish": "Dallas, TX"}).json()
        self.assertGreater(data["meta"]["station_search_radius_miles"], 0.5)

    def test_station_table_empty(self):
        from routing.models import FuelStation
        from routing.stations import reset_index
        FuelStation.objects.all().delete()
        reset_index()
        self.error(post(self.client, {"start": "Chicago, IL", "finish": "Dallas, TX"}), 422, "no_fuel_plan")
        short = post(self.client, {"start": "Chicago, IL", "finish": "Milwaukee, WI"})
        self.assertEqual(short.status_code, 200)


class VolumeAndStress(ProviderTestCase):
    def test_1000_sequential_requests(self):
        rng = random.Random(5)
        client = Client()
        statuses = Counter()
        started = time.perf_counter()
        for _ in range(1000):
            a, b = rng.sample(CITIES, 2)
            r = post(client, {"start": a, "finish": b})
            statuses[r.status_code] += 1
            self.assertLess(r.status_code, 500, (a, b, r.content[:200]))
            if r.status_code == 200:
                check_plan(self, r.json())
        elapsed = time.perf_counter() - started
        print(f"\n1000 sequential requests: {dict(statuses)} in {elapsed:.1f}s")
        self.assertGreaterEqual(statuses[200], 950)
        self.assertLess(self.stub.calls["route"], 1000)  # repeated pairs came from cache

    def test_1000_invalid_inputs_never_fail_with_5xx(self):
        providers._MIN_GEOCODE_INTERVAL = 0.0  # random text reaches the geocoder; skip its 1 s pacing
        self.addCleanup(setattr, providers, "_MIN_GEOCODE_INTERVAL", 1.0)
        rng = random.Random(9)
        client = Client()
        alphabet = "abcXYZ 0123456789,;-.'\"<>{}[]\\/é中\n\t"
        for _ in range(1000):
            kind = rng.randrange(5)
            if kind == 0:
                body = {"start": "".join(rng.choice(alphabet) for _ in range(rng.randrange(0, 40))),
                        "finish": "".join(rng.choice(alphabet) for _ in range(rng.randrange(0, 40)))}
            elif kind == 1:
                body = {"start": rng.choice([None, 1, 2.5, True, [], {}, ["a"]]), "finish": rng.choice(CITIES)}
            elif kind == 2:
                body = {"start": f"{rng.uniform(-200, 200):.3f},{rng.uniform(-300, 300):.3f}", "finish": rng.choice(CITIES)}
            elif kind == 3:
                body = {"start": rng.choice(CITIES), "finish": rng.choice(CITIES),
                        "starting_fuel_gallons": rng.choice(["x", -5, 1e9, None, "", [], {}, "1e400", 12])}
            else:
                body = rng.choice([[], "text", 5, None, {"finish": "Dallas, TX"}])
            with override_settings(PROVIDER_TIMEOUT_SECONDS=2):
                r = post(client, body) if not isinstance(body, str) else client.post(
                    "/api/route/", body, content_type="application/json")
            self.assertLess(r.status_code, 500, (body, r.content[:200]))
            self.assertIn("application/json", r["Content-Type"])

    def test_concurrent_requests(self):
        pairs = random.Random(11).sample([(a, b) for a in CITIES for b in CITIES if a != b], 400)
        results = []
        lock = threading.Lock()

        def work(pair):
            client = Client()
            t = time.perf_counter()
            r = post(client, {"start": pair[0], "finish": pair[1]})
            dt = time.perf_counter() - t
            with lock:
                results.append((r.status_code, dt, r.json() if r.status_code == 200 else None))

        from routing import geo
        from routing.stations import get_index
        geo.gazetteer()
        get_index()  # the test database is bound to this thread's transaction
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=16) as pool:
            list(pool.map(work, pairs))
        elapsed = time.perf_counter() - started
        statuses = Counter(s for s, _, _ in results)
        times = sorted(dt for _, dt, _ in results)
        print(f"\n400 requests / 16 threads: {dict(statuses)} in {elapsed:.1f}s, "
              f"median {times[len(times)//2]*1000:.0f} ms, p95 {times[int(len(times)*.95)]*1000:.0f} ms")
        self.assertTrue(all(s < 500 for s in statuses))
        for status, _, data in results:
            if status == 200:
                check_plan(self, data)


class GeocoderPacing(ProviderTestCase):
    def test_queue_is_bounded(self):
        providers._next_geocode_at = time.monotonic() + 60
        r = post(Client(), {"start": "Space Needle, Seattle", "finish": "Dallas, TX"})
        self.assertEqual(r.status_code, 502)
        self.assertEqual(r.json()["error"]["code"], "upstream_error")
