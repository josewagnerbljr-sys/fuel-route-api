from django.urls import include, path

from routing import views

urlpatterns = [
    path("", views.home_view, name="home"),
    path("api/", include("routing.urls")),
]
