from django.urls import path
from rest_framework.routers import DefaultRouter, SimpleRouter
from .views import (VehicleViewSet, DashboardStatsView, VehicleDetailView, VehicleRepairsView,
                    AvailableVehiclesView)

router = SimpleRouter()
router.register(r"", VehicleViewSet, basename="vehicles")

urlpatterns = [
    path("dashboard/", DashboardStatsView.as_view()),
    path("available/", AvailableVehiclesView.as_view()),
    path("detail/<int:vehicle_id>/", VehicleDetailView.as_view()),
]

urlpatterns += router.urls