from django.urls import path
from rest_framework.routers import SimpleRouter
from .views import (
    CustomRegisterView, CustomLoginView, LogoutView, CookieTokenRefreshView,
    FullRegisterView, TelegramAdminStartView, UserProfileView, ApproveVehicleRequestView,
    VehicleRequestsListView, PendingRequestsView,
    DriverViewSet, RejectVehicleRequestView, PendingRequestDetailView, EndShiftView, StartShiftView,
    DeleteDriverView, FuelReportView, DriverFuelReportCreateView, ShiftDetailsView
)

router = SimpleRouter()
router.register(r"drivers", DriverViewSet, basename="drivers")

urlpatterns = [
    path("register/", CustomRegisterView.as_view()),
    path("login/", CustomLoginView.as_view()),
    path("login/refresh/", CookieTokenRefreshView.as_view()),
    path("logout/", LogoutView.as_view()),

    path("full_register/", FullRegisterView.as_view()),
    path("telegram/admin/start/", TelegramAdminStartView.as_view()),
    path("profile/<int:telegram_id>/", UserProfileView.as_view()),

    path("vehicle_requests/", VehicleRequestsListView.as_view()),
    path("requests/pending/", PendingRequestsView.as_view()),
    path("requests/pending/<int:request_id>/", PendingRequestDetailView.as_view()),
    path("requests/approve/", ApproveVehicleRequestView.as_view()),
    path("requests/reject/", RejectVehicleRequestView.as_view()),

    path("shifts/start/", StartShiftView.as_view()),
    path("shifts/end/", EndShiftView.as_view()),
    path("shifts/<int:shift_id>/", ShiftDetailsView.as_view()),

    path("drivers/delete/<int:driver_id>/", DeleteDriverView.as_view()),

    path("reports/fuel/", DriverFuelReportCreateView.as_view()),
]

urlpatterns += router.urls
