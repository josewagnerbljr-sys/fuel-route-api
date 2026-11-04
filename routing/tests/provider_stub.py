"""Local HTTP server that returns OSRM and Nominatim style responses.

Used by the tests so the HTTP client code runs without reaching the public
services. Routes follow the great circle between the two points with a small
lateral offset and are deterministic.
"""

from __future__ import annotations

import json
import math
import re
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from routing.geometry import EARTH_RADIUS_MILES

LANDMARKS = {
    "space needle, seattle": (47.6205, -122.3493),
    "times square": (40.7580, -73.9855),
    "eiffel tower": (48.8584, 2.2945),
}

_ROUTE_RE = re.compile(r"^/route/v1/driving/(-?[\d.]+),(-?[\d.]+);(-?[\d.]+),(-?[\d.]+)$")


def great_circle_points(lat1, lon1, lat2, lon2, step_miles=0.4, offset_deg=0.03):
    p1, l1, p2, l2 = map(math.radians, (lat1, lon1, lat2, lon2))
    v1 = (math.cos(p1) * math.cos(l1), math.cos(p1) * math.sin(l1), math.sin(p1))
    v2 = (math.cos(p2) * math.cos(l2), math.cos(p2) * math.sin(l2), math.sin(p2))
    dot = max(-1.0, min(1.0, sum(a * b for a, b in zip(v1, v2))))
    omega = math.acos(dot)
    length = omega * EARTH_RADIUS_MILES
    n = max(1, int(length / step_miles))
    points = []
    for k in range(n + 1):
        t = k / n
        if omega < 1e-12:
            v = v1
        else:
            a = math.sin((1 - t) * omega) / math.sin(omega)
            b = math.sin(t * omega) / math.sin(omega)
            v = tuple(a * x + b * y for x, y in zip(v1, v2))
        lat = math.degrees(math.asin(max(-1.0, min(1.0, v[2]))))
        lon = math.degrees(math.atan2(v[1], v[0]))
        envelope = math.sin(math.pi * t)
        lat += offset_deg * envelope * math.sin(2 * math.pi * t * max(1.0, length / 250.0))
        points.append((lat, lon))
    return points, length


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _send(self, status: int, body: bytes, ctype="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        stub: ProviderStub = self.server.stub
        parsed = urlparse(self.path)
        path = unquote(self.path.split("?", 1)[0])
        with stub.lock:
            stub.calls[path.split("/")[1] if path.count("/") > 1 else path] += 1
            mode = stub.mode

        if path.startswith("/route/"):
            match = _ROUTE_RE.match(path)
            if mode == "slow":
                time.sleep(stub.delay)
            if mode == "error500":
                return self._send(500, b'{"code":"Error"}')
            if mode == "badjson":
                return self._send(200, b"<html>not json</html>", "text/html")
            if mode == "badshape":
                return self._send(200, b'{"code":"Ok","routes":[{}]}')
            if mode == "noroute":
                return self._send(400, b'{"code":"NoRoute","message":"Impossible route"}')
            if mode == "noroute200":
                return self._send(200, b'{"code":"NoRoute","routes":[]}')
            if not match:
                return self._send(400, b'{"code":"InvalidUrl"}')
            lon1, lat1, lon2, lat2 = map(float, match.groups())
            points, length = great_circle_points(lat1, lon1, lat2, lon2)
            # polyline length (with offset) is what a router would report as distance
            dist = sum(
                math.hypot((points[i + 1][0] - points[i][0]) * 69.0,
                           (points[i + 1][1] - points[i][1]) * 69.0 * math.cos(math.radians(points[i][0])))
                for i in range(len(points) - 1)
            )
            meters = max(dist, length) * 1609.344
            body = json.dumps({
                "code": "Ok",
                "routes": [{
                    "distance": meters,
                    "duration": meters / 1609.344 / 58.0 * 3600.0,
                    "geometry": {"type": "LineString", "coordinates": [[lo, la] for la, lo in points]},
                }],
                "waypoints": [],
            }).encode()
            return self._send(200, body)

        if path == "/search":
            q = parse_qs(parsed.query).get("q", [""])[0].strip().lower()
            if mode == "geocode500":
                return self._send(500, b"{}")
            hit = LANDMARKS.get(q)
            body = json.dumps([] if hit is None else [{
                "lat": str(hit[0]), "lon": str(hit[1]), "display_name": q.title(),
            }]).encode()
            return self._send(200, body)

        self._send(404, b"{}")


class ProviderStub:
    def __init__(self, delay: float = 2.0):
        self.mode = "ok"
        self.delay = delay
        self.calls: Counter = Counter()
        self.lock = threading.Lock()
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.daemon_threads = True
        self._server.request_queue_size = 256
        self._server.stub = self
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def start(self) -> "ProviderStub":
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def reset(self) -> None:
        with self.lock:
            self.mode = "ok"
            self.calls.clear()
