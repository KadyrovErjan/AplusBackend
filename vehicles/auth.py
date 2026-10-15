import jwt
from django.conf import settings
from jwt import InvalidTokenError


def decode_jwt(token: str):
    """
    Проверяем JWT токен.
    Возвращаем payload если токен валиден.
    """
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
        return payload
    except InvalidTokenError:
        return None
