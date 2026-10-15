from django.urls import path
from .views import (
    VehicleExcelExportView,
    RepairExcelExportView,
    FuelExcelExportView,
    AnnualAnalyticsExportView,
    TripsExcelExportView,
)

urlpatterns = [
    path('vehicles/', VehicleExcelExportView.as_view(), name='export-vehicles'),
    path('repairs/', RepairExcelExportView.as_view(), name='export-repairs'),
    path('fuels/', FuelExcelExportView.as_view(), name='export-fuels'),
    path('annual/', AnnualAnalyticsExportView.as_view(), name='export-annual'),
    path('trips/', TripsExcelExportView.as_view(), name='export-trips'),
]