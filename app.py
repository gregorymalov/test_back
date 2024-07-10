import asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import redis

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

r = redis.Redis(host='localhost', port=6379, db=0)

class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[str, list[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, user_id: str):
        await websocket.accept()
        if user_id not in self.active_connections:
            self.active_connections[user_id] = []
        self.active_connections[user_id].append(websocket)

    def disconnect(self, websocket: WebSocket, user_id: str):
        self.active_connections[user_id].remove(websocket)
        if not self.active_connections[user_id]:
            del self.active_connections[user_id]

    async def send_personal_message(self, message: dict, websocket: WebSocket):
        await websocket.send_json(message)

    async def broadcast(self, user_id: str, message: dict):
        for connection in self.active_connections.get(user_id, []):
            await connection.send_json(message)

manager = ConnectionManager()

async def update_user_counters(user_id: str):
    try:
        # Обновление энергии
        energy_key = f"{user_id}_energy"
        energy = r.get(energy_key)
        if energy is None:
            energy = 100
        else:
            energy = int(energy)
        energy = min(100, energy + 1)  # Энергия не может превышать 100
        r.set(energy_key, energy)
        
        # Обновление счетчика с текущим значением приращения
        counter_key = f"{user_id}_counter"
        increment_key = f"{user_id}_increment"
        counter = r.get(counter_key)
        increment = r.get(increment_key)
        if counter is None:
            counter = 0
        else:
            counter = int(counter)
        if increment is None:
            increment = 1
        else:
            increment = int(increment)
        counter += increment
        r.set(counter_key, counter)

        # Отправка обновленных данных всем подключенным вебсокетам
        await manager.broadcast(user_id, {"counter": counter, "energy": energy, "increment_value": increment})
    except Exception as e:
        print(f"Error updating user {user_id}: {e}")

async def update_counters_periodically():
    while True:
        await asyncio.sleep(1)
        tasks = [update_user_counters(user_id) for user_id in manager.active_connections]
        await asyncio.gather(*tasks)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(update_counters_periodically())

@app.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: str):
    await manager.connect(websocket, user_id)

    try:
        counter_key = f"{user_id}_counter"
        counter = r.get(counter_key)
        if counter is None:
            counter = 0
        else:
            counter = int(counter)
        
        energy_key = f"{user_id}_energy"
        energy = r.get(energy_key)
        if energy is None:
            energy = 100
        else:
            energy = int(energy)
        
        increment_key = f"{user_id}_increment"
        increment_value = r.get(increment_key)
        if increment_value is None:
            increment_value = 1
        else:
            increment_value = int(increment_value)

        await manager.send_personal_message({"counter": counter, "energy": energy, "increment_value": increment_value, "user_id": user_id}, websocket)

        while True:
            data = await websocket.receive_json()
            if data.get("action") == 'increment':
                click_value = data.get("value", 1)
                counter = r.get(counter_key)
                if counter is None:
                    counter = 0
                else:
                    counter = int(counter)
                counter += click_value
                r.set(counter_key, counter)
                
                energy = r.get(energy_key)
                if energy is None:
                    energy = 100
                else:
                    energy = int(energy)
                
                if energy >= click_value:
                    energy -= click_value
                    r.set(energy_key, energy)
                await manager.send_personal_message({"counter": counter, "energy": energy}, websocket)
            elif data.get("action") == 'set_increment':
                new_increment_value = int(data.get("new_increment_value", 1))
                r.set(increment_key, new_increment_value)
                increment_value = new_increment_value
                await manager.send_personal_message({"increment_value": increment_value}, websocket)
    except WebSocketDisconnect:
        manager.disconnect(websocket, user_id)
    except Exception as e:
        print(f"Error: {e}")
    finally:
        if websocket in manager.active_connections.get(user_id, []):
            manager.disconnect(websocket, user_id)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
