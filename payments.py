"""Подписки: Telegram Stars и рубли картой (автоматически), NFT-подарок / рубли / GRAM вручную,
промокоды. Ручные оплаты подтверждает владелец."""
from __future__ import annotations

import html
import logging
import re
import time

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)

from config import Config
from db import Database
from owners import Owners
from plans import PLANS, PLANS_BY_KEY, Plan, fmt_gram, fmt_left, fmt_until, gram_price, parse_duration

log = logging.getLogger("payments")
MAX_PENDING = 3  # столько неподтверждённых заявок на оплату можно держать одновременно
PROMO_RE = re.compile(r"[A-Z0-9_-]{3,32}")


def _kb(rows: list[list[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=t, callback_data=d) for t, d in row] for row in rows]
    )


def plans_kb() -> InlineKeyboardMarkup:
    return _kb([[(f"{p.title} — {p.rub} ₽ / {p.stars} ⭐", f"plan:{p.key}")] for p in PLANS])


def expected_payment(payload: str):
    """По payload счёта: (тариф, валюта, сумма в минимальных единицах, способ, подпись суммы) или None."""
    kind, _, key = (payload or "").partition(":")
    plan = PLANS_BY_KEY.get(key)
    if plan is None:
        return None
    if kind == "sub":
        return plan, "XTR", plan.stars, "stars", f"{plan.stars} ⭐"
    if kind == "rub":
        return plan, "RUB", plan.rub * 100, "rub-card", f"{plan.rub} ₽"
    return None


async def _safe_send(bot, chat_id: int, text: str, **kw) -> bool:
    try:
        await bot.send_message(chat_id, text, **kw)
        return True
    except Exception as e:  # заблокировал бота / не начинал диалог
        log.warning("Не удалось отправить сообщение %s: %s", chat_id, e)
        return False


