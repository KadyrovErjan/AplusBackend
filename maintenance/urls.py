from rest_framework.routers import SimpleRouter

from .views import IssueReportViewSet

router = SimpleRouter()
router.register(r"issues", IssueReportViewSet, basename="maintenance-issues")

urlpatterns = router.urls
