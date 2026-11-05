"""Django settings for the fuel route API."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_list(name: str, default: str) -> list[str]:
    return [item.strip() for item in os.environ.get(name, default).split(",") if item.strip()]


SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-secret-key-set-DJANGO_SECRET_KEY-in-production")
DEBUG = _env_bool("DJANGO_DEBUG", False)
ALLOWED_HOSTS = _env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1],testserver")

INSTALLED_APPS = [
    "django.contrib.staticfiles",
    "routing",
]

# Map tiles are served by OpenStreetMap, which requires a Referer header.
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "fuelroute.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": []},
    },
]

WSGI_APPLICATION = "fuelroute.wsgi.application"
ASGI_APPLICATION = "fuelroute.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": os.environ.get("FUELROUTE_DB_PATH", str(BASE_DIR / "db.sqlite3")),
        "OPTIONS": {"timeout": 20},
    }
}

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "fuelroute-cache",
        "OPTIONS": {"MAX_ENTRIES": 500},
    }
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = False
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Vehicle
VEHICLE_RANGE_MILES = float(os.environ.get("VEHICLE_RANGE_MILES", 500))
VEHICLE_MPG = float(os.environ.get("VEHICLE_MPG", 10))
# Fuel in the tank when the trip starts. Empty value means a full tank.
_start_fuel = os.environ.get("VEHICLE_START_FUEL_GALLONS", "").strip()
VEHICLE_START_FUEL_GALLONS = float(_start_fuel) if _start_fuel else None

# Station matching
# Maximum distance (miles) between a station and the route. If no feasible
# plan exists with the first value, the next ones are tried in order.
CORRIDOR_MILES = (15.0, 30.0, 60.0)
# The tank at departure is billed at the cheapest station price found within this
# many miles of the first station on the route.
START_PRICE_WINDOW_MILES = 25.0
# Spacing (miles) of the points used to match stations to the route.
ROUTE_SAMPLE_MILES = 0.5
# Spacing (miles) of the points returned in the map geometry.
GEOMETRY_THIN_MILES = 0.3

# External providers (free, no key required)
OSRM_BASE_URL = os.environ.get("OSRM_BASE_URL", "https://router.project-osrm.org")
NOMINATIM_BASE_URL = os.environ.get("NOMINATIM_BASE_URL", "https://nominatim.openstreetmap.org")
NOMINATIM_USER_AGENT = os.environ.get("NOMINATIM_USER_AGENT", "fuel-route-api/1.0")
PROVIDER_TIMEOUT_SECONDS = float(os.environ.get("PROVIDER_TIMEOUT_SECONDS", 20))
ROUTE_CACHE_SECONDS = int(os.environ.get("ROUTE_CACHE_SECONDS", 86400))

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {"routing": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "WARNING")}},
}
