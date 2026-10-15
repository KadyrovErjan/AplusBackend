from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.conf import settings


class CustomUserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("Email обязателен")

        email = self.normalize_email(email)
        extra_fields.setdefault("username", email.split("@")[0])

        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("username", email.split("@")[0])
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True")

        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True")

        return self.create_user(email, password, **extra_fields)


class CustomUser(AbstractUser):
    telegram_id = models.BigIntegerField(unique=True, null=True, blank=True, verbose_name="Telegram ID")
    telegram_username = models.CharField(max_length=64, null=True, blank=True, verbose_name="Telegram username")

    phone = models.CharField(max_length=20, null=True, blank=True, verbose_name="Телефон")
    surname = models.CharField(max_length=50, null=True, blank=True, verbose_name="Фамилия")
    language = models.CharField(max_length=5, default="ru", verbose_name="Язык")

    ROLE_CHOICES = [
        ("driver", "Водитель"),
        ("mechanic", "Механик"),
        ("admin", "Администратор"),
    ]
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default="driver", verbose_name="Роль")

    STATUS_CHOICES = [
        ("registration", "Регистрация"),
        ("pending_vehicle", "Ожидание назначения ТС"),
        ("active", "Активен"),
    ]
    status = models.CharField(max_length=30, choices=STATUS_CHOICES, default="registration", verbose_name="Статус")

    birthdate = models.DateField(null=True, blank=True, verbose_name="Дата рождения")
    passport_id = models.CharField(max_length=20, null=True, blank=True, verbose_name="Паспорт")
    iin = models.CharField(max_length=14, null=True, blank=True, verbose_name="ИИН")
    address = models.TextField(null=True, blank=True, verbose_name="Адрес проживания")

    passport_front = models.ImageField(upload_to="users/passport_front/", null=True, blank=True, verbose_name="Паспорт (лицевая сторона)")
    passport_back = models.ImageField(upload_to="users/passport_back/", null=True, blank=True, verbose_name="Паспорт (обратная сторона)")
    driver_license_front = models.ImageField(upload_to="users/driver_license_front/", null=True, blank=True, verbose_name="Водительское удостоверение (лицевая)")
    driver_license_back = models.ImageField(upload_to="users/driver_license_back/", null=True, blank=True, verbose_name="Водительское удостоверение (обратная)")
    selfie = models.ImageField(upload_to="users/selfies/", null=True, blank=True, verbose_name="Селфи")

    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Дата создания")

    email = models.EmailField(unique=True, verbose_name="Email")
    username = models.CharField(max_length=150, blank=True, verbose_name="Имя пользователя")

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = CustomUserManager()

    class Meta:
        verbose_name = "Пользователь"
        verbose_name_plural = "Пользователи"

    def __str__(self):
        first = (self.first_name or "").strip()
        last = (self.surname or self.last_name or "").strip()
        full_name = f"{first} {last}".strip()
        return f"{full_name or self.email} | {self.get_role_display()}"

from .shift_settings import ShiftKPISettings
