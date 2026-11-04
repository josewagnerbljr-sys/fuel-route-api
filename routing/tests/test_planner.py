import random

from django.test import SimpleTestCase

from routing.planner import InfeasibleRoute, plan_purchases

RANGE, MPG = 500.0, 10.0
CAP = RANGE / MPG


def total_cost(purchases):
    return sum(p.gallons * p.price for p in purchases)


def replay(purchases, miles, total, f0):
    """Drive the plan and check the tank never runs dry or overflows."""
    by_index = {p.index: p for p in purchases}
    fuel, pos = f0, 0.0
    points = sorted(set(list(by_index) + []))
    for i in points:
        fuel -= (miles[i] - pos) / MPG
        assert fuel >= -1e-6, f"ran dry before station {i}"
        fuel += by_index[i].gallons
        assert fuel <= CAP + 1e-6, f"tank overflow at station {i}"
        pos = miles[i]
    fuel -= (total - pos) / MPG
    assert fuel >= -1e-6, "ran dry before the destination"


def exact_cost(miles, prices, total, f0_units, unit_miles=5):
    """Dynamic programme over integer fuel units (0.5 gal when unit_miles=5, mpg=10)."""
    cap_units = int(RANGE / unit_miles)
    unit_gal = unit_miles / MPG
    pts = [0] + list(miles) + [total]
    inf = float("inf")
    dp = [inf] * (cap_units + 1)
    dp[min(f0_units, cap_units)] = 0.0
    for k in range(1, len(pts)):
        need = (pts[k] - pts[k - 1]) // unit_miles
        nxt = [inf] * (cap_units + 1)
        for f, c in enumerate(dp):
            if c < inf and f >= need:
                nxt[f - need] = min(nxt[f - need], c)
        dp = nxt
        if k == len(pts) - 1:
            break
        price = prices[k - 1]
        for f in range(1, cap_units + 1):  # buying is a running minimum over f-1
            if dp[f - 1] < inf:
                dp[f] = min(dp[f], dp[f - 1] + unit_gal * price)
    return min(dp)


class PlannerExamples(SimpleTestCase):
    def test_short_trip_needs_no_stop(self):
        self.assertEqual(plan_purchases([100, 200], [3.0, 3.2], 300, RANGE, MPG), [])

    def test_buys_only_what_is_needed(self):
        got = plan_purchases([400], [3.0], 700, RANGE, MPG)
        self.assertEqual(len(got), 1)
        # start with 50 gal, reach mile 400 with 10, need 30 more for the last 300 miles
        self.assertAlmostEqual(got[0].gallons, 20.0)
        self.assertAlmostEqual(got[0].tank_on_arrival, 10.0)

    def test_fills_up_at_cheap_station_before_expensive_one(self):
        got = plan_purchases([100, 300], [2.0, 4.0], 750, RANGE, MPG)
        self.assertEqual([p.index for p in got][0], 0)
        self.assertAlmostEqual(got[0].gallons, 50.0 - 40.0)  # arrives with 40, tops up to 50
        replay(got, [100, 300], 750, CAP)
        self.assertAlmostEqual(got[1].gallons, 15.0)  # last top-up at the dearer station, only what is needed

    def test_waits_for_cheaper_station_when_in_range(self):
        got = plan_purchases([300, 600], [4.0, 2.0], 1000, RANGE, MPG)
        # At mile 300 (arrive with 20 gal) only buy enough to reach mile 600.
        self.assertAlmostEqual(got[0].gallons, 10.0)
        replay(got, [300, 600], 1000, CAP)

    def test_equal_prices_do_not_create_tiny_stops(self):
        got = plan_purchases([480, 484, 488], [3.0, 3.0, 3.0], 900, RANGE, MPG)
        self.assertEqual(len(got), 1)

    def test_gap_larger_than_range(self):
        with self.assertRaises(InfeasibleRoute) as ctx:
            plan_purchases([100, 700], [3.0, 3.0], 800, RANGE, MPG)
        self.assertEqual(ctx.exception.at_mile, 100)

    def test_first_station_out_of_reach(self):
        with self.assertRaises(InfeasibleRoute):
            plan_purchases([600], [3.0], 700, RANGE, MPG)

    def test_destination_out_of_reach_from_last_station(self):
        with self.assertRaises(InfeasibleRoute):
            plan_purchases([100], [3.0], 700, RANGE, MPG)

    def test_empty_tank_start_requires_station_at_origin(self):
        with self.assertRaises(InfeasibleRoute):
            plan_purchases([10], [3.0], 100, RANGE, MPG, start_fuel_gallons=0)
        got = plan_purchases([0, 10], [3.0, 3.5], 100, RANGE, MPG, start_fuel_gallons=0)
        self.assertAlmostEqual(sum(p.gallons for p in got), 10.0)

    def test_stations_at_route_ends(self):
        got = plan_purchases([0, 300], [3.0, 2.0], 700, RANGE, MPG)
        replay(got, [0, 300], 700, CAP)


class PlannerAgainstExactSolver(SimpleTestCase):
    def test_1000_random_instances_match_exact_dynamic_programme(self):
        rng = random.Random(20240611)
        checked = 0
        for _ in range(1000):
            total = rng.randrange(100, 3000, 5)
            n = rng.randrange(0, 14)
            miles = sorted(rng.randrange(0, total + 1, 5) for _ in range(n))
            prices = [rng.choice([2.5, 2.75, 3.0, 3.0, 3.25, 3.5, 4.0, 4.5]) + rng.randrange(0, 4) * 0.01
                      for _ in range(n)]
            f0 = rng.choice([50.0, 50.0, 25.0, 10.0])
            try:
                got = plan_purchases(miles, prices, total, RANGE, MPG, f0)
            except InfeasibleRoute:
                points = [0, *miles, total]
                gaps = [points[k + 1] - points[k] for k in range(len(points) - 1)]
                self.assertTrue(
                    gaps[0] > f0 * MPG or max(gaps[1:] or [0]) > RANGE,
                    f"declared infeasible but gaps fit: {miles} {total}",
                )
                self.assertEqual(exact_cost(miles, prices, total, int(f0 * 2)), float("inf"))
                continue
            replay(got, miles, total, f0)
            self.assertAlmostEqual(total_cost(got), exact_cost(miles, prices, total, int(f0 * 2)), places=6,
                                   msg=f"{miles} {prices} {total} {f0}")
            checked += 1
        self.assertGreater(checked, 300)
