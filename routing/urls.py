from django.urls import path
from .views import RouteAPIView, HealthAPIView, MapView

urlpatterns = [
    path('route/', RouteAPIView.as_view(), name='route'),
    path('route/map/', MapView.as_view(), name='route-map'),
    path('health/', HealthAPIView.as_view(), name='health'),
]
