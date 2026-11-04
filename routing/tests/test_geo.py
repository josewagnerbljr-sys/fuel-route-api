import random

from django.test import SimpleTestCase

from routing import geo
from routing.geo import LocationError
from routing.geometry import clean_polyline, haversine_miles, resample, thin_indices

import numpy as np


class ResolveOffline(SimpleTestCase):
    def assertNear(self, loc, lat, lon, tol=0.6):
        self.assertAlmostEqual(loc.lat, lat, delta=tol)
        self.assertAlmostEqual(loc.lon, lon, delta=tol)

    def test_city_state_variants(self):
        for text in ["Chicago, IL", "chicago,il", "  Chicago ,  Illinois ", "Chicago IL", "Chicago, IL, USA",
                     "Chicago, Illinois 60601", "233 S Wacker Dr, Chicago, IL"]:
            loc = geo.resolve_offline(text)
            self.assertIsNotNone(loc, text)
            self.assertNear(loc, 41.88, -87.65)
            self.assertEqual(loc.source, "gazetteer")

    def test_multi_word_names_and_abbreviations(self):
        self.assertNear(geo.resolve_offline("St. Louis, MO"), 38.63, -90.25, 0.5)
        self.assertNear(geo.resolve_offline("Salt Lake City UT"), 40.76, -111.9, 0.5)
        self.assertNear(geo.resolve_offline("New York, New York"), 40.75, -73.95, 0.5)
        self.assertIsNotNone(geo.resolve_offline("De Forest, WI"))
        self.assertIsNotNone(geo.resolve_offline("DeForest, WI"))

    def test_zip(self):
        loc = geo.resolve_offline("60601")
        self.assertNear(loc, 41.88, -87.62, 0.2)
        self.assertIsNotNone(geo.resolve_offline("60601-1234"))
        with self.assertRaises(LocationError):
            geo.resolve_offline("00000")

    def test_coordinates(self):
        for text in ["41.88,-87.63", "41.88 -87.63", " 41.88 , -87.63 ", "41.88;-87.63"]:
            loc = geo.resolve_offline(text)
            self.assertEqual(loc.source, "coordinates")
            self.assertAlmostEqual(loc.lat, 41.88)
        loc = geo.resolve_offline({"lat": 41.88, "lon": -87.63})
        self.assertAlmostEqual(loc.lon, -87.63)
        loc = geo.resolve_offline({"latitude": "41.88", "lng": "-87.63"})
        self.assertAlmostEqual(loc.lon, -87.63)

    def test_rejections(self):
        for bad in ["51.05,-114.07", "19.43,-99.13", "48.85,2.29", "95,-87", "41.8,-200", {"lat": "x", "lon": 1},
                    {"lat": 1}, "", "   ", 5, ["Chicago, IL"], "x" * 300]:
            with self.assertRaises(LocationError, msg=repr(bad)):
                geo.resolve_offline(bad)

    def test_free_text_is_left_for_the_geocoder(self):
        for text in ["Space Needle", "Springfield", "Chicago, Narnia", "Nowhereville, TX"]:
            self.assertIsNone(geo.resolve_offline(text), text)

    def test_every_station_city_resolves(self):
        import csv
        from django.conf import settings
        path = settings.BASE_DIR / "fuel-prices-for-be-assessment.csv"
        with open(path, encoding="utf-8") as fh:
            pairs = {(" ".join(r["City"].split()), r["State"]) for r in csv.DictReader(fh)
                     if r["State"] in geo.US_STATE_CODES}
        missing = [p for p in pairs if geo.city_centroid(*p) is None]
        self.assertEqual(missing, [])

    def test_is_in_usa(self):
        self.assertTrue(geo.is_in_usa(41.88, -87.63))
        self.assertTrue(geo.is_in_usa(61.2, -149.9))   # Anchorage
        self.assertTrue(geo.is_in_usa(21.3, -157.85))  # Honolulu
        self.assertFalse(geo.is_in_usa(51.05, -114.07))  # Calgary
        self.assertFalse(geo.is_in_usa(0.0, 0.0))


class Geometry(SimpleTestCase):
    def test_haversine_known_distance(self):
        d = float(haversine_miles(41.8781, -87.6298, 32.7767, -96.7970))  # Chicago-Dallas
        self.assertAlmostEqual(d, 802.0, delta=6.0)
        self.assertAlmostEqual(float(haversine_miles(10, 10, 10, 10)), 0.0)

    def test_clean_polyline_drops_repeats(self):
        coords = np.array([[40, -80], [40, -80], [40.1, -80], [40.1, -80], [40.2, -80.0]])
        out, cum = clean_polyline(coords)
        self.assertEqual(len(out), 3)
        self.assertTrue(np.all(np.diff(cum) > 0))

    def test_resample_and_thin_keep_endpoints(self):
        rng = random.Random(3)
        for _ in range(200):
            n = rng.randrange(2, 400)
            lat = 35 + np.cumsum([rng.random() * 0.02 for _ in range(n)])
            lon = -100 + np.cumsum([rng.random() * 0.02 for _ in range(n)])
            coords, cum = clean_polyline(np.column_stack((lat, lon)))
            pts, miles = resample(coords, cum, 0.5)
            np.testing.assert_allclose(pts[0], coords[0])
            np.testing.assert_allclose(pts[-1], coords[-1])
            self.assertLessEqual(np.max(np.diff(miles)), 0.5 + 1e-9)
            keep = thin_indices(cum, 0.3)
            self.assertEqual(keep[0], 0)
            self.assertEqual(keep[-1], len(cum) - 1)
