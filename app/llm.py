"""Клиенты нейросетей. Сейчас: Gemini (REST API) и заглушка для тестов без ключа."""
import asyncio
import json
import logging
import urllib.error
import urllib.request

log = logging.getLogger("dungeonhub.llm")

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


class LLMError(Exception):
    """Ошибка, которую можно показать игрокам."""


class RateLimited(LLMError):
    """Превышен лимит запросов (бесплатный уровень API)."""


class GeminiClient:
    name = "gemini"

    def __init__(self, api_key: str, model: str, thinking_budget: int | None = None, timeout: float = 120):
        self.api_key = api_key
        self.default_model = model
        self.thinking_budget = thinking_budget
        self.timeout = timeout

    async def generate(self, system: str, contents: list[dict], temperature: float = 1.0,
                       model: str | None = None) -> str:
        return await asyncio.to_thread(self._generate_sync, system, contents, temperature, model or self.default_model)

    def _generate_sync(self, system: str, contents: list[dict], temperature: float, model: str) -> str:
        gen_config: dict = {"temperature": temperature, "maxOutputTokens": 8192}
        if self.thinking_budget is not None:
            gen_config["thinkingConfig"] = {"thinkingBudget": self.thinking_budget}
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": contents,
            "generationConfig": gen_config,
        }
        req = urllib.request.Request(
            GEMINI_URL.format(model=model),
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "x-goog-api-key": self.api_key},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = _error_message(e)
            log.warning("Gemini HTTP %s: %s", e.code, detail)
            if e.code == 429:
                raise RateLimited(detail) from e
            raise LLMError(f"Gemini ответил ошибкой {e.code}: {detail}") from e
        except (urllib.error.URLError, TimeoutError) as e:
            raise LLMError(f"Не удалось связаться с Gemini: {e}") from e

        candidates = data.get("candidates") or []
        if not candidates:
            reason = (data.get("promptFeedback") or {}).get("blockReason")
            raise LLMError(f"Gemini не дал ответа (причина: {reason or 'неизвестна'})")
        cand = candidates[0]
        parts = (cand.get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        if not text.strip():
            raise LLMError(f"Пустой ответ от Gemini (finishReason: {cand.get('finishReason')})")
        return text.strip()


def _error_message(e: urllib.error.HTTPError) -> str:
    try:
        payload = json.loads(e.read().decode("utf-8"))
        return (payload.get("error") or {}).get("message") or str(payload)[:300]
    except Exception:
        return e.reason or "без описания"


class StubClient:
    """Заглушка мастера: работает без ключа, чтобы проверить сайт."""

    name = "stub"

    async def generate(self, system: str, contents: list[dict], temperature: float = 1.0,
                       model: str | None = None) -> str:
        await asyncio.sleep(0.3)
        last = ""
        if contents:
            last = "".join(p.get("text", "") for p in contents[-1].get("parts", []))
        if "[Летопись]" in system and "Составь летопись" in last:
            return "Летопись (заглушка): герои прошли через несколько сцен."
        return (
            "*Мастер-заглушка* (ключ Gemini не задан).\n\n"
            f"Я получил ход:\n\n> {last[-500:]}\n\nМир отвечает тишиной. Что вы делаете?"
        )
