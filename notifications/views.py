from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from django.shortcuts import get_object_or_404
from django.db.models import Q
from django.utils import timezone
from datetime import timedelta

from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi

from notifications.models import Notification
from notifications.serializers import NotificationSerializer


class NotificationListView(APIView):
    """
    GET /api/notifications/
    Возвращает сгруппированный список уведомлений.
    """

    @swagger_auto_schema(
        operation_summary="Получить список уведомлений (с группировкой)",
        manual_parameters=[
            openapi.Parameter(
                "filter",
                openapi.IN_QUERY,
                type=openapi.TYPE_STRING,
                enum=["all", "unread"],
                description="Фильтр: 'all' (все) или 'unread' (только непрочитанные)"
            ),
            openapi.Parameter(
                "search",
                openapi.IN_QUERY,
                type=openapi.TYPE_STRING,
                description="Поиск по заголовку или тексту"
            ),
        ]
    )
    def get(self, request):
        qs = Notification.objects.all().order_by("-created_at")

        search_query = request.query_params.get("search")
        if search_query:
            qs = qs.filter(
                Q(title__icontains=search_query) |
                Q(message__icontains=search_query)
            )

        filter_type = request.query_params.get("filter")
        if filter_type == "unread":
            qs = qs.filter(is_read=False)

        total_unread_count = Notification.objects.filter(is_read=False).count()

        qs = qs[:100]

        serializer = NotificationSerializer(qs, many=True)
        serialized_data = serializer.data

        groups = {
            "today": [],
            "yesterday": [],
            "week": [],
            "earlier": []
        }

        now = timezone.localtime(timezone.now())
        today_date = now.date()
        yesterday_date = today_date - timedelta(days=1)
        week_ago_date = today_date - timedelta(days=7)

        for i, obj in enumerate(qs):
            item_json = serialized_data[i]

            obj_date = timezone.localtime(obj.created_at).date()

            if obj_date == today_date:
                groups["today"].append(item_json)
            elif obj_date == yesterday_date:
                groups["yesterday"].append(item_json)
            elif obj_date >= week_ago_date:
                groups["week"].append(item_json)
            else:
                groups["earlier"].append(item_json)

        final_groups = []

        if groups["today"]:
            final_groups.append({"title": "Сегодня", "data": groups["today"]})

        if groups["yesterday"]:
            final_groups.append({"title": "Вчера", "data": groups["yesterday"]})

        if groups["week"]:
            final_groups.append({"title": "На этой неделе", "data": groups["week"]})

        if groups["earlier"]:
            final_groups.append({"title": "Ранее", "data": groups["earlier"]})

        return Response({
            "count_unread": total_unread_count,
            "groups": final_groups
        })


class NotificationMarkReadView(APIView):
    @swagger_auto_schema(operation_summary="Прочитать уведомление")
    def post(self, request, pk):
        notif = get_object_or_404(Notification, pk=pk)
        if not notif.is_read:
            notif.is_read = True
            notif.save(update_fields=["is_read"])
        return Response({"status": "ok"})