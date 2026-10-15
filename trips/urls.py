from django.urls import path
from trips.views import TripsHistoryView, TripsStatisticsView, TripDetailView

urlpatterns = [
    path('statistics/', TripsStatisticsView.as_view()),
    path('history/', TripsHistoryView.as_view()),
    path('history/<int:pk>/', TripDetailView.as_view()),
]