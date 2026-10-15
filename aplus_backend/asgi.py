import os
from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
import vehicles.routing

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "aplus_backend.settings")

application = ProtocolTypeRouter({
    "http": get_asgi_application(),
    "websocket": URLRouter(
        vehicles.routing.websocket_urlpatterns
    ),
})
