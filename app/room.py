"""Логика комнаты: подключения, заявки, ходы мастера, летопись.

Модуль не знает про WebSocket: клиент — любой объект с методом `async send(dict)`.
Это позволяет потом подключить Discord или Telegram поверх того же движка.
"""
import asyncio
import logging
import re
import time
from typing import Protocol

from . import dice
from .db import Database
from .llm import LLMError, RateLimited
from .prompts import SUMMARY_SYSTEM, build_contents, build_summary_request, build_system_prompt

log = logging.getLogger("dungeonhub.room")

START_RE = re.compile(r"^\W*начало\s+заявки\W*", re.IGNORECASE)
END_AT_END_RE = re.compile(r"\W*конец\s+заявки\W*$", re.IGNORECASE)
END_AT_START_RE = re.compile(r"^\W*конец\s+заявки\W*", re.IGNORECASE)

ROLL_CMDS = ("/roll", "/r", "/бросок", "/к")
OOC_CMDS = ("/ooc", "/оос", "/вне")
TURN_CMDS = ("/ход", "/turn", "/master", "/мастер")
HELP_CMDS = ("/help", "/помощь", "/?")

HELP_TEXT = """\
**Как играть**
• Обычное сообщение — это заявка: что делает ваш персонаж. Мастер ответит через несколько секунд, собрав заявки всех игроков.
• Длинная заявка: напишите «начало заявки», затем сколько угодно сообщений, затем «конец заявки». Пока заявка открыта, мастер ждёт.
• /roll 1d20+3 — бросок кубиков (можно добавить пояснение: /roll 1d20+3 внимательность).
• /ooc текст или ((текст)) — разговор вне игры, мастер не считает это действием.
• /ход — передать ход мастеру прямо сейчас (закрывает открытые заявки).
• /help — эта подсказка."""


class Client(Protocol):
    async def send(self, payload: dict) -> None: ...


def _strip_cmd(text: str, cmds: tuple[str, ...]) -> str | None:
    """Если текст начинается с одной из команд, вернуть остаток, иначе None."""
    low = text.lower()
    for c in cmds:
        if low == c or low.startswith(c + " ") or low.startswith(c + "\n"):
            return text[len(c):].strip()
    return None


def split_roll(arg: str) -> tuple[str, str]:
    """'1d20+3 на внимательность' -> ('1d20+3', 'на внимательность')."""
    tokens = arg.split()
    for i in range(len(tokens), 0, -1):
        candidate = " ".join(tokens[:i])
        if dice._FULL.match(candidate):
            return candidate, " ".join(tokens[i:])
    return arg, ""


