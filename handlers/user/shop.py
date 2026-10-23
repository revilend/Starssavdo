"""
handlers/user/shop.py
=====================
🛍 Do'kon: toifalar, mahsulotlar va ikki usuldagi to'lov
(rasmiy Telegram Stars (XTR) yoki bot balansi).

Oqimlar:
 1. "🛍 Do'kon" — do'kon kirish sahifasi + toifalar klaviaturasi.
 2. `cat_{category}` — bazadagi faol mahsulotlar (nom, narx, tavsif).
 3. `buy_prod_{product_id}` — mahsulot tafsilotlari + usul tanlash:
      ⭐️ Rasmiy Telegram Stars (XTR) bilan to'lash
      💳 Botdagi balansimdan yechish
 4. `pay_xtr_{product_id}` — bazada pending buyurtma + `sendInvoice`
    (XTR); `pre_checkout_query` 10 soniya ichida tasdiqlanadi;
    `successful_payment` buyurtmani `paid` qiladi, chek yuboradi va
    adminga darhol xabarnoma yuboradi.
 5. `pay_balance_{product_id}` — bot balansi tekshiriladi, yetarli bo'lsa
    atomik xarid (balans - , transactions audit, buyurtma paid),
    yetarli emasda alert.

Xavfsizlik:
 - Payload "order:{id}:{user_id}" — pre_checkout'da egasi tekshiriladi
   (boshqa foydalanuvchining invoysi bilan to'lov rad etiladi).
 - `OrderRepository.complete_order` idempotent (takroriy
   successful_payment xavfsiz).
 - Balans orqali xarid atomik (`create_balance_order`): race'da
   InsufficientBalanceError -> foydalanuvchiga alert.
"""

from __future__ import annotations

import html
import logging
from typing import Any

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, PreCheckoutQuery

from config.settings import settings
from database.queries import (
    ORDER_PENDING,
    PAYMENT_XTR,
    PRODUCT_CATEGORIES,
    InsufficientBalanceError,
    OrderRepository,
    ProductRepository,
    RepositoryError,
    UserRepository,
)
from keyboards.inline import (
    CB_PAY_BALANCE_PREFIX,
    CB_PAY_XTR_PREFIX,
    CB_SHOP_CATEGORIES,
    CATEGORY_EMOJI,
    get_products_list_kb,
    get_shop_categories_kb,
    get_topup_needed_kb,
)
from services.button_registry import ButtonText
from services.gift_delivery import deliver_gift
from services.payments import (
    ORDER_PAYLOAD_PREFIX,
    build_order_payload,
    parse_order_payload,
    send_stars_invoice,
)

logger = logging.getLogger(__name__)

router = Router(name="user_shop")

# Do'kon kirish matni (HTML)
SHOP_WELCOME_TEXT = (
    "🛍 <b>NFTYUT Rasmiy Do'koniga xush kelibsiz!</b>\n\n"
    "Kerakli bo'limni tanlang:\n"
    "⭐️ <b>Stars</b> — Rasmiy Telegram Stars to'plamlari\n"
    "💎 <b>Premium</b> — Tezkor Telegram Premium obunasi\n"
    "🖼 <b>NFT</b> — Telegram eksklyuziv raqamli kolleksiyalari\n"
    "🎁 <b>Mayda Giftlar</b> — Profilingiz yoki do'stlaringiz uchun "
    "ajoyib sovg'alar\n\n"
    "To'lovlarni rasmiy Telegram Stars yoki botdagi balansingiz orqali "
    "amalga oshirishingiz mumkin!"
)

# Toifa sarlavhalari (HTML)
CATEGORY_TITLES: dict[str, str] = {
    "stars": "⭐️ <b>Telegram Stars to'plamlari</b>",
    "premium": "💎 <b>Telegram Premium obunalari</b>",
    "nft": "🖼 <b>Telegram NFT kolleksiyalari</b>",
    "gift": "🎁 <b>Mayda Giftlar</b>",
}

# Bo'sh toifa matni
EMPTY_CATEGORY_TEXT = (
    "{category_title}\n\n"
    "Hozircha bu bo'limda mahsulot yo'q. Tez orada to'ldiriladi! 🚀"
)

# Mahsulot tafsilotlari (HTML)
PRODUCT_DETAIL_TEXT = (
    "{emoji} <b>{title}</b>\n\n"
    "📝 <b>Tavsif:</b> {description}\n\n"
    "💰 <b>Narx:</b> {price} XTR\n\n"
    "Xarid usulini tanlang 👇"
)

# XTR chek (spetsifikatsiya matni, HTML)
PAYMENT_RECEIPT_TEXT = (
    "✅ <b>To'lov muvaffaqiyatli qabul qilindi!</b>\n\n"
    "🧾 Buyurtma ID: #{order_id}\n"
    "📦 Mahsulot: {product_name}\n"
    "⭐️ To'langan: {total_amount} XTR\n\n"
    "Buyurtmangiz tez orada yetkaziladi yoki hisobingizga biriktiriladi!"
)

# Balans orqali xarid cheki (HTML)
BALANCE_RECEIPT_TEXT = (
    "✅ <b>Xarid muvaffaqiyatli yakunlandi!</b>\n\n"
    "🧾 Buyurtma ID: #{order_id}\n"
    "📦 Mahsulot: {product_name}\n"
    "⭐️ To'landi: {price} XTR (bot balansidan)\n"
    "💰 Qolgan balans: {new_balance} ⭐️\n\n"
    "Buyurtmangiz tez orada yetkaziladi yoki hisobingizga biriktiriladi!"
)

# Adminga xabarnoma (spetsifikatsiya matni, HTML)
ADMIN_BUY_ALERT_TEXT = (
    "🔔 <b>Yangi xarid amalga oshirildi!</b>\n"
    "Foydalanuvchi: {who} (ID: {user_id})\n"
    "Mahsulot: {product_name}\n"
    "Summa: {total_amount} Stars"
)

# Xatolik alertlari
ALERT_PRODUCT_NOT_FOUND = "❌ Mahsulot topilmadi!"
ALERT_NOT_ENOUGH_BALANCE = (
    "❌ Balansingizda Stars yetarli emas! "
    "Do'stlaringizni taklif qilib Stars to'plang."
)
ALERT_CATEGORY_NOT_FOUND = "❌ Toifa topilmadi!"
ALERT_INVOICE_FAILED = "❌ Invoysni yuborib bo'lmadi! Keyinroq urinib ko'ring."
ALERT_PURCHASE_FAILED = "❌ Xaridni amalga oshirib bo'lmadi! Keyinroq urinib ko'ring."

# Balans yetarli bo'lmaganda yuboriladigan xabar (pastda to'ldirish tugmasi)
NOT_ENOUGH_BALANCE_TEXT = (
    "❌ <b>Balansingizda Stars yetarli emas!</b>\n\n"
    "├ Mahsulot narxi: <b>{price} ⭐️</b>\n"
    "├ Joriy balans: <b>{balance} ⭐️</b>\n"
    "└ Yetishmayapti: <b>{missing} ⭐️</b>\n\n"
    "Avval «💳 Balans to'ldirish» bo'limidan hisobingizni to'ldiring — "
    "to'lov avtomatik tasdiqlanadi."
)


# =============================================================
# Yordamchilar
# =============================================================
def _parse_product_id(data: str, prefix: str) -> int | None:
    """
    "buy_prod_123" kabi callback'dan mahsulot ID'sini ajratib oladi.

    Noto'g'ri format (son emas, manfiy) -> None.
    """
    raw = (data or "")[len(prefix):].strip()
    if not raw.isdigit():
        return None
    product_id = int(raw)
    return product_id if product_id > 0 else None


async def _notify_admins(
    bot, user, product_name: str, total_amount: int
) -> None:
    """Barcha adminlarga xarid haqida darhol xabarnoma yuboradi."""
    if user.username:
        who = f"@{html.escape(user.username)}"
    else:
        who = html.escape(user.first_name or str(user.id))

    text = ADMIN_BUY_ALERT_TEXT.format(
        who=who,
        user_id=user.id,
        product_name=html.escape(product_name),
        total_amount=total_amount,
    )
    for admin_id in settings.ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, parse_mode="HTML")
        except TelegramAPIError:
            # Admin blokagan yoki chat topilmadi — xaridni buzmaslik kerak
            logger.warning("Adminga xabarnoma yetmadi: admin=%s", admin_id)


# =============================================================
# 1. DO'KON KIRISHI
# =============================================================
@router.message(StateFilter("*"), ButtonText("menu_shop"))
@router.message(StateFilter("*"), F.text.contains("Do'kon"))
async def shop_menu(message: Message, state: FSMContext | None = None) -> None:
    """🛍 Do'kon — kirish sahifasi va toifalar menyusi (istalgan holatda)."""
    if state is not None:
        await state.clear()
    await message.answer(
        SHOP_WELCOME_TEXT,
        parse_mode="HTML",
        reply_markup=get_shop_categories_kb(),
    )
    logger.info(
        "Do'kon ochildi: user=%s",
        message.from_user.id if message.from_user else "?",
    )


@router.message(StateFilter("*"), ButtonText("menu_gifts"))
@router.message(StateFilter("*"), F.text.contains("Mayda Giftlar"))
async def gifts_menu(message: Message, state: FSMContext | None = None) -> None:
    """🎁 Mayda Giftlar — to'g'ridan-to'g'ri gift toifasini ko'rsatadi."""
    if state is not None:
        await state.clear()
    text, markup = await _category_view("gift")
    await message.answer(text, parse_mode="HTML", reply_markup=markup)
    logger.info(
        "Giftlar bo'limi ochildi: user=%s",
        message.from_user.id if message.from_user else "?",
    )


# =============================================================
# 2. TOIFA — MAHSULOTLAR RO'YXATI
# =============================================================
async def _category_view(category: str) -> tuple[str, Any]:
    """Toifa uchun (matn, klaviatura) tayyorlaydi — callback va reply uchun."""
    products = await ProductRepository.get_products_by_category(category)
    category_title = CATEGORY_TITLES.get(category, CATEGORY_EMOJI.get(category, "🛍"))

    if not products:
        return (
            EMPTY_CATEGORY_TEXT.format(category_title=category_title),
            get_shop_categories_kb(),
        )

    lines: list[str] = []
    for index, product in enumerate(products, start=1):
        desc_html = ""
        if product.description:
            desc_html = f"\n   <i>{html.escape(product.description)}</i>"
        lines.append(
            f"{index}. <b>{html.escape(product.title)}</b> — "
            f"<b>{product.price_xtr} XTR</b>{desc_html}"
        )

    text = (
        f"{category_title}\n\n" + "\n".join(lines) +
        "\n\nXarid qilish uchun tugmani bosing 👇"
    )
    return text, get_products_list_kb(products, category)


@router.callback_query(
    StateFilter("*"), F.data.startswith("cat_")
)
async def shop_category(
    callback: CallbackQuery, state: FSMContext | None = None
) -> None:
    """Tanlangan toifadagi faol mahsulotlar (nom, narx, tavsif)."""
    category = (callback.data or "")[len("cat_"):].strip()
    if category not in PRODUCT_CATEGORIES:
        await callback.answer(ALERT_CATEGORY_NOT_FOUND, show_alert=True)
        return

    if state is not None:
        await state.clear()
    await callback.answer()
    text, markup = await _category_view(category)
    await callback.message.answer(text, parse_mode="HTML", reply_markup=markup)
    logger.info(
        "Toifa ko'rsatildi: user=%s category=%s",
        callback.from_user.id, category,
    )


# =============================================================
# 2.1. "◀️ ORQAGA" — TOIFALAR MENYUSIGA QAYTISH
# =============================================================
@router.callback_query(StateFilter("*"), F.data == CB_SHOP_CATEGORIES)
async def shop_categories_callback(
    callback: CallbackQuery, state: FSMContext | None = None
) -> None:
    """`shop_categories` — mahsulotlar ro'yxatidan toifalarga qaytaradi."""
    if state is not None:
        await state.clear()
    await callback.answer()

    try:
        await callback.message.edit_text(
            SHOP_WELCOME_TEXT,
            parse_mode="HTML",
            reply_markup=get_shop_categories_kb(),
        )
    except (TelegramBadRequest, AttributeError):
        # Xabar tahrirlanmaydi (rasm/eski xabar) — yangi xabar yuboramiz
        await callback.message.answer(
            SHOP_WELCOME_TEXT,
            parse_mode="HTML",
            reply_markup=get_shop_categories_kb(),
        )


# =============================================================
# 3. MAHSULOT — TASHILOTLARI VA USUL TANLASH
# =============================================================
async def _purchase_with_balance(
    callback: CallbackQuery, product_id: int
) -> None:
    """
    Mahsulotni faqat BOT BALANSI orqali sotib oladi.

    Balans yetarli bo'lmasa — foydalanuvchiga to'ldirish tugmasi yuboriladi;
    yetarli bo'lsa — balans atomik yechiladi, sovg'a (gift_id bo'lsa)
    avtomatik yuboriladi va adminga xabarnoma boradi.
    """
    product = await ProductRepository.get_product_by_id(product_id)
    if product is None or not product.is_active:
        await callback.answer(ALERT_PRODUCT_NOT_FOUND, show_alert=True)
        return

    user_id = callback.from_user.id
    user = await UserRepository.get_user(user_id)
    if user is None:
        await callback.answer(
            "❌ Profilingiz topilmadi! /start buyrug'ini bosing.",
            show_alert=True,
        )
        return

    if user.stars_balance < product.price_xtr:
        await callback.answer(ALERT_NOT_ENOUGH_BALANCE, show_alert=True)
        await callback.message.answer(
            NOT_ENOUGH_BALANCE_TEXT.format(
                price=product.price_xtr,
                balance=user.stars_balance,
                missing=product.price_xtr - user.stars_balance,
            ),
            parse_mode="HTML",
            reply_markup=get_topup_needed_kb(),
        )
        return

    # Atomik xarid (balans - , audit, order=pending -> paid)
    try:
        order = await OrderRepository.create_balance_order(user_id, product.id)
    except InsufficientBalanceError:
        # Parallel sarf — balans shu oraliqda kamaygan bo'lishi mumkin
        await callback.answer(ALERT_NOT_ENOUGH_BALANCE, show_alert=True)
        await callback.message.answer(
            NOT_ENOUGH_BALANCE_TEXT.format(
                price=product.price_xtr,
                balance=0,
                missing=product.price_xtr,
            ),
            parse_mode="HTML",
            reply_markup=get_topup_needed_kb(),
        )
        return
    except RepositoryError:
        logger.exception(
            "Balans xaridi amalga oshmadi: user=%s product=%s",
            user_id, product.id,
        )
        await callback.answer(ALERT_PURCHASE_FAILED, show_alert=True)
        return

    new_balance = user.stars_balance - product.price_xtr
    await callback.answer("✅ Xarid muvaffaqiyatli!")
    await callback.message.answer(
        BALANCE_RECEIPT_TEXT.format(
            order_id=order.id,
            product_name=html.escape(product.title),
            price=product.price_xtr,
            new_balance=new_balance,
        ),
        parse_mode="HTML",
    )

    # Gift mahsuloti bo'lsa — avtomatik sendGift (gift_id mavjud bo'lsa)
    delivered = await deliver_gift(callback.bot, user_id, product)
    if delivered:
        await callback.message.answer(
            "🎁 <b>Sovg'angiz avtomatik yuborildi!</b>\n"
            "Telegram sovg'alar bo'limidan ko'rishingiz mumkin."
        )

    # Adminga har doim xabarnoma (yetkazish nazorati uchun)
    await _notify_admins(
        callback.bot, callback.from_user, product.title, product.price_xtr
    )

    logger.info(
        "Balans orqali xarid: user=%s order=%s product=%s -%sXTR gift=%s",
        user_id, order.id, product.id, product.price_xtr, delivered,
    )


@router.callback_query(StateFilter("*"), F.data.startswith("buy_prod_"))
async def buy_product(
    callback: CallbackQuery, state: FSMContext | None = None
) -> None:
    """Mahsulot tanlandi — darhol bot balansidan sotib olinadi."""
    if state is not None:
        await state.clear()

    product_id = _parse_product_id(callback.data or "", "buy_prod_")
    if product_id is None:
        await callback.answer(ALERT_PRODUCT_NOT_FOUND, show_alert=True)
        return

    await _purchase_with_balance(callback, product_id)


# =============================================================
# 4. RASMIY TELEGRAM STARS (XTR) ORQALI TO'LOV
# =============================================================
@router.callback_query(F.data.startswith(CB_PAY_XTR_PREFIX))
async def pay_with_xtr(callback: CallbackQuery) -> None:
    """Pending buyurtma yaratadi va rasmiy XTR invoicesini yuboradi."""
    product_id = _parse_product_id(callback.data or "", CB_PAY_XTR_PREFIX)
    if product_id is None:
        await callback.answer(ALERT_PRODUCT_NOT_FOUND, show_alert=True)
        return

    product = await ProductRepository.get_product_by_id(product_id)
    if product is None or not product.is_active:
        await callback.answer(ALERT_PRODUCT_NOT_FOUND, show_alert=True)
        return

    user_id = callback.from_user.id

    # Bazada pending buyurtma yoziladi (status='pending')
    try:
        order = await OrderRepository.create_order(
            user_id=user_id,
            product_id=product.id,
            payment_method=PAYMENT_XTR,
            amount_xtr=product.price_xtr,
            order_data={
                "product_title": product.title,
                "category": product.category,
            },
        )
    except RepositoryError:
        logger.exception(
            "Buyurtma yaratilmadi: user=%s product=%s", user_id, product.id
        )
        await callback.answer(ALERT_PURCHASE_FAILED, show_alert=True)
        return

    # Invoys yuboriladi (Telegram Stars balansi bilan to'lov)
    try:
        await send_stars_invoice(
            bot=callback.bot,
            chat_id=callback.message.chat.id,
            title=product.title,
            description=product.description or f"{product.title} — {product.price_xtr} XTR",
            payload=build_order_payload(order.id, user_id),
            amount_xtr=product.price_xtr,
        )
    except (TelegramAPIError, ValueError):
        logger.exception(
            "Invoys yuborilmadi: user=%s order=%s", user_id, order.id
        )
        await callback.answer(ALERT_INVOICE_FAILED, show_alert=True)
        return

    await callback.answer("⭐️ To'lov sahifasi yuborildi ✅")
    logger.info(
        "XTR invoys yuborildi: user=%s order=%s product=%s amount=%s",
        user_id, order.id, product.id, product.price_xtr,
    )


@router.pre_checkout_query(
    F.invoice_payload.startswith(f"{ORDER_PAYLOAD_PREFIX}:")
)
async def on_pre_checkout(query: PreCheckoutQuery) -> None:
    """
    To'lov tasdiqlash so'rovi — 10 soniya ichida javob BERILISHI SHART.

    Tekshiruvlar: payload formati, invoys egasi, buyurtma `pending`
    holatida va `xtr` usulida ekanligi. Muvaffaqiyatli holatda ok=True.
    """
    parsed = parse_order_payload(query.invoice_payload)
    if parsed is None:
        logger.warning(
            "pre_checkout: noto'g'ri payload=%r user=%s",
            query.invoice_payload, query.from_user.id,
        )
        await query.answer(ok=False, error_message="Noto'g'ri to'lov so'rovi!")
        return

    order_id, owner_id = parsed
    if owner_id != query.from_user.id:
        await query.answer(
            ok=False, error_message="To'lov boshqa foydalanuvchiga tegishli!"
        )
        return

    order = await OrderRepository.get_order(order_id)
    if (
        order is None
        or order.user_id != query.from_user.id
        or order.status != ORDER_PENDING
        or order.payment_method != PAYMENT_XTR
    ):
        await query.answer(
            ok=False,
            error_message="Buyurtma topilmadi yoki allaqachon qayta ishlangan!",
        )
        return

    await query.answer(ok=True)
    logger.info("pre_checkout tasdiqlandi: user=%s order=%s",
                query.from_user.id, order_id)


@router.message(
    F.successful_payment.invoice_payload.startswith(f"{ORDER_PAYLOAD_PREFIX}:")
)
async def on_successful_payment(message: Message) -> None:
    """
    Stars orqali to'lov muvaffaqiyatli yakunlangach:
      1) buyurtma `paid` qilinadi (idempotent);
      2) foydalanuvchiga chek yuboriladi;
      3) barcha adminlarga darhol xabarnoma yuboriladi.
    """
    sp = message.successful_payment
    charge_id = sp.telegram_payment_charge_id
    total_amount = sp.total_amount

    parsed = parse_order_payload(sp.invoice_payload)
    if parsed is None:
        # To'lov bo'lgan, lekin buyurtma topilmadi — yo'qotmaslik uchun
        # foydalanuvchiga xabar, admin qo'lda ko'radi.
        logger.error(
            "successful_payment: noto'g'ri payload=%r user=%s charge=%s",
            sp.invoice_payload, message.from_user.id, charge_id,
        )
        await message.answer(
            "✅ To'lov qabul qilindi!\n"
            "Buyurtmangiz administrator tomonidan qo'lda tekshiriladi."
        )
        return

    order_id, _ = parsed
    try:
        order = await OrderRepository.complete_order(order_id, charge_id)
    except RepositoryError:
        logger.exception(
            "Buyurtma topilmadi: order=%s charge=%s", order_id, charge_id
        )
        await message.answer(
            "✅ To'lov qabul qilindi!\n"
            "Buyurtmangiz administrator tomonidan qo'lda tekshiriladi."
        )
        return

    # Mahsulot nomi (snapshot order_data'dan ham olinadi — o'chirilgan
    # mahsulot uchun ham chek to'g'ri ko'rinadi)
    product = await ProductRepository.get_product_by_id(order.product_id)
    if product is not None:
        product_name = product.title
    else:
        product_name = (order.order_data or {}).get("product_title", "Mahsulot")

    await message.answer(
        PAYMENT_RECEIPT_TEXT.format(
            order_id=order.id,
            product_name=html.escape(product_name),
            total_amount=total_amount,
        ),
        parse_mode="HTML",
    )

    # Gift mahsuloti bo'lsa — rasmiy sendGift bilan avtomatik yetkazish
    delivered = False
    if product is not None:
        delivered = await deliver_gift(
            message.bot, message.from_user.id, product
        )

    if delivered:
        await message.answer(
            "🎁 <b>Sovg'angiz avtomatik yuborildi!</b>\n"
            "Telegram sovg'alar bo'limidan ko'rishingiz mumkin."
        )
    else:
        # gift_id yo'q yoki yuborilmadi — admin qo'lda yetkazadi
        await _notify_admins(
            message.bot, message.from_user, product_name, total_amount
        )

    logger.info(
        "XTR to'lov qabul qilindi: user=%s order=%s charge=%s amount=%s gift=%s",
        message.from_user.id, order.id, charge_id, total_amount, delivered,
    )


# =============================================================
# 5. BOT BALANSI ORQALI XARID (eski "pay_balance_" tugmalari uchun)
# =============================================================
@router.callback_query(F.data.startswith(CB_PAY_BALANCE_PREFIX))
async def pay_with_balance(callback: CallbackQuery) -> None:
    """Bot balansidan xarid — `_purchase_with_balance` orqali."""
    product_id = _parse_product_id(callback.data or "", CB_PAY_BALANCE_PREFIX)
    if product_id is None:
        await callback.answer(ALERT_PRODUCT_NOT_FOUND, show_alert=True)
        return

    await _purchase_with_balance(callback, product_id)