def build_payments_router(db: Database, cfg: Config, owners: Owners) -> Router:
    r = Router()
    contact = f"@{cfg.pay_username}"

    def gram(plan: Plan) -> str:
        """Цена тарифа в GRAM по курсу из настроек."""
        return fmt_gram(gram_price(plan.rub, cfg.gram_rub_rate))

    def methods_kb(plan: Plan) -> InlineKeyboardMarkup:
        return _kb([
            [("⭐ Звёзды (автоматически)", f"pay:stars:{plan.key}")],
            [("🎁 NFT-подарком", f"pay:gift:{plan.key}")],
            [("💳 Рубли", f"pay:rub:{plan.key}")],
            [("💎 GRAM", f"pay:gram:{plan.key}")],
            [("← Назад", "buy")],
        ])

    def manual_instruction(method: str, plan: Plan) -> str:
        if method == "gift":
            return (
                f"Отправь NFT-подарок стоимостью от <b>{gram(plan)} GRAM</b> пользователю "
                f"<b>{html.escape(contact)}</b>. Затем нажми «Я оплатил»."
            )
        if method == "rub":
            where = html.escape(cfg.rub_details) if cfg.rub_details else f"реквизиты выдаёт {html.escape(contact)}, напиши ему"
            return f"Переведи <b>{plan.rub} ₽</b>: {where}\n\nЗатем нажми «Я оплатил»."
        where = html.escape(cfg.gram_details) if cfg.gram_details else f"адрес выдаёт {html.escape(contact)}, напиши ему"
        return f"Переведи <b>{gram(plan)} GRAM</b>: {where}\n\nЗатем нажми «Я оплатил»."

    def amount_str(method: str, plan: Plan) -> str:
        if method == "gift":
            return f"NFT-подарок от {gram(plan)} GRAM"
        if method == "rub":
            return f"{plan.rub} ₽"
        return f"{gram(plan)} GRAM"

    # ------------------------------------------------------------------ покупка
    @r.message(Command("buy", "subscribe"))
    async def buy(m: Message):
        owners.bind(m.from_user)
        if not owners.enabled:
            return await m.answer("Подписки сейчас отключены, уведомления приходят бесплатно.")
        await m.answer("Выбери срок подписки.\nЕсть промокод? Отправь /promo КОД", reply_markup=plans_kb())

    @r.callback_query(F.data == "buy")
    async def show_plans(cb: CallbackQuery):
        await cb.message.edit_text("Выбери срок подписки.\nЕсть промокод? Отправь /promo КОД", reply_markup=plans_kb())
        await cb.answer()

    @r.callback_query(F.data.startswith("plan:"))
    async def choose_plan(cb: CallbackQuery):
        plan = PLANS_BY_KEY.get(cb.data.split(":", 1)[1])
        if plan is None:
            return await cb.answer("Такого тарифа нет", show_alert=True)
        await cb.message.edit_text(
            f"Подписка «{plan.title}»: {plan.rub} ₽ / {plan.stars} ⭐ / {gram(plan)} GRAM\nКак будешь платить?",
            reply_markup=methods_kb(plan),
        )
        await cb.answer()

    @r.callback_query(F.data.startswith("pay:"))
    async def pay(cb: CallbackQuery):
        _, method, key = cb.data.split(":", 2)
        plan = PLANS_BY_KEY.get(key)
        if plan is None or method not in {"stars", "gift", "rub", "gram"}:
            return await cb.answer("Ошибка тарифа", show_alert=True)
        title = f"Подписка: {plan.title}"
        desc = f"Доступ к уведомлениям о новых улучшенных подарках: {plan.title.lower()}."
        if method == "stars":
            await cb.message.answer_invoice(
                title=title, description=desc, payload=f"sub:{plan.key}", currency="XTR",
                prices=[LabeledPrice(label=title, amount=plan.stars)],
            )
            return await cb.answer()
        if method == "rub" and cfg.provider_token:  # автоматическая оплата картой через платёжного провайдера
            await cb.message.answer_invoice(
                title=title, description=desc, payload=f"rub:{plan.key}", currency="RUB",
                provider_token=cfg.provider_token, prices=[LabeledPrice(label=title, amount=plan.rub * 100)],
            )
            return await cb.answer()
        await cb.message.edit_text(
            manual_instruction(method, plan),
            reply_markup=_kb([[("✅ Я оплатил", f"paid:{method}:{plan.key}")], [("← Назад", f"plan:{plan.key}")]]),
        )
        await cb.answer()

    @r.callback_query(F.data.startswith("paid:"))
    async def paid_manual(cb: CallbackQuery):
        _, method, key = cb.data.split(":", 2)
        plan = PLANS_BY_KEY.get(key)
        if plan is None or method not in {"gift", "rub", "gram"}:
            return await cb.answer("Ошибка тарифа", show_alert=True)
        uid = cb.from_user.id
        if db.pending_count(uid) >= MAX_PENDING:
            return await cb.answer("У тебя уже есть неподтверждённые заявки, подожди ответа.", show_alert=True)
        pid = db.add_payment(uid, method, plan.key, amount_str(method, plan), "pending")
        who = html.escape(cb.from_user.full_name or str(uid))
        uname = f" (@{html.escape(cb.from_user.username)})" if cb.from_user.username else ""
        text = (
            f"💰 Заявка на оплату #{pid}\nОт: {who}{uname}, id <code>{uid}</code>\n"
            f"Тариф: {plan.title}\nСпособ: {method}, сумма: {html.escape(amount_str(method, plan))}"
        )
        sent = 0
        for owner in owners.ids:
            ok = await _safe_send(
                cb.bot, owner, text,
                reply_markup=_kb([[("✅ Подтвердить", f"adm:ok:{pid}"), ("❌ Отклонить", f"adm:no:{pid}")]]),
            )
            sent += ok
        note = "" if sent else f"\n\nНе удалось уведомить владельца, напиши {contact}."
        await cb.message.edit_text(
            f"Заявка #{pid} отправлена. Как только оплату подтвердят, подписка включится.{note}"
        )
        await cb.answer()

    @r.callback_query(F.data.startswith("adm:"))
    async def admin_decide(cb: CallbackQuery):
        if cb.from_user.id not in owners:
            return await cb.answer("Нет доступа", show_alert=True)
        _, action, pid_s = cb.data.split(":", 2)
        pid = int(pid_s)
        pay_row = db.get_payment(pid)
        if pay_row is None:
            return await cb.answer("Заявка не найдена", show_alert=True)
        plan = PLANS_BY_KEY.get(pay_row["plan"])
        approve = action == "ok"
        if approve and plan is None:
            return await cb.answer("Тариф из заявки больше не существует", show_alert=True)
        if not db.resolve_payment(pid, "ok" if approve else "rejected"):
            return await cb.answer("Заявка уже обработана", show_alert=True)
        if approve:
            until = db.extend_sub(pay_row["chat_id"], plan.seconds)
            await _safe_send(
                cb.bot, pay_row["chat_id"],
                f"✅ Оплата подтверждена. Подписка «{plan.title}» активна до {fmt_until(until)}.",
            )
        else:
            await _safe_send(
                cb.bot, pay_row["chat_id"],
                f"❌ Оплата по заявке #{pid} не подтверждена. Если это ошибка, напиши {contact}.",
            )
        mark = "✅ Подтверждено" if approve else "❌ Отклонено"
        await cb.message.edit_text(f"{cb.message.html_text}\n\n{mark}")
        await cb.answer()

    # ------------------------------------------------------------------ Stars и карта: автоматически
    @r.pre_checkout_query()
    async def pre_checkout(q: PreCheckoutQuery):
        exp = expected_payment(q.invoice_payload)
        if exp is None or q.currency != exp[1] or q.total_amount != exp[2]:
            return await q.answer(ok=False, error_message="Тариф изменился, открой /buy заново.")
        await q.answer(ok=True)

    @r.message(F.successful_payment)
    async def got_payment(m: Message):
        sp = m.successful_payment
        exp = expected_payment(sp.invoice_payload)
        if exp is None or sp.currency != exp[1] or sp.total_amount != exp[2]:
            log.error("Подозрительная оплата: %s %s %s", sp.invoice_payload, sp.currency, sp.total_amount)
            for owner in owners.ids:
                await _safe_send(
                    m.bot, owner,
                    f"⚠️ Оплата не совпала с тарифом: user {m.chat.id}, payload {sp.invoice_payload}, "
                    f"{sp.total_amount} {sp.currency}, charge <code>{sp.telegram_payment_charge_id}</code>",
                )
            return await m.answer(f"Оплата получена, но тариф не распознан. Напиши {contact}.")
        plan, _, _, method, label = exp
        pid = db.add_payment(m.chat.id, method, plan.key, label, "ok", charge_id=sp.telegram_payment_charge_id)
        if pid is None:
            return  # повторная доставка того же платежа
        until = db.extend_sub(m.chat.id, plan.seconds)
        await m.answer(f"✅ Оплата получена. Подписка «{plan.title}» активна до {fmt_until(until)}.")
        for owner in owners.ids:
            await _safe_send(m.bot, owner, f"💰 Оплата {method}: {plan.title}, {label}, user <code>{m.chat.id}</code>")

    # ------------------------------------------------------------------ промокоды
    @r.message(Command("promo"))
    async def promo(m: Message, command: CommandObject):
        owners.bind(m.from_user)
        code = (command.args or "").strip().upper()
        if not code:
            return await m.answer("Отправь промокод так: /promo КОД")
        status, seconds = db.redeem_promo(code, m.chat.id)
        if status == "ok":
            until = db.extend_sub(m.chat.id, seconds)
            return await m.answer(
                f"🎉 Промокод принят: +{fmt_left(seconds)}. Подписка активна до {fmt_until(until)}."
            )
        await m.answer({
            "nope": "Такого промокода нет.",
            "used": "Ты уже использовал этот промокод.",
            "limit": "Лимит активаций этого промокода исчерпан.",
        }[status])

    @r.message(Command("newpromo"))
    async def newpromo(m: Message, command: CommandObject):
        if m.from_user.id not in owners:
            return
        args = (command.args or "").split()
        usage = "Пример: /newpromo FREE5 5m [лимит активаций]. Срок: 5m, 2h, 3d, 1w. Без лимита: 0 или не указывать."
        code = args[0].upper() if args else ""
        seconds = parse_duration(args[1]) if len(args) > 1 else None
        if not PROMO_RE.fullmatch(code) or seconds is None:
            return await m.answer(usage)
        limit = int(args[2]) if len(args) > 2 and args[2].isdigit() else 0
        if not db.add_promo(code, seconds, limit):
            return await m.answer("Такой промокод уже есть. Удалить: /delpromo " + code)
        await m.answer(f"Создан промокод {code}: {fmt_left(seconds)}, активаций: {limit or 'без лимита'}.")

    @r.message(Command("delpromo"))
    async def delpromo(m: Message, command: CommandObject):
        if m.from_user.id not in owners:
            return
        code = (command.args or "").strip().upper()
        await m.answer("Удалён." if code and db.del_promo(code) else "Не найден. Пример: /delpromo FREE5")

    @r.message(Command("promos"))
    async def promos(m: Message):
        if m.from_user.id not in owners:
            return
        rows = db.list_promos()
        if not rows:
            return await m.answer("Промокодов нет. Создать: /newpromo FREE5 5m")
        await m.answer("\n".join(
            f"<code>{row['code']}</code>: {fmt_left(row['seconds'])}, использовано {row['used']}"
            f"/{row['max_uses'] or '∞'}" for row in rows
        ))

    # ------------------------------------------------------------------ прочее
    @r.message(Command("status"))
    async def status(m: Message):
        owners.bind(m.from_user)
        if not owners.enabled:
            return await m.answer("Подписки отключены, уведомления приходят бесплатно.")
        if m.chat.id in owners:
            return await m.answer("Ты владелец: доступ активен всегда.")
        u = db.get_user(m.chat.id)
        now = int(time.time())
        if u and u.sub_until > now:
            return await m.answer(f"✅ Подписка до {fmt_until(u.sub_until)} (осталось {fmt_left(u.sub_until - now)}).")
        await m.answer("Подписки нет, уведомления не приходят. Оформить: /buy")

    @r.message(Command("paysupport"))
    async def paysupport(m: Message):
        await m.answer(f"Вопросы по оплате: {contact}")

    @r.message(Command("id"))
    async def my_id(m: Message):
        owners.bind(m.from_user)
        await m.answer(f"Твой id: <code>{m.from_user.id}</code>")

    @r.message(Command("grant"))
    async def grant(m: Message, command: CommandObject):
        if m.from_user.id not in owners:
            return
        args = (command.args or "").split()
        usage = "Пример: /grant 123456789 day [количество]. Тарифы: " + ", ".join(PLANS_BY_KEY)
        if len(args) < 2 or not args[0].lstrip("-").isdigit() or args[1] not in PLANS_BY_KEY:
            return await m.answer(usage)
        count = int(args[2]) if len(args) > 2 and args[2].isdigit() and int(args[2]) > 0 else 1
        plan, uid = PLANS_BY_KEY[args[1]], int(args[0])
        until = db.extend_sub(uid, plan.seconds * count)
        db.add_payment(uid, "owner", plan.key, f"x{count}", "ok")
        await m.answer(f"Выдано: {plan.title} x{count} для {uid}, до {fmt_until(until)}.")
        await _safe_send(m.bot, uid, f"✅ Тебе выдана подписка до {fmt_until(until)}.")

    return r
