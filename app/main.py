"""Веб-сервер DungeonHub: HTTP API + WebSocket."""
import logging
import os
import re
import secrets
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

from .db import DEFAULT_SETTINGS, Database
from .llm import GeminiClient, StubClient
from .room import RoomSession

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("dungeonhub")

STATIC = Path(__file__).parent / "static"
DB_PATH = os.environ.get("DB_PATH", "dungeonhub.db")
CREATE_PASSWORD = os.environ.get("CREATE_ROOM_PASSWORD", "")
DEFAULT_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.8-flash")


def make_llm():
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        log.warning("GEMINI_API_KEY не задан — мастер работает в режиме заглушки")
        return StubClient()
    budget = os.environ.get("GEMINI_THINKING_BUDGET", "").strip()
    return GeminiClient(key, DEFAULT_MODEL, int(budget) if budget else None)


Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
db = Database(DB_PATH)
llm = make_llm()
sessions: dict[str, RoomSession] = {}


def get_session(room_id: str) -> RoomSession:
    if room_id not in sessions:
        sessions[room_id] = RoomSession(room_id, db, llm)
    return sessions[room_id]


# ---------- проверка входных данных ----------
TEXT_FIELDS = ("world", "situation", "plot", "style", "rules")
MODEL_RE = re.compile(r"^[\w.\-]{0,80}$")


def clean_settings(raw: dict, current: dict) -> dict:
    s = dict(DEFAULT_SETTINGS)
    s.update(current)
    for f in TEXT_FIELDS:
        if f in raw:
            s[f] = str(raw[f] or "")[:20000]
    if raw.get("length") in ("short", "medium", "long"):
        s["length"] = raw["length"]
    if raw.get("mode") in ("narrative", "mechanics"):
        s["mode"] = raw["mode"]

    def num(key, lo, hi, cast=float):
        if key in raw:
            try:
                s[key] = cast(min(hi, max(lo, float(raw[key]))))
            except (TypeError, ValueError):
                pass

    num("batch_seconds", 0, 60)
    num("open_timeout_minutes", 1, 120)
    num("temperature", 0, 2)
    if "model" in raw and MODEL_RE.match(str(raw["model"] or "")):
        s["model"] = str(raw["model"] or "")
    return s


def clean_name(value, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def check_admin(request: Request, room: dict) -> bool:
    token = request.headers.get("x-admin-token", "")
    return bool(token) and secrets.compare_digest(token, room["admin_token"])


# ---------- HTTP ----------
async def index(request: Request):
    return FileResponse(STATIC / "index.html")


async def room_page(request: Request):
    return FileResponse(STATIC / "room.html")


async def api_config(request: Request):
    return JSONResponse({"needs_password": bool(CREATE_PASSWORD), "llm": llm.name, "default_model": DEFAULT_MODEL})


async def api_create_room(request: Request):
    data = await request.json()
    if CREATE_PASSWORD and not secrets.compare_digest(str(data.get("password", "")), CREATE_PASSWORD):
        return JSONResponse({"error": "Неверный пароль для создания комнат"}, status_code=403)
    name = clean_name(data.get("name"), 80) or "Новая кампания"
    room_id = secrets.token_urlsafe(6).replace("-", "x").replace("_", "y")
    admin_token = secrets.token_urlsafe(24)
    db.create_room(room_id, name, admin_token)
    return JSONResponse({"id": room_id, "name": name, "admin_token": admin_token})


async def api_room(request: Request):
    room = db.get_room(request.path_params["room_id"])
    if not room:
        return JSONResponse({"error": "Комната не найдена"}, status_code=404)
    return JSONResponse({"id": room["id"], "name": room["name"]})


async def api_admin(request: Request):
    room_id = request.path_params["room_id"]
    room = db.get_room(room_id)
    if not room:
        return JSONResponse({"error": "Комната не найдена"}, status_code=404)
    if not check_admin(request, room):
        return JSONResponse({"error": "Нет прав мастера комнаты"}, status_code=403)

    if request.method == "PUT":
        data = await request.json()
        if "name" in data:
            name = clean_name(data["name"], 80)
            if name:
                db.update_room_name(room_id, name)
        if isinstance(data.get("settings"), dict):
            db.update_settings(room_id, clean_settings(data["settings"], room["settings"]))
        if "chronicle" in data:
            db.set_chronicle(room_id, str(data["chronicle"] or "")[:50000])
        room = db.get_room(room_id)
        if room_id in sessions:
            await sessions[room_id].broadcast({"type": "room", "room": {"id": room_id, "name": room["name"]}})

    return JSONResponse({
        "name": room["name"], "settings": room["settings"], "chronicle": room["chronicle"],
        "default_model": DEFAULT_MODEL,
    })


# ---------- WebSocket ----------
class WSClient:
    def __init__(self, ws: WebSocket):
        self.ws = ws

    async def send(self, payload: dict) -> None:
        await self.ws.send_json(payload)


async def ws_room(websocket: WebSocket):
    room_id = websocket.path_params["room_id"]
    await websocket.accept()
    if not db.get_room(room_id):
        await websocket.send_json({"type": "error", "text": "Комната не найдена"})
        await websocket.close(code=4404)
        return

    session = get_session(room_id)
    client = WSClient(websocket)
    player = None
    try:
        while True:
            data = await websocket.receive_json()
            kind = data.get("type")
            if kind == "join":
                name = clean_name(data.get("player"), 40)
                character = clean_name(data.get("character"), 60)
                if not name or not character:
                    await client.send({"type": "error", "text": "Укажите имя игрока и персонажа"})
                    continue
                player = name
                await session.join(client, player, character)
            elif player is None:
                await client.send({"type": "error", "text": "Сначала войдите в комнату"})
            elif kind == "say":
                await session.say(client, player, str(data.get("text", "")))
            elif kind == "turn":
                await session.force_turn(player)
            elif kind == "sheet":
                await session.set_sheet(player, str(data.get("sheet", "")))
            elif kind == "ping":
                await client.send({"type": "pong"})
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("Ошибка в WebSocket комнаты %s", room_id)
    finally:
        await session.leave(client)


routes = [
    Route("/", index),
    Route("/r/{room_id}", room_page),
    Route("/api/config", api_config),
    Route("/api/rooms", api_create_room, methods=["POST"]),
    Route("/api/rooms/{room_id}", api_room),
    Route("/api/rooms/{room_id}/admin", api_admin, methods=["GET", "PUT"]),
    WebSocketRoute("/ws/{room_id}", ws_room),
    Mount("/static", StaticFiles(directory=STATIC), name="static"),
]

app = Starlette(routes=routes)
