import random

import numpy as np

from routing import geometry
from routing.stations import get_index

from .base import StationsTestCase


class CorridorMatching(StationsTestCase):
    def test_1000_random_routes_match_brute_force(self):
        index = get_index()
        rng = random.Random(77)
        for n in range(1000):
            lat0, lon0 = rng.uniform(28, 47), rng.uniform(-120, -75)
            lat1, lon1 = lat0 + rng.uniform(-3, 3), lon0 + rng.uniform(-4, 4)
            count = rng.randrange(2, 60)
            t = np.linspace(0, 1, count)
            coords = np.column_stack((lat0 + (lat1 - lat0) * t, lon0 + (lon1 - lon0) * t))
            coords, cum = geometry.clean_polyline(coords)
            pts, miles = geometry.resample(coords, cum, 0.5)
            xyz = geometry.to_xyz(pts[:, 0], pts[:, 1])
            radius = rng.choice([5.0, 15.0, 30.0])

            idx, off, mark = index.near_route(xyz, miles, radius)

            diff = index.xyz[:, None, :] - xyz[None, ::1, :]
            dist = np.sqrt((diff ** 2).sum(axis=2))
            best = dist.min(axis=1)
            expected = np.nonzero(best <= radius)[0]
            self.assertEqual(sorted(idx.tolist()), expected.tolist(), f"route {n}")
            np.testing.assert_allclose(off, best[idx], atol=1e-6)
            np.testing.assert_allclose(mark, miles[dist[idx].argmin(axis=1)], atol=1e-6)
