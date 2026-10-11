"""Тесты движка. Запуск: python -m unittest discover tests"""
import asyncio
import os
import random
import tempfile
import unittest

from app import dice
from app.db import Database
from app.llm import LLMError, RateLimited
from app.prompts import build_contents, build_system_prompt
from app.room import RoomSession, split_roll


class DiceTest(unittest.TestCase):
    def test_basic(self):
        r = dice.roll("1d20+3", random.Random(1))
        self.assertTrue(4 <= r.total <= 23)
        self.assertEqual(r.expression, "1d20+3")

    def test_russian_and_multi(self):
        r = dice.roll("2к6 - 1", random.Random(2))
        self.assertTrue(1 <= r.total <= 11)
        self.assertIn("(", r.detail)

    def test_bad(self):
        for bad in ["", "abc", "5", "1d1", "1000d6"]:
            with self.assertRaises(dice.DiceError, msg=bad):
                dice.roll(bad)

    def test_split_roll(self):
        self.assertEqual(split_roll("1d20+3 для ловкости"), ("1d20+3", "для ловкости"))
        self.assertEqual(split_roll("d20"), ("d20", ""))


class PromptTest(unittest.TestCase):
    def test_contents_alternate(self):
        msgs = [
            {"kind": "master", "author": "Мастер", "character": "", "content": "Вы в замке."},
            {"kind": "action", "author": "Петя", "character": "Гарри", "content": "Иду вперёд"},
            {"kind": "ooc", "author": "Маша", "character": "", "content": "я отойду"},
            {"kind": "system", "author": "", "character": "", "content": "служебное"},
            {"kind": "roll", "author": "Маша", "character": "Гермиона", "content": "1d20 → (12) = 12"},
        ]
        c = build_contents(msgs)
        self.assertEqual([x["role"] for x in c], ["user", "model", "user"])
        self.assertIn("[Гарри (игрок Петя)]", c[2]["parts"][0]["text"])
        self.assertNotIn("служебное", str(c))

    def test_empty_history_starts_game(self):
        c = build_contents([])
        self.assertEqual(c[0]["role"], "user")

    def test_system_prompt_mode(self):
        p = build_system_prompt({"mode": "mechanics", "rules": "d20", "world": "Хогвартс"},
                                [{"player": "Петя", "character": "Гарри", "sheet": "смелый"}], "")
        self.assertIn("Хогвартс", p)
        self.assertIn("/roll", p)
        self.assertIn("смелый", p)


class FakeLLM:
    name = "fake"

    def __init__(self):
        self.calls = []
        self.fail_with = None

    async def generate(self, system, contents, temperature=1.0, model=None):
        self.calls.append(contents)
        if self.fail_with:
            raise self.fail_with
        await asyncio.sleep(0.01)
        return f"ответ {len(self.calls)}"


class FakeClient:
    def __init__(self):
        self.received = []

    async def send(self, payload):
        self.received.append(payload)


class RoomTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(os.path.join(self.tmp.name, "t.db"))
        self.db.create_room("r1", "Тест", "tok")
        room = self.db.get_room("r1")
        s = room["settings"]
        s["batch_seconds"] = 0.05
        self.db.update_settings("r1", s)
        self.llm = FakeLLM()
        self.room = RoomSession("r1", self.db, self.llm, retry_delay=0.05, max_retries=1)
        self.c1, self.c2 = FakeClient(), FakeClient()
        await self.room.join(self.c1, "Петя", "Гарри")
        await self.room.join(self.c2, "Маша", "Гермиона")

    async def asyncTearDown(self):
        await self.room.close()
        self.db.close()
        self.tmp.cleanup()

    def masters(self):
        return [m for m in self.db.recent_messages("r1") if m["kind"] == "master"]

    async def settle(self, t=0.3):
        await asyncio.sleep(t)

    async def test_simultaneous_actions_batched(self):
        await self.room.say(self.c1, "Петя", "Открываю дверь")
        await self.room.say(self.c2, "Маша", "Достаю палочку")
        await self.settle()
        self.assertEqual(len(self.llm.calls), 1)
        self.assertEqual(len(self.masters()), 1)
        text = self.llm.calls[0][-1]["parts"][0]["text"]
        self.assertIn("Открываю дверь", text)
        self.assertIn("Достаю палочку", text)

    async def test_multi_message_request(self):
        await self.room.say(self.c1, "Петя", "Начало заявки")
        await self.room.say(self.c1, "Петя", "Сначала осматриваюсь")
        await self.settle(0.2)
        await self.room.say(self.c2, "Маша", "Жду Гарри")  # Маша пишет, но заявка Пети ещё открыта
        await self.settle(0.2)
        self.assertEqual(len(self.llm.calls), 0)
        self.assertTrue(self.room.state()["players"][0]["open"])
        await self.room.say(self.c1, "Петя", "потом иду к окну. Конец заявки")
        await self.settle()
        self.assertEqual(len(self.llm.calls), 1)
        actions = [m["content"] for m in self.db.recent_messages("r1") if m["kind"] == "action"]
        self.assertIn("потом иду к окну", actions)
        self.assertNotIn("", actions)

    async def test_start_and_end_in_one_message(self):
        await self.room.say(self.c1, "Петя", "начало заявки иду конец заявки")
        await self.settle()
        self.assertEqual(len(self.llm.calls), 1)

    async def test_ooc_does_not_trigger(self):
        await self.room.say(self.c1, "Петя", "/ooc я за чаем")
        await self.room.say(self.c1, "Петя", "((вернусь через минуту))")
        await self.settle()
        self.assertEqual(len(self.llm.calls), 0)
        oocs = [m for m in self.db.recent_messages("r1") if m["kind"] == "ooc"]
        self.assertEqual(len(oocs), 2)

    async def test_roll_triggers_turn(self):
        await self.room.say(self.c1, "Петя", "/roll 1d20+2 внимательность")
        await self.settle()
        rolls = [m for m in self.db.recent_messages("r1") if m["kind"] == "roll"]
        self.assertEqual(len(rolls), 1)
        self.assertIn("внимательность", rolls[0]["content"])
        self.assertEqual(len(self.llm.calls), 1)

    async def test_bad_roll_is_private(self):
        await self.room.say(self.c1, "Петя", "/roll абв")
        notices = [p for p in self.c1.received if p["type"] == "notice"]
        self.assertEqual(len(notices), 1)
        self.assertFalse(any(p["type"] == "notice" for p in self.c2.received))

    async def test_force_turn_closes_requests(self):
        await self.room.say(self.c1, "Петя", "начало заявки")
        await self.room.say(self.c2, "Маша", "/ход")
        await self.settle()
        self.assertEqual(len(self.llm.calls), 1)
        self.assertEqual(self.room.open_requests, {})

    async def test_force_turn_with_empty_history_opens_scene(self):
        await self.room.force_turn("Петя")
        await self.settle()
        self.assertEqual(len(self.masters()), 1)

    async def test_actions_during_generation_get_next_turn(self):
        orig = self.llm.generate

        async def slow(*a, **k):
            await asyncio.sleep(0.2)
            return await orig(*a, **k)

        self.llm.generate = slow
        await self.room.say(self.c1, "Петя", "Действие 1")
        await asyncio.sleep(0.12)  # ход уже идёт
        await self.room.say(self.c2, "Маша", "Действие 2")
        await self.settle(0.9)
        self.assertEqual(len(self.masters()), 2)

    async def test_llm_error_reports_and_stops(self):
        self.llm.fail_with = LLMError("сломалось")
        await self.room.say(self.c1, "Петя", "Иду")
        await self.settle()
        self.assertEqual(len(self.llm.calls), 1)
        systems = [m["content"] for m in self.db.recent_messages("r1") if m["kind"] == "system"]
        self.assertTrue(any("сломалось" in s for s in systems))

    async def test_rate_limit_retries(self):
        self.llm.fail_with = RateLimited("429")
        await self.room.say(self.c1, "Петя", "Иду")
        await self.settle(0.5)
        self.assertEqual(len(self.llm.calls), 2)  # первый вызов + один повтор
        self.assertEqual(len(self.masters()), 0)

    async def test_summarize(self):
        self.room.summarize_threshold = 6
        self.room.keep_recent = 2
        for i in range(8):
            self.db.add_message("r1", "action", f"шаг {i}", "Петя", "Гарри")
        await self.room.maybe_summarize()
        room = self.db.get_room("r1")
        self.assertTrue(room["chronicle"])
        self.assertGreater(room["summarized_upto"], 0)

    async def test_request_timeout(self):
        s = self.db.get_room("r1")["settings"]
        s["open_timeout_minutes"] = 0.002  # ~0.12 сек
        self.db.update_settings("r1", s)
        await self.room.say(self.c1, "Петя", "начало заявки")
        await self.room.say(self.c1, "Петя", "делаю что-то")
        await self.settle(0.5)
        self.assertEqual(self.room.open_requests, {})
        self.assertEqual(len(self.llm.calls), 1)


if __name__ == "__main__":
    unittest.main()
