from django.urls import path

from . import views

urlpatterns = [
    path("route/", views.route_view, name="route"),
    path("route/map/", views.map_view, name="route-map"),
    path("health/", views.health_view, name="health"),
]
