import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "fuelroute.settings")

application = get_wsgi_application()


def _warm_up() -> None:
    """Load the gazetteer and station index before the first request."""
    try:
        from routing import geo
        from routing.stations import get_index

        geo.gazetteer()
        get_index()
    except Exception:  # database not migrated yet: the first request reports it
        pass


_warm_up()
