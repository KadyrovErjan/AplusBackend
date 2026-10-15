from django.urls import path
from .views import (
    FuelListView,
    FuelZoneDetailView,
    FuelZoneListView,
    FuelZoneNamesView,
    FuelZoneVehiclesView,
    FuelZoneSimpleListView,
)

urlpatterns = [
    path("", FuelListView.as_view(), name="fuels-list"),
    path("names/", FuelZoneNamesView.as_view(), name="fuels-names"),
    path("zones/", FuelZoneSimpleListView.as_view(), name="fuels-zones-list"),
    path("<int:zone_id>/", FuelZoneDetailView.as_view(), name="fuels-zone-detail"),
]