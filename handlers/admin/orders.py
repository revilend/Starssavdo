"""
handlers/admin/orders.py
========================
Admin panel — SO'MDA to'langan buyurtmalar (Premium / NFT / Stars / Gift).

Imkoniyatlar:
 - oxirgi buyurtmalar ro'yxati (holati, summasi, mahsuloti);
 - buyurtma kartasi: foydalanuvchi, @username / TON hamyon, to'lov summasi;
 - Premium uchun **[✅ Fragment orqali yuborildi]**, NFT uchun
   **[✅ NFT yuborildi]** tugmasi — bosilganda buyurtma `fulfilled_at`
   bilan belgilanadi va foydalanuvchiga tasdiqnoma yuboriladi.

To'lovlar `services/order_payments.py` orqali avtomatik tasdiqlanadi;
bu bo'lim faqat qo'lda yetkaziladigan mahsulotlar uchun.
"""

from __future__ import annotations

import html
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramAPIError
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from config.settings import settings
from database.models import Order
from database.queries import OrderRepository, RepositoryError
from keyboards.builders import CB_ADM_BACK, CB_ADM_ORDERS
from services.card_payment import format_som
from services.order_payments import CB_ORDER_DONE_PREFIX

logger = logging.getLogger(__name__)

router = Router(name="admin_orders")

router.message.filter(F.from_user.id.in_(settings.ADMIN_IDS))
router.callback_query.filter(F.from_user.id.in_(settings.ADMIN_IDS))

ORDERS_LIST_TEXT = (
    "🧾 <b>BUYURTMALAR (SO'MDA)</b>\n\n"
    "├ Jami ko'rsatilgan: <b>{total}</b> ta\n"
    "├ To'langan: <b>{paid}</b> ta\n"
    "└ Kutilmoqda: <b>{pending}</b> ta\n\n"
    "Buyurtmani tanlab, tafsilotlarni ko'ring 👇"
)

STATUS_LABELS = {
    "pending": "🕓 kutilmoqda",
    "paid": "✅ to'langan",
    "cancelled": "❌ bekor qilingan",
    "expired": "⏳ muddati tugagan",
}

ORDER_DETAIL_TEXT = (
    "🧾 <b>BUYURTMA #{order_id}</b>\n\n"
    "├ Holat: {status}\n"
    "├ Mahsulot: <b>{title}</b>\n"
    "├ Turi: <code>{category}</code>\n"
    "├ Narx: <b>{price} so'm</b>\n"
    "├ To'landi: <b>{pay_amount}</b>\n"
    "├ Foydalanuvchi: <code>{user_id}</code>\n"
    "{target}"
    "└ Yaratilgan: {created}\n\n"
    "{delivery}"
)


def _status_label(status: str) -> str:
    return STATUS_LABELS.get(status, status)


def _orders_kb(orders: list[Order]) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for order in orders:
        data = order.order_data or {}
        title = str(data.get("product_title") or f"#{order.product_id}")
        amount = format_som(order.pay_amount or order.price_uzs)
        builder.row(
            InlineKeyboardButton(
                text=f"{_status_label(order.status)} — {title} — {amount} so'm",
                callback_data=f"adm_ord_{order.id}",
                style="success" if order.status == "paid" else "primary",
            )
        )
    builder.row(
        InlineKeyboardButton(
            text="◀️ Admin panelga qaytish", callback_data=CB_ADM_BACK, style="primary"
        )
    )
    return builder.as_markup()


def _order_detail_kb(order: Order) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    data = order.order_data or {}
    category = str(data.get("category") or "")

    if order.status == "paid" and order.fulfilled_at is None:
        if category == "premium":
            builder.row(
                InlineKeyboardButton(
                    text="✅ Fragment orqali yuborildi",
                    callback_data=f"{CB_ORDER_DONE_PREFIX}{order.id}",
                    style="success",
                )
            )
        elif category == "nft":
            builder.row(
                InlineKeyboardButton(
                    text="✅ NFT yuborildi",
                    callback_data=f"{CB_ORDER_DONE_PREFIX}{order.id}",
                    style="success",
                )
            )

    builder.row(
        InlineKeyboardButton(
            text="◀️ Buyurtmalar", callback_data=CB_ADM_ORDERS, style="primary"
        )
    )
    return builder.as_markup()


def _order_detail_text(order: Order) -> str:
    data = order.order_data or {}
    title = str(data.get("product_title") or f"Mahsulot #{order.product_id}")
    category = str(data.get("category") or "?")
    target = ""
    if data.get("target_username"):
        target = f"├ Username: <b>@{html.escape(str(data['target_username']))}</b>\n"
    elif data.get("wallet"):
        target = f"├ TON hamyon: <code>{html.escape(str(data['wallet']))}</code>\n"

    delivery = ""
    if order.fulfilled_at is not None:
        delivery = "✅ <i>Yetkazilgan deb belgilangan.</i>"
    elif order.status == "paid":
        delivery = "⏳ <i>Yetkazish kutilmoqda.</i>"
    else:
        delivery = "—"

    return ORDER_DETAIL_TEXT.format(
        order_id=order.id,
        status=_status_label(order.status),
        title=html.escape(title),
        category=html.escape(category),
        price=format_som(order.price_uzs),
        pay_amount=f"{format_som(order.pay_amount or 0)} so'm",
        user_id=order.user_id,
        target=target,
        created=order.created_at.strftime("%d.%m.%Y %H:%M") if order.created_at else "—",
        delivery=delivery,
    )


@router.callback_query(F.data == CB_ADM_ORDERS)
async def admin_orders_list(callback: CallbackQuery) -> None:
    """Buyurtmalar ro'yxati."""
    orders = await OrderRepository.get_orders(limit=30)
    await callback.answer()

    text = ORDERS_LIST_TEXT.format(
        total=len(orders),
        paid=sum(1 for order in orders if order.status == "paid"),
        pending=sum(1 for order in orders if order.status == "pending"),
    )
    if not orders:
        text += "\n\nHozircha buyurtmalar yo'q."

    markup = _orders_kb(orders)
    try:
        await callback.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest:
        await callback.message.answer(text, reply_markup=markup)


@router.callback_query(F.data.startswith("adm_ord_"))
async def admin_order_detail(callback: CallbackQuery) -> None:
    """Bitta buyurtma kartasi."""
    raw = (callback.data or "")[len("adm_ord_"):].strip()
    if not raw.isdigit():
        await callback.answer("❌ Noto'g'ri buyurtma ID", show_alert=True)
        return

    order = await OrderRepository.get_order(int(raw))
    if order is None:
        await callback.answer("❌ Buyurtma topilmadi", show_alert=True)
        return

    await callback.answer()
    try:
        await callback.message.edit_text(
            _order_detail_text(order), reply_markup=_order_detail_kb(order)
        )
    except TelegramBadRequest:
        await callback.message.answer(
            _order_detail_text(order), reply_markup=_order_detail_kb(order)
        )


@router.callback_query(F.data.startswith(CB_ORDER_DONE_PREFIX))
async def admin_order_done(callback: CallbackQuery) -> None:
    """[✅ Fragment / NFT yuborildi] — yetkazilganini belgilaydi."""
    raw = (callback.data or "")[len(CB_ORDER_DONE_PREFIX):].strip()
    if not raw.isdigit():
        await callback.answer("❌ Noto'g'ri buyurtma ID", show_alert=True)
        return

    try:
        order = await OrderRepository.mark_order_fulfilled(int(raw))
    except RepositoryError:
        await callback.answer("❌ Buyurtma topilmadi", show_alert=True)
        return

    await callback.answer("✅ Yetkazilgan deb belgilandi")

    try:
        await callback.message.edit_text(
            _order_detail_text(order), reply_markup=_order_detail_kb(order)
        )
    except (TelegramBadRequest, AttributeError):
        pass

    try:
        await callback.bot.send_message(
            order.user_id,
            "✅ <b>Buyurtmangiz yetkazildi!</b>\n\n"
            f"🧾 Buyurtma: <code>#{order.id}</code>\n"
            "Xaridingiz uchun rahmat! 🛒",
            parse_mode="HTML",
        )
    except TelegramAPIError:
        logger.warning("Yetkazish tasdiqnomasi yuborilmadi: user=%s", order.user_id)

    logger.info(
        "Buyurtma yetkazildi (admin=%s): order=%s",
        callback.from_user.id, order.id,
    )