class RoomSession:
    def __init__(self, room_id: str, db: Database, llm, *, retry_delay: float = 30, max_retries: int = 3,
                 summarize_threshold: int = 60, keep_recent: int = 20, context_limit: int = 120):
        self.room_id = room_id
        self.db = db
        self.llm = llm
        self.retry_delay = retry_delay
        self.max_retries = max_retries
        self.summarize_threshold = summarize_threshold
        self.keep_recent = keep_recent
        self.context_limit = context_limit

        self.clients: dict[Client, str] = {}
        self.open_requests: dict[str, float] = {}
        self._timeout_tasks: dict[str, asyncio.Task] = {}
        self._debounce: asyncio.Task | None = None
        self._tasks: set[asyncio.Task] = set()
        self.busy = False
        self._rerun_forced = False
        self._retries = 0
        self._summarizing = False
        self.pending = self._has_unanswered()

    # ---------- служебное ----------
    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def _has_unanswered(self) -> bool:
        last_master = 0
        has_action_after = False
        for m in self.db.recent_messages(self.room_id, 200):
            if m["kind"] == "master":
                last_master = m["id"]
                has_action_after = False
            elif m["kind"] in ("action", "roll") and m["id"] > last_master:
                has_action_after = True
        return has_action_after

    def settings(self) -> dict:
        room = self.db.get_room(self.room_id)
        return room["settings"] if room else {}

    def character_of(self, player: str) -> str:
        for c in self.db.get_characters(self.room_id):
            if c["player"] == player:
                return c["character"]
        return player

    def state(self) -> dict:
        online = set(self.clients.values())
        players = [
            {
                "player": c["player"], "character": c["character"], "sheet": c["sheet"],
                "online": c["player"] in online, "open": c["player"] in self.open_requests,
            }
            for c in self.db.get_characters(self.room_id)
        ]
        return {"type": "state", "players": players, "busy": self.busy, "pending": self.pending}

    async def broadcast(self, payload: dict) -> None:
        for client in list(self.clients):
            try:
                await client.send(payload)
            except Exception:
                self.clients.pop(client, None)

    async def broadcast_state(self) -> None:
        await self.broadcast(self.state())

    async def _post(self, kind: str, content: str, author: str = "", character: str = "") -> dict:
        msg = self.db.add_message(self.room_id, kind, content, author, character)
        await self.broadcast({"type": "message", "message": msg})
        return msg

    async def system(self, text: str) -> None:
        await self._post("system", text)

    # ---------- подключения ----------
    async def join(self, client: Client, player: str, character: str) -> None:
        self.db.upsert_character(self.room_id, player, character)
        self.clients[client] = player
        room = self.db.get_room(self.room_id)
        await client.send({
            "type": "welcome",
            "room": {"id": self.room_id, "name": room["name"] if room else ""},
            "messages": self.db.recent_messages(self.room_id, 200),
        })
        await self.broadcast_state()

    async def leave(self, client: Client) -> None:
        if self.clients.pop(client, None) is not None:
            await self.broadcast_state()

    async def set_sheet(self, player: str, sheet: str) -> None:
        self.db.set_sheet(self.room_id, player, sheet[:4000])
        await self.broadcast_state()

    # ---------- сообщения игроков ----------
    async def say(self, client: Client | None, player: str, text: str) -> None:
        text = (text or "").strip()[:4000]
        if not text:
            return
        character = self.character_of(player)

        if _strip_cmd(text, HELP_CMDS) is not None:
            if client:
                await client.send({"type": "notice", "text": HELP_TEXT})
            return

        arg = _strip_cmd(text, ROLL_CMDS)
        if arg is not None:
            expr, comment = split_roll(arg or "1d20")
            try:
                result = dice.roll(expr)
            except dice.DiceError as e:
                if client:
                    await client.send({"type": "notice", "text": str(e)})
                return
            content = result.describe() + (f" — {comment}" if comment else "")
            await self._post("roll", content, player, character)
            self._mark_pending(player)
            return

        arg = _strip_cmd(text, OOC_CMDS)
        if arg is None and text.startswith("(("):
            arg = text.strip("() ")
        if arg is not None:
            if arg:
                await self._post("ooc", arg, player, character)
            return

        if _strip_cmd(text, TURN_CMDS) is not None:
            await self.force_turn(player)
            return

        # --- заявки ---
        opened_now = False
        m = START_RE.match(text)
        if m:
            text = text[m.end():]
            await self._open_request(player)
            opened_now = True

        closing = False
        m = END_AT_END_RE.search(text)
        if m:
            text, closing = text[:m.start()], True
        else:
            m = END_AT_START_RE.match(text)
            if m:
                text, closing = text[m.end():], True

        if text.strip():
            await self._post("action", text.strip(), player, character)
            self.pending = True

        if closing:
            await self._close_request(player)
            return
        if opened_now or player in self.open_requests:
            return
        self.schedule()

    def _mark_pending(self, player: str) -> None:
        self.pending = True
        if player not in self.open_requests:
            self.schedule()

    async def _open_request(self, player: str) -> None:
        opened_at = time.monotonic()
        self.open_requests[player] = opened_at
        old = self._timeout_tasks.pop(player, None)
        if old:
            old.cancel()
        if self._debounce:
            self._debounce.cancel()
            self._debounce = None
        minutes = float(self.settings().get("open_timeout_minutes") or 5)
        self._timeout_tasks[player] = self._spawn(self._request_timeout(player, opened_at, minutes * 60))
        await self.broadcast_state()

    async def _close_request(self, player: str, auto: bool = False) -> None:
        if self.open_requests.pop(player, None) is None:
            return
        task = self._timeout_tasks.pop(player, None)
        if task and task is not asyncio.current_task():
            task.cancel()
        if auto:
            await self.system(f"Заявка {self.character_of(player)} закрыта автоматически по таймауту.")
        await self.broadcast_state()
        self.schedule()

    async def _request_timeout(self, player: str, opened_at: float, seconds: float) -> None:
        await asyncio.sleep(seconds)
        if self.open_requests.get(player) == opened_at:
            await self._close_request(player, auto=True)

    # ---------- ходы мастера ----------
    def schedule(self) -> None:
        """Запланировать ход мастера после короткой паузы, чтобы собрать почти одновременные заявки."""
        if not self.pending or self.open_requests:
            return
        if self.busy:
            return  # после текущего хода проверим pending ещё раз
        if self._debounce:
            self._debounce.cancel()
        delay = float(self.settings().get("batch_seconds") or 0)
        self._debounce = self._spawn(self._debounced(delay))

    async def _debounced(self, delay: float) -> None:
        await asyncio.sleep(max(0.0, delay))
        self._debounce = None
        await self.run_turn()

    async def force_turn(self, player: str | None = None) -> None:
        if self.open_requests:
            for p in list(self.open_requests):
                self.open_requests.pop(p, None)
                t = self._timeout_tasks.pop(p, None)
                if t:
                    t.cancel()
        if self._debounce:
            self._debounce.cancel()
            self._debounce = None
        who = self.character_of(player) if player else "Кто-то"
        await self.system(f"{who} передаёт ход мастеру.")
        if self.busy:
            self._rerun_forced = True
            await self.broadcast_state()
            return
        self._spawn(self.run_turn())

    async def run_turn(self) -> None:
        if self.busy:
            return
        room = self.db.get_room(self.room_id)
        if not room:
            return
        self.busy = True
        await self.broadcast_state()
        cutoff = self.db.last_message_id(self.room_id)
        succeeded = False
        try:
            settings = room["settings"]
            messages = self.db.messages_after(self.room_id, room["summarized_upto"])[-self.context_limit:]
            system = build_system_prompt(settings, self.db.get_characters(self.room_id), room["chronicle"])
            contents = build_contents(messages)
            try:
                text = await self.llm.generate(
                    system, contents,
                    temperature=float(settings.get("temperature") or 1.0),
                    model=(settings.get("model") or "").strip() or None,
                )
            except RateLimited:
                await self._handle_rate_limit()
                return
            except LLMError as e:
                await self.system(f"Мастер запнулся: {e}\nНажмите «Ход мастера», чтобы попробовать снова.")
                return
            except Exception:
                log.exception("Неожиданная ошибка при ходе мастера")
                await self.system("Мастер запнулся из-за внутренней ошибки. Нажмите «Ход мастера», чтобы попробовать снова.")
                return

            self._retries = 0
            await self._post("master", text, "Мастер")
            succeeded = True
            newer = self.db.messages_after(self.room_id, cutoff)
            self.pending = any(m["kind"] in ("action", "roll") for m in newer)
        finally:
            self.busy = False
            await self.broadcast_state()

        if self._rerun_forced:
            self._rerun_forced = False
            self._spawn(self.run_turn())
        elif succeeded:
            # После ошибки не перезапускаемся сами, чтобы не зациклиться: игроки жмут «Ход мастера».
            self.schedule()
        if succeeded:
            self._spawn(self.maybe_summarize())

    async def _handle_rate_limit(self) -> None:
        if self._retries < self.max_retries:
            self._retries += 1
            await self.system(
                f"Мастер переводит дух: упёрлись в лимит бесплатных запросов. "
                f"Попробую снова через {int(self.retry_delay)} сек."
            )
            self._spawn(self._retry_later())
        else:
            self._retries = 0
            await self.system(
                "Лимит запросов к нейросети пока исчерпан. Подождите немного и нажмите «Ход мастера»."
            )

    async def _retry_later(self) -> None:
        await asyncio.sleep(self.retry_delay)
        await self.run_turn()

    # ---------- летопись ----------
    async def maybe_summarize(self) -> None:
        if self._summarizing:
            return
        room = self.db.get_room(self.room_id)
        if not room:
            return
        msgs = [m for m in self.db.messages_after(self.room_id, room["summarized_upto"]) if m["kind"] != "system"]
        if len(msgs) <= self.summarize_threshold:
            return
        chunk = msgs[:-self.keep_recent]
        self._summarizing = True
        try:
            text = await self.llm.generate(
                SUMMARY_SYSTEM, build_summary_request(room["chronicle"], chunk), temperature=0.3,
                model=(room["settings"].get("model") or "").strip() or None,
            )
            self.db.set_chronicle(self.room_id, text, chunk[-1]["id"])
            log.info("Летопись комнаты %s обновлена до сообщения %s", self.room_id, chunk[-1]["id"])
        except LLMError as e:
            log.warning("Не удалось обновить летопись: %s", e)
        finally:
            self._summarizing = False

    async def close(self) -> None:
        for t in list(self._tasks):
            t.cancel()
