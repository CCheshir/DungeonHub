"""Броски кубиков. Случайность генерирует сервер, а не нейросеть."""
import random
import re
from dataclasses import dataclass

# 1d20, d20, 2d6+3, 1d20 - 1, 4d6+2d4+1
_TERM = re.compile(r"([+-]?)\s*(\d*)\s*[dдк]\s*(\d+)|([+-]?)\s*(\d+)", re.IGNORECASE)
_FULL = re.compile(r"^\s*(?:[+-]?\s*(?:\d*\s*[dдк]\s*\d+|\d+)\s*)+$", re.IGNORECASE)

MAX_DICE = 100
MAX_SIDES = 1000


class DiceError(ValueError):
    pass


@dataclass
class RollResult:
    expression: str
    total: int
    detail: str  # человекочитаемая расшифровка: "(14) + 3"

    def describe(self) -> str:
        return f"{self.expression} → {self.detail} = {self.total}"


def roll(expression: str, rng: random.Random | None = None) -> RollResult:
    rng = rng or random.SystemRandom()
    expr = expression.strip()
    if not expr or not _FULL.match(expr):
        raise DiceError("Не понял бросок. Пример: /roll 1d20+3 или /roll 2d6")

    total = 0
    parts: list[str] = []
    dice_count = 0
    for m in _TERM.finditer(expr):
        if m.group(3):  # кубик
            sign = -1 if m.group(1) == "-" else 1
            n = int(m.group(2) or 1)
            sides = int(m.group(3))
            if n < 1 or sides < 2:
                raise DiceError("Кубиков должно быть не меньше 1, граней — не меньше 2")
            dice_count += n
            if dice_count > MAX_DICE or sides > MAX_SIDES:
                raise DiceError("Слишком большой бросок")
            values = [rng.randint(1, sides) for _ in range(n)]
            total += sign * sum(values)
            parts.append(("- " if sign < 0 else "+ ") + "(" + ", ".join(map(str, values)) + ")")
        elif m.group(5):  # модификатор
            sign = -1 if m.group(4) == "-" else 1
            val = int(m.group(5))
            total += sign * val
            parts.append(("- " if sign < 0 else "+ ") + str(val))
    if dice_count == 0:
        raise DiceError("В броске нет ни одного кубика. Пример: /roll 1d20+3")

    detail = " ".join(parts)
    if detail.startswith("+ "):
        detail = detail[2:]
    normalized = re.sub(r"\s+", "", expr).lower().replace("д", "d").replace("к", "d")
    return RollResult(expression=normalized, total=total, detail=detail)
