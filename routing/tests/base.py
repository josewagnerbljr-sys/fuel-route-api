from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings

from routing import providers
from routing.stations import reset_index

from .provider_stub import ProviderStub

CITIES = [
    "New York, NY", "Los Angeles, CA", "Chicago, IL", "Houston, TX", "Phoenix, AZ", "Philadelphia, PA",
    "San Antonio, TX", "San Diego, CA", "Dallas, TX", "Jacksonville, FL", "Columbus, OH", "Charlotte, NC",
    "Indianapolis, IN", "Seattle, WA", "Denver, CO", "Washington, DC", "Boston, MA", "Nashville, TN",
    "Oklahoma City, OK", "Las Vegas, NV", "Portland, OR", "Memphis, TN", "Louisville, KY", "Baltimore, MD",
    "Milwaukee, WI", "Albuquerque, NM", "Tucson, AZ", "Kansas City, MO", "Atlanta, GA", "Omaha, NE",
    "Minneapolis, MN", "New Orleans, LA", "Cleveland, OH", "Tampa, FL", "St. Louis, MO", "Pittsburgh, PA",
    "Cincinnati, OH", "Salt Lake City, UT", "Boise, ID", "Billings, MT", "Fargo, ND", "Little Rock, AR",
    "Birmingham, AL", "Richmond, VA", "Detroit, MI", "Sacramento, CA", "Amarillo, TX", "Cheyenne, WY",
]


class StationsTestCase(TestCase):
    """Loads the shipped CSV once per class and resets in-memory caches."""

    @classmethod
    def setUpTestData(cls):
        call_command("load_stations", verbosity=0)

    def setUp(self):
        reset_index()
        cache.clear()


class ProviderTestCase(StationsTestCase):
    """Adds a local OSRM/Nominatim test server and points the settings at it."""

    stub: ProviderStub

    @classmethod
    def setUpClass(cls):
        cls.stub = ProviderStub().start()
        cls._override = override_settings(
            OSRM_BASE_URL=cls.stub.url,
            NOMINATIM_BASE_URL=cls.stub.url,
            PROVIDER_TIMEOUT_SECONDS=5,
        )
        cls._override.enable()
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls._override.disable()
        cls.stub.stop()
        providers.reset_session()

    def setUp(self):
        super().setUp()
        self.stub.reset()
        providers.reset_session()
        providers._next_geocode_at = 0.0
