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
    yetarli emasda xabar.
 6. "🎁 Mayda Giftlar" reply tugmasi — matn emoji bilan yoki emojisiz
    kelsa ham gift kategoriyasi ochiladi (`is_gift_menu` filtri).
 7. Navigatsiya: `shop_categories` (toifalarga qaytish) va
    `back_to_menu` (bosh menyu) callback'lari shu routerda.

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

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import CallbackQuery, Message, PreCheckoutQuery

from config.settings import settings
from database.queries import (
    ORDER_PENDING,
    PAYMENT_BALANCE,
    PAYMENT_XTR,
    PRODUCT_CATEGORIES,
    InsufficientBalanceError,
    OrderRepository,
    ProductRepository,
    RepositoryError,
    UserRepository,
)
from keyboards.inline import (
    CB_BACK_TO_MENU,
    CB_PAY_BALANCE_PREFIX,
    CB_PAY_XTR_PREFIX,
    CB_SHOP_CATEGORIES,
    CATEGORY_EMOJI,
    get_payment_methods_kb,
    get_products_list_kb,
    get_shop_categories_kb,
)
from keyboards.reply import BTN_SHOP, get_main_menu_kb
from services.payments import (
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

# "Mayda Giftlar" reply tugmasini moslashuvchan aniqlash uchun kalit so'zlar.
# Tugma matni emoji bilan ("🎁 Mayda Giftlar") yoki emojisiz ("Mayda Giftlar"),
# hatto kichik harflarda kelsa ham ishlaydi.
GIFT_KEYWORDS: tuple[str, ...] = ("gift", "sovg'a", "sovga", "mayda")


def is_gift_menu(message: Message) -> bool:
    """Xabar matni gift bo'limiga tegishlimi (emoji va registrga bog'liq emas)."""
    text = (message.text or "").lower()
    return any(keyword in text for keyword in GIFT_KEYWORDS)


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


def _category_title(category: str) -> str:
    """Toifa sarlavhasi (HTML) — noma'lum toifa uchun emoji/standart matn."""
    return CATEGORY_TITLES.get(category, CATEGORY_EMOJI.get(category, "🛍"))


def _category_text(category: str, products) -> str:
    """Tanlangan toifa mahsulotlari ro'yxati (HTML matn)."""
    lines: list[str] = []
    for index, product in enumerate(products, start=1):
        desc_html = ""
        if product.description:
            desc_html = f"\n   <i>{html.escape(product.description)}</i>"
        lines.append(
            f"{index}. <b>{html.escape(product.title)}</b> — "
            f"<b>{product.price_xtr} XTR</b>{desc_html}"
        )
    return (
        f"{_category_title(category)}\n\n" + "\n".join(lines) +
        "\n\nXarid qilish uchun tugmani bosing 👇"
    )


async def _edit_or_send(
    callback: CallbackQuery, text: str, reply_markup=None
) -> None:
    """
    Joriy inline xabarni `text` bilan almashtiradi.

    Xabar eski/o'chirilgan bo'lsa (tahrirlab bo'lmaydi yoki InaccessibleMessage)
    — chatga yangi xabar yuboriladi: foydalanuvchi baribir javob oladi.
    """
    message = callback.message
    edit = getattr(message, "edit_text", None)
    if edit is not None:
        try:
            await edit(text, parse_mode="HTML", reply_markup=reply_markup)
            return
        except TelegramBadRequest:
            pass

    chat_id = callback.from_user.id
    chat = getattr(message, "chat", None)
    if chat is not None and getattr(chat, "id", None):
        chat_id = chat.id

    await callback.bot.send_message(
        chat_id, text, parse_mode="HTML", reply_markup=reply_markup
    )


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
@router.message(F.text == BTN_SHOP)
async def shop_menu(message: Message) -> None:
    """🛍 Do'kon — kirish sahifasi va toifalar menyusi."""
    await message.answer(
        SHOP_WELCOME_TEXT,
        parse_mode="HTML",
        reply_markup=get_shop_categories_kb(),
    )
    logger.info("Do'kon ochildi: user=%s", message.from_user.id)


@router.message(is_gift_menu)
async def open_gifts(message: Message) -> None:
    """
    🎁 Mayda Giftlar — reply menyu tugmasi.

    Tugma matni emoji bilan ("🎁 Mayda Giftlar") yoki emojisiz
    ("Mayda Giftlar", "mayda sovg'alar") kelsa ham gift kategoriyasi
    ochiladi (`is_gift_menu` filtri).
    """
    products = await ProductRepository.get_products_by_category("gift")

    if not products:
        await message.answer(
            EMPTY_CATEGORY_TEXT.format(category_title=_category_title("gift")),
            parse_mode="HTML",
            reply_markup=get_shop_categories_kb(),
        )
        return

    await message.answer(
        _category_text("gift", products),
        parse_mode="HTML",
        reply_markup=get_products_list_kb(products, "gift"),
    )
    logger.info("Gift bo'limi ochildi: user=%s", message.from_user.id)


# =============================================================
# 2. TOIFA — MAHSULOTLAR RO'YXATI
# =============================================================
@router.callback_query(F.data.startswith("cat_"))
async def shop_category(callback: CallbackQuery) -> None:
    """Tanlangan toifadagi faol mahsulotlar (nom, narx, tavsif)."""
    category = (callback.data or "")[len("cat_"):].strip()
    if category not in PRODUCT_CATEGORIES:
        await callback.answer(ALERT_CATEGORY_NOT_FOUND, show_alert=True)
        return

    # Spinner DARHOL yopiladi — DB so'rovi tugmasini kutdirmaydi
    await callback.answer()

    products = await ProductRepository.get_products_by_category(category)

    if not products:
        await callback.message.answer(
            EMPTY_CATEGORY_TEXT.format(category_title=_category_title(category)),
            parse_mode="HTML",
            reply_markup=get_shop_categories_kb(),
        )
        return

    await callback.message.answer(
        _category_text(category, products),
        parse_mode="HTML",
        reply_markup=get_products_list_kb(products, category),
    )
    logger.info(
        "Toifa ko'rsatildi: user=%s category=%s products=%s",
        callback.from_user.id, category, len(products),
    )


# =============================================================
# 3. MAHSULOT — TASHILOTLARI VA USUL TANLASH
# =============================================================
@router.callback_query(F.data.startswith("buy_prod_"))
async def buy_product(callback: CallbackQuery) -> None:
    """Mahsulot tafsilotlari + to'lov usulini tanlash taklifi."""
    product_id = _parse_product_id(callback.data or "", "buy_prod_")
    if product_id is None:
        await callback.answer(ALERT_PRODUCT_NOT_FOUND, show_alert=True)
        return

    # Spinner DARHOL yopiladi (DB so'rovidan oldin)
    await callback.answer()

    product = await ProductRepository.get_product_by_id(product_id)
    if product is None or not product.is_active:
        await callback.message.answer(ALERT_PRODUCT_NOT_FOUND)
        return

    emoji = CATEGORY_EMOJI.get(product.category, "🛍")
    await callback.message.answer(
        PRODUCT_DETAIL_TEXT.format(
            emoji=emoji,
            title=html.escape(product.title),
            description=html.escape(
                product.description or "Tavsif kiritilmagan"
            ),
            price=product.price_xtr,
        ),
        parse_mode="HTML",
        reply_markup=get_payment_methods_kb(product.id),
    )


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

    # Spinner DARHOL yopiladi — ortdagi DB/Telegram API so'rovlari kutdirilmaydi
    await callback.answer()

    product = await ProductRepository.get_product_by_id(product_id)
    if product is None or not product.is_active:
        await callback.message.answer(ALERT_PRODUCT_NOT_FOUND)
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
        await callback.message.answer(ALERT_PURCHASE_FAILED)
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
        await callback.message.answer(ALERT_INVOICE_FAILED)
        return

    logger.info(
        "XTR invoys yuborildi: user=%s order=%s product=%s amount=%s",
        user_id, order.id, product.id, product.price_xtr,
    )


@router.pre_checkout_query()
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


@router.message(F.successful_payment)
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
    await _notify_admins(
        message.bot, message.from_user, product_name, total_amount
    )
    logger.info(
        "XTR to'lov qabul qilindi: user=%s order=%s charge=%s amount=%s",
        message.from_user.id, order.id, charge_id, total_amount,
    )


# =============================================================
# 5. BOT BALANSI ORQALI XARID
# =============================================================
@router.callback_query(F.data.startswith(CB_PAY_BALANCE_PREFIX))
async def pay_with_balance(callback: CallbackQuery) -> None:
    """
    Bot balansidan xarid: balans tekshiriladi, yetarli bo'lsa
    atomik ravishda balansdan ayriladi, `transactions`ga log yoziladi
    va buyurtma darhol `paid` qilinadi.
    """
    product_id = _parse_product_id(callback.data or "", CB_PAY_BALANCE_PREFIX)
    if product_id is None:
        await callback.answer(ALERT_PRODUCT_NOT_FOUND, show_alert=True)
        return

    # Spinner DARHOL yopiladi (DB so'rovlaridan oldin)
    await callback.answer()

    product = await ProductRepository.get_product_by_id(product_id)
    if product is None or not product.is_active:
        await callback.message.answer(ALERT_PRODUCT_NOT_FOUND)
        return

    user_id = callback.from_user.id

    # Do'stlar orqali to'plangan balans tekshiriladi
    user = await UserRepository.get_user(user_id)
    if user is None:
        await callback.message.answer(
            "❌ Profilingiz topilmadi! /start buyrug'ini bosing."
        )
        return

    if user.stars_balance < product.price_xtr:
        await callback.message.answer(ALERT_NOT_ENOUGH_BALANCE)
        return

    # Atomik xarid (balans - , audit, order=pending -> paid)
    try:
        order = await OrderRepository.create_balance_order(user_id, product.id)
    except InsufficientBalanceError:
        # Parallel sarf — balans shu oraliqda kamaygan bo'lishi mumkin
        await callback.message.answer(ALERT_NOT_ENOUGH_BALANCE)
        return
    except RepositoryError:
        logger.exception(
            "Balans xaridi amalga oshmadi: user=%s product=%s",
            user_id, product.id,
        )
        await callback.message.answer(ALERT_PURCHASE_FAILED)
        return

    new_balance = user.stars_balance - product.price_xtr
    await callback.message.answer(
        BALANCE_RECEIPT_TEXT.format(
            order_id=order.id,
            product_name=html.escape(product.title),
            price=product.price_xtr,
            new_balance=new_balance,
        ),
        parse_mode="HTML",
    )
    logger.info(
        "Balans orqali xarid: user=%s order=%s product=%s -%sXTR",
        user_id, order.id, product.id, product.price_xtr,
    )


# =============================================================
# 6. NAVIGATSIYA — toifalarga qaytish / bosh menyu
# =============================================================
@router.callback_query(F.data == CB_SHOP_CATEGORIES)
async def back_to_categories(callback: CallbackQuery) -> None:
    """[ ◀️ Toifalarga qaytish / Orqaga ] — toifalar menyusini qaytaradi."""
    await callback.answer()
    await _edit_or_send(
        callback, SHOP_WELCOME_TEXT, get_shop_categories_kb()
    )


@router.callback_query(F.data == CB_BACK_TO_MENU)
async def back_to_main_menu(callback: CallbackQuery) -> None:
    """[ ◀️ Bosh menyu ] — inline xabarni almashtirib, asosiy reply menyuni beradi."""
    await callback.answer()
    await _edit_or_send(
        callback,
        "🏠 Bosh menyu.",
        get_main_menu_kb(is_admin=callback.from_user.id in settings.ADMIN_IDS),
    )
