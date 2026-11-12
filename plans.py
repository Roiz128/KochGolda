"""Тарифы подписки. Цены меняй здесь: rub — рубли, stars — Telegram Stars.
Цена в GRAM считается из рублей по курсу GRAM_RUB_RATE из .env."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class Plan:
    key: str       # латиницей, без пробелов: используется в кнопках и /grant
    title: str     # как показывать пользователю
    seconds: int   # на сколько выдаётся доступ
    rub: int
    stars: int


PLANS = [
    Plan("hour", "Час", 3600, 30, 15),
    Plan("day", "День", 86400, 100, 50),
    Plan("week", "Неделя", 7 * 86400, 300, 150),
    Plan("month", "Месяц", 30 * 86400, 1000, 500),
]
PLANS_BY_KEY = {p.key: p for p in PLANS}


def gram_price(rub: float, rate: float) -> float:
    """Цена в GRAM по курсу «rate рублей за 1 GRAM», с округлением вверх до 0.01 (чтобы не недобрать)."""
    return math.ceil(rub / rate * 100 - 1e-9) / 100


def fmt_gram(x: float) -> str:
    return f"{x:g}"


def fmt_left(seconds: int) -> str:
    seconds = max(int(seconds), 0)
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m = rem // 60
    parts = []
    if d:
        parts.append(f"{d} д")
    if h:
        parts.append(f"{h} ч")
    if m or not parts:
        parts.append(f"{m} мин")
    return " ".join(parts)


def fmt_until(ts: int) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%d.%m.%Y %H:%M UTC")


_UNITS = {"m": 60, "min": 60, "мин": 60, "м": 60, "h": 3600, "ч": 3600, "d": 86400, "д": 86400, "w": 604800, "н": 604800}


def parse_duration(text: str) -> int | None:
    """'5m' / '2h' / '3d' / '1w' (и русские м/ч/д/н) -> секунды. None, если не распознано."""
    m = re.fullmatch(r"(\d{1,5})\s*(min|мин|[mhdwмчдн])", text.strip().lower())
    if not m:
        return None
    secs = int(m.group(1)) * _UNITS[m.group(2)]
    return secs if 0 < secs <= 366 * 86400 else None
