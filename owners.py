"""Владельцы бота: у них доступ без подписки и право подтверждать оплату.

Владелец задаётся юзернеймом (OWNER_USERNAME, по умолчанию Roiz14). Его числовой id бот
определяет сам (через аккаунт-наблюдатель при запуске или по первому сообщению от этого
юзернейма) и запоминает в базе. Дополнительно можно перечислить id в ADMIN_IDS.
"""
from __future__ import annotations


class Owners:
    def __init__(self, db, username: str = "", static_ids=()):
        self.db = db
        self.username = (username or "").lower().lstrip("@")
        self.static = frozenset(static_ids)
        saved = db.get_meta("owner_ids") or ""
        self._saved = {int(x) for x in saved.split(",") if x.strip().lstrip("-").isdigit()}

    @property
    def enabled(self) -> bool:
        """Подписки включены, если известен хоть какой-то владелец. Иначе уведомления у всех бесплатно."""
        return bool(self.username or self.static)

    @property
    def ids(self) -> frozenset:
        return self.static | frozenset(self._saved)

    def __contains__(self, uid) -> bool:
        return uid in self.ids

    def add(self, uid: int) -> None:
        if uid not in self._saved:
            self._saved.add(int(uid))
            self.db.set_meta("owner_ids", ",".join(str(i) for i in sorted(self._saved)))

    def bind(self, user) -> bool:
        """Если пишет человек с юзернеймом владельца, запомнить его id."""
        uname = getattr(user, "username", None)
        if self.username and uname and uname.lower() == self.username:
            self.add(user.id)
            return True
        return False
