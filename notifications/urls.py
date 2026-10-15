from django.urls import path
from notifications.views import (
    NotificationListView,
    NotificationMarkReadView,
)

urlpatterns = [
    path("", NotificationListView.as_view()),
    path("<int:pk>/read/", NotificationMarkReadView.as_view()),
]
