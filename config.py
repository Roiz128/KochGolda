from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _load_dotenv(path: str = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Config:
    api_id: int
    api_hash: str
    bot_token: str
    session: str
    db_path: str
    rps: float                 # запросов к Telegram в секунду
    refresh_minutes: int       # как часто обновлять список коллекций
    extra_collections: list[str]
    # владелец и подписки
    owner_username: str = "Roiz14"   # доступ без подписки; пусто и без ADMIN_IDS = подписки выключены
    admin_ids: frozenset[int] = frozenset()
    pay_username: str = "Roiz14"     # кому слать подарки в оплату
    rub_details: str = ""
    gram_details: str = ""
    gram_rub_rate: float = 97.44   # сколько рублей стоит 1 GRAM (все цены в GRAM считаются по нему)
    provider_token: str = ""      # токен платёжного провайдера из @BotFather: оплата рублями картой


def load_config() -> Config:
    _load_dotenv()

    def need(name: str) -> str:
        v = os.environ.get(name, "").strip()
        if not v:
            raise SystemExit(f"Не задана переменная {name} (см. .env.example)")
        return v

    rate = float(os.environ.get("GRAM_RUB_RATE", "97.44").replace(",", "."))
    if rate <= 0:
        raise SystemExit("GRAM_RUB_RATE должен быть больше нуля")
    owner = os.environ.get("OWNER_USERNAME", "Roiz14").strip().lstrip("@")
    extra = [x.strip() for x in os.environ.get("EXTRA_COLLECTIONS", "").split(",") if x.strip()]
    return Config(
        api_id=int(need("API_ID")),
        api_hash=need("API_HASH"),
        bot_token=need("BOT_TOKEN"),
        session=os.environ.get("SESSION_NAME", "watcher"),
        db_path=os.environ.get("DB_PATH", "gifts.db"),
        rps=float(os.environ.get("REQUESTS_PER_SECOND", "8")),
        refresh_minutes=int(os.environ.get("REFRESH_MINUTES", "60")),
        extra_collections=extra,
        owner_username=owner,
        admin_ids=frozenset(
            int(x) for x in os.environ.get("ADMIN_IDS", "").replace(" ", "").split(",") if x.lstrip("-").isdigit()
        ),
        pay_username=(os.environ.get("PAY_USERNAME", "").strip().lstrip("@") or owner),
        rub_details=os.environ.get("RUB_DETAILS", "").strip(),
        gram_details=os.environ.get("GRAM_DETAILS", "").strip(),
        gram_rub_rate=rate,
        provider_token=os.environ.get("PROVIDER_TOKEN", "").strip(),
    )
