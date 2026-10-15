from django.contrib import admin
from django.urls import path, include
from drf_yasg.views import get_schema_view
from drf_yasg import openapi
from rest_framework.permissions import AllowAny
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from django.conf import settings
from django.conf.urls.static import static
from vehicles.views import GlobalMapView
from fuels.views import FuelZoneListView

schema_view = get_schema_view(
    openapi.Info(
        title="APlus API Docs",
        default_version="v1",
        description="API documentation",
    ),
    public=True,
    permission_classes=(AllowAny,),
)

urlpatterns = [
      path('admin/', admin.site.urls),

      path("api/users/", include("users.urls")),
      path("api/vehicles/", include("vehicles.urls")),
      path("api/maintenance/", include("maintenance.urls")),
      path("api/notifications/", include("notifications.urls")),
      path("api/trips/", include("trips.urls")),
      path("api/fuels/", include("fuels.urls")),
      path('api/reports/', include('reports.urls')),

      path("api/map/data/", GlobalMapView.as_view(), name="map-data"),

      path("api/objects/", FuelZoneListView.as_view(), name="objects-list-root"),

      path("api/docs/", schema_view.with_ui("swagger", cache_timeout=0), name="swagger-ui"),
      path("api/redoc/", schema_view.with_ui("redoc", cache_timeout=0), name="redoc-ui"),

      path("api/token/", TokenObtainPairView.as_view()),
      path("api/token/refresh/", TokenRefreshView.as_view()),

  ] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
