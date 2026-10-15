from rest_framework import serializers
from django.utils.timezone import now
from .models import Notification


class NotificationSerializer(serializers.ModelSerializer):
    created_at = serializers.SerializerMethodField()

    class Meta:
        model = Notification
        fields = ("id", "type", "title", "message", "is_read", "created_at")

    def get_created_at(self, obj):
        """
        Превращает дату в человекочитаемый формат:
        - < 1 мин: "1 мин назад"
        - < 1 час: "N мин назад"
        - < 24 часа: "N час назад"
        - < 30 дней: "N дней назад"
        - иначе: "25-12-2025 12:30"
        """
        diff = now() - obj.created_at
        seconds = diff.total_seconds()

        if seconds < 60:
            return "1 мин назад"

        minutes = int(seconds // 60)
        if minutes < 60:
            return f"{minutes} мин назад"

        hours = int(seconds // 3600)
        if hours < 24:
            return f"{hours} час назад"

        days = diff.days
        if days < 30:
            return f"{days} дней назад"

        if days < 365:
            months = int(days // 30)
            return f"{months} месяц назад"

        return obj.created_at.strftime("%d-%m-%Y %H:%M")