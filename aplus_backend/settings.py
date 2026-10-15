import os
from pathlib import Path
from dotenv import load_dotenv
from urllib.parse import urlparse
from datetime import timedelta
from celery.schedules import crontab
from .jazzmin import JAZZMIN_SETTINGS, JAZZMIN_UI_TWEAKS
import sentry_sdk
from sentry_sdk.integrations.django import DjangoIntegration


BASE_DIR = Path(__file__).resolve().parent.parent


load_dotenv()
SECRET_KEY = os.getenv('SECRET_KEY')

DEBUG = os.getenv("DEBUG", "False") == "True"

ALLOWED_HOSTS = [
    "www.aplus-dev.site",
    "aplus-dev.site",
    "37.27.5.77",
    "localhost",
    "127.0.0.1",
    "127.0.0.0"
]


INSTALLED_APPS = [
    'jazzmin',
    'users',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    "rest_framework_simplejwt",
    'vehicles',
    'maintenance',
    "channels",
    "notifications",
    "trips",
    "fuels",
    'rest_framework_simplejwt.token_blacklist',
    'corsheaders',
    "drf_yasg",
    "reports",
]
MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'aplus_backend.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'aplus_backend.wsgi.application'

ASGI_APPLICATION = "aplus_backend.asgi.application"

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv("POSTGRES_DB"),
        'USER': os.getenv("POSTGRES_USER"),
        'PASSWORD': os.getenv("POSTGRES_PASSWORD"),
        'HOST': os.getenv("POSTGRES_HOST", "localhost"),
        'PORT': os.getenv("POSTGRES_PORT", "5432"),
    }
}
PILOT_API_URL = os.getenv("PILOT_API_URL")
PILOT_LOGIN = os.getenv("PILOT_LOGIN")
PILOT_PASSWORD = os.getenv("PILOT_PASSWORD")
ADMIN_TELEGRAM_SECRET = os.getenv("ADMIN_TELEGRAM_SECRET")

ADMINS = [("Ulanbek", "uislambekuulu@gmail.com")]


AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "users.authentication.CookieJWTAuthentication",
    ),
}

SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
USE_X_FORWARDED_HOST = True
USE_X_FORWARDED_PORT = True

SECURE_SSL_REDIRECT = False

SWAGGER_SETTINGS = {
    'USE_SESSION_AUTH': False,
    'SECURITY_DEFINITIONS': {
        'Bearer': {
            'type': 'apiKey',
            'name': 'Authorization',
            'in': 'header'
        }
    }
}

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,

    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
        },
    },

    "root": {
        "handlers": ["console"],
        "level": "ERROR",
    },

    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "ERROR",
            "propagate": False,
        },
    },
}


if not DEBUG:
    sentry_sdk.init(
        dsn=os.getenv("SENTRY_DSN"),
        integrations=[DjangoIntegration()],
        traces_sample_rate=1.0,
        send_default_pii=True,
    )


REDIS_HOST = os.getenv("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [(REDIS_HOST, REDIS_PORT)],
        },
    },
}


LANGUAGE_CODE = 'ru'

TIME_ZONE = 'Asia/Bishkek'

USE_I18N = True

USE_L10N = True

USE_TZ = True



STATIC_URL = "/static/"

if DEBUG:
    STATICFILES_DIRS = [BASE_DIR / "static"]
else:
    STATIC_ROOT = BASE_DIR / "static"

MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')


SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=30),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "UPDATE_LAST_LOGIN": False,
    "AUTH_HEADER_TYPES": ("Bearer",),
}

CORS_ORIGIN_ALLOW_ALL = False
CORS_ALLOW_CREDENTIALS = True

CORS_ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "https://aplus-dev.site",
    "https://aplus-dev-sepia.vercel.app",
    "https://www.aplus-dev.site",
]

CORS_ALLOW_METHODS = [
    "GET",
    "POST",
    "PUT",
    "PATCH",
    "DELETE",
    "OPTIONS",
]

CORS_ALLOW_HEADERS = [
    "content-type",
    "authorization",
    "x-requested-with",
    "accept",
    "origin",
    "cache-control",
    "pragma",
]



SESSION_COOKIE_DOMAIN = ".aplus-dev.site"
CSRF_COOKIE_DOMAIN = ".aplus-dev.site"

SESSION_COOKIE_SAMESITE = "None"
CSRF_COOKIE_SAMESITE = "None"

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

CSRF_TRUSTED_ORIGINS = [
    "https://www.aplus-dev.site",
    "https://aplus-dev.site",
    "https://aplus-dev-sepia.vercel.app",
]


CELERY_BROKER_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_TIMEZONE = "Asia/Bishkek"
AUTO_SHIFT_CLOSE_NOT_BEFORE_HOUR_LOCAL = int(os.getenv("AUTO_SHIFT_CLOSE_NOT_BEFORE_HOUR_LOCAL", "18"))
WORKDAY_START_HOUR_LOCAL = int(os.getenv("WORKDAY_START_HOUR_LOCAL", "9"))
WORKDAY_WARMUP_MINUTES = int(os.getenv("WORKDAY_WARMUP_MINUTES", "30"))
WORKDAY_NORM_MINUTES = int(os.getenv("WORKDAY_NORM_MINUTES", "480"))

BOT_API_URL = os.getenv('BOT_API_URL')
BOT_SHIFT_NOTIFICATION_ENDPOINT = os.getenv("BOT_SHIFT_NOTIFICATION_ENDPOINT", "/send_shift_notification")


CELERY_BEAT_SCHEDULE = {
    "check-low-fuel-every-5-min": {
        "task": "notifications.tasks.check_low_fuel",
        "schedule": crontab(minute="*/5"),
    },
    "check-oil-service-every-day": {
        "task": "notifications.tasks.check_oil_service",
        "schedule": crontab(hour=0, minute=30),
    },
    "import-fuel-every-10-min": {
        "task": "fuels.tasks.import_fuel_task",
        "schedule": crontab(minute="*/10"),
        "kwargs": {"days": 1},
    },
    'sync-vehicles-every-minute': {
        'task': 'vehicles.tasks.run_sync_vehicles',
        'schedule': 60.0,
    },
    "sync-trips-every-15-min": {
        "task": "trips.tasks.sync_trips_task",
        "schedule": crontab(minute="*/15"),
    },
    "monitor-vehicles-for-shifts-every-5-min": {
        "task": "users.tasks.monitor_vehicles_for_shift_creation",
        "schedule": crontab(minute="*/5"),
    },
    "check-idle-vehicles-every-5-min": {
        "task": "trips.tasks.check_idle_vehicles_task",
        "schedule": crontab(minute="*/5"),
    },
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

AUTH_USER_MODEL = "users.CustomUser"
