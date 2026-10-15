import pytest
from django.conf import settings

@pytest.fixture(scope="session", autouse=True)
def configure_sqlite_test_db():
    """
    Полностью переопределяем БД для pytest,
    чтобы Django НЕ требовал ATOMIC_REQUESTS и MIRROR.
    """
    settings.DATABASES["default"] = {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
        "TEST": {
            "NAME": ":memory:",
            "MIRROR": None,
            "CREATE_DB": True,
        },
        "ATOMIC_REQUESTS": False,
        "AUTOCOMMIT": True,
        "CONN_MAX_AGE": 0,
        "OPTIONS": {},
    }
