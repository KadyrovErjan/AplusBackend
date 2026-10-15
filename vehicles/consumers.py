import json                                     
import jwt                                      
from django.conf import settings                
from channels.generic.websocket import AsyncWebsocketConsumer 
from channels.db import database_sync_to_async   
from django.contrib.auth import get_user_model   


class VehicleStatusConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        query = self.scope.get("query_string", b"").decode() 
        token = None                                        
        for part in query.split("&"):                       
            if part.startswith("token="):                   
                token = part.split("=", 1)[1]               
                break                                       

        if not token:
            await self.close(code=4001)
            return

        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
        except Exception:
            await self.close(code=4002)
            return

        user_id = payload.get("user_id")

        self.scope["user_id_from_token"] = user_id

        await self.channel_layer.group_add("vehicles", self.channel_name)

        vehicle_id = self.scope.get("url_route", {}).get("kwargs", {}).get("vehicle_id")
        if vehicle_id:
            await self.channel_layer.group_add(f"vehicle_{vehicle_id}", self.channel_name)

        await self.accept()

    async def disconnect(self, close_code):
        try:
            await self.channel_layer.group_discard("vehicles", self.channel_name)
        except Exception:
            pass

        vehicle_id = self.scope.get("url_route", {}).get("kwargs", {}).get("vehicle_id")
        if vehicle_id:
            try:
                await self.channel_layer.group_discard(f"vehicle_{vehicle_id}", self.channel_name)
            except Exception:
                pass

    async def receive(self, text_data=None, bytes_data=None):
        return

    async def send_update(self, event):
        await self.send(text_data=json.dumps(event["data"]))
