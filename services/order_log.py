"""
services/order_log.py
=====================
Zakaz kanali — barcha yangi xaridlar (Premium, Gift, NFT, Stars) uchun
chiroyli chek xabari.

Qanday ishlaydi:
 1. Admin panelda [ 📬 Zakaz kanali ] orqali yopiq kanal ID'si saqlanadi
    (`settings.order_log_chat_id`). Bot kanalda admin bo'lishi shart.
 2. Har bir muvaffaqiyatli xarid (`deliver_order`, XTR to'lov, balans
    xaridi) `notify_order_paid()` ni chaqiradi.
 3. Chek kanalga yuboriladi; tagida [ ✅ Topshirildi ] va
    [ ❌ Bekor qilish ] tugmalari bo'ladi:
      - `zakaz_done_{order_id}`  — buyurtma yetkazilgan deb belgilanadi
        (`fulfilled_at` + foydalanuvchiga xabar);
      - `zakaz_cancel_{order_id}` — buyurtma bekor qilinadi (`cancelled`).
 4. Sozlanmagan kanal (0/bo'sh) — xabar yuborilmaydi, xatolik chiqmaydi.

Xavfsizlik: barcha xatolar ushlanadi va loglanadi — chek yuborilishi
xarid oqimini hech qachon buzmaydi.
"""

from __future__ import annotations

import html
import logging

from aiogram.exceptions import TelegramAPIError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from database.queries import SettingsRepository
from services.card_payment import format_som

logger = logging.getLogger(__name__)

# Callback konstantalari (admin tugmalari)
CB_ZAKAZ_DONE_PREFIX = "zakaz_done_"
CB_ZAKAZ_CANCEL_PREFIX = "zakaz_cancel_"

# Chek matni (HTML)
ZAKAZ_RECEIPT_TEXT = (
    "🧾 <b>YANGI XARID — NFT YUT</b>\n\n"
    "├ Buyurtma: <code>#{order_id}</code>\n"
    "├ Foydalanuvchi: {user}\n"
    "├ Mahsulot: <b>{title}</b>\n"
    "├ To'lov usuli: <code>{method}</code>\n"
    "└ Summa: <b>{price}</b>\n\n"
    "📦 Yetkazish: {delivery}"
)

# To'lov usullari yorliqlari
METHOD_LABELS: dict[str, str] = {
    "card": "💳 HUMO karta (UZS)",
    "xtr": "⭐️ Telegram Stars (XTR)",
    "balance": "💰 Bot balansi",
}

# Yetkazish holati yorliqlari (kategoriya bo'yicha)
DELIVERY_LABELS: dict[str, str] = {
    "stars": "⭐️ Stars balansga qo'shildi",
    "gift": "🎁 Sovg'a yuboriladi",
    "premium": "💎 Premium ulanadi (Fragment)",
    "nft": "🖼 NFT yuboriladi",
}


def _zakaz_kb(order_id: int) -> InlineKeyboardMarkup:
    """[ ✅ Topshirildi ] + [ ❌ Bekor qilish ]."""
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(
            text="✅ Topshirildi",
            callback_data=f"{CB_ZAKAZ_DONE_PREFIX}{order_id}",
            style="success",
        ),
        InlineKeyboardButton(
            text="❌ Bekor qilish",
            callback_data=f"{CB_ZAKAZ_CANCEL_PREFIX}{order_id}",
            style="danger",
        ),
    )
    return builder.as_markup()


def build_zakaz_receipt(
    order_id: int,
    user_label: str,
    title: str,
    method: str,
    price_label: str,
    category: str,
) -> str:
    """Chek matnini shakllantiradi (test uchun alohida funksiya)."""
    return ZAKAZ_RECEIPT_TEXT.format(
        order_id=order_id,
        user=user_label,
        title=html.escape(title),
        method=html.escape(METHOD_LABELS.get(method, method)),
        price=html.escape(price_label),
        delivery=DELIVERY_LABELS.get(category, "📦 Admin tomonidan yetkaziladi"),
    )


async def notify_order_paid(
    bot,
    order,
    product=None,
    user=None,
) -> bool:
    """
    To'langan buyurtma haqida zakaz kanaliga chek yuboradi.

    Args:
        bot: aiogram Bot instansi.
        order: Order modeli (id, user_id, payment_method, price_uzs, ...).
        product: Product yoki None (order_data'dan nom olinadi).
        user: User yoki None (kanaldagi ko'rinish uchun).

    Returns:
        True — chek yuborildi; False — kanal sozlanmagan yoki xatolik.
    """
    try:
        chat_id = await SettingsRepository.get_order_log_chat_id()
    except Exception:  # noqa: BLE001 — DB xatosi chekka to'sqinlik qilmasin
        logger.warning("Zakaz kanali ID'sini o'qib bo'lmadi")
        return False

    if not chat_id:
        return False

    data = order.order_data or {}
    title = str(
        data.get("product_title")
        or getattr(product, "title", None)
        or "Mahsulot"
    )
    category = str(
        data.get("category") or getattr(product, "category", "") or ""
    )

    if user is not None:
        who = user.first_name or user.username or str(order.user_id)
        if user.username:
            who = f"{who} (@{user.username})"
        user_label = f"{html.escape(str(who))} · <code>{order.user_id}</code>"
    else:
        user_label = f"<code>{order.user_id}</code>"

    price = format_som(order.price_uzs or 0)
    text = build_zakaz_receipt(
        order_id=order.id,
        user_label=user_label,
        title=title,
        method=str(order.payment_method or ""),
        price_label=f"{price} so'm",
        category=category,
    )

    try:
        await bot.send_message(
            chat_id,
            text,
            parse_mode="HTML",
            reply_markup=_zakaz_kb(order.id),
        )
    except TelegramAPIError as exc:
        logger.warning(
            "Zakaz kanaliga chek yuborilmadi (chat=%s order=%s): %s",
            chat_id, order.id, exc,
        )
        return False

    logger.info(
        "Zakaz kanaliga chek yuborildi: chat=%s order=%s", chat_id, order.id
    )
    return True
