"""
handlers/admin/products.py
==========================
Admin panel: `🛍 Mahsulotlar` bo'limi — do'kon katalogini to'liq boshqarish.

Imkoniyatlar:
 - 🎁 **Giftlarni kiritish** — alohida FSM: nom → narx → ta'rif → rasm →
   Telegram `gift_id`; ma'lumot `products` jadvaliga `category='gift'`
   sifatida yaratiladi;
 - 🛍 **Mahsulotlarni boshqarish** — barcha sotiladigan narsalar (gift,
   premium, nft, stars) umumiy ro'yxati yoki toifa bo'yicha: har birini
   tanlab nomini, tavsifini, narxini, rasmini va `gift_id` ni tahrirlash,
   nofaol qilish yoki o'chirish;
 - Toifalar bo'yicha ro'yxat — faol/nofaol holati va narx ko'rinadi;
 - Yangi mahsulot qo'shish (FSM): nom → tavsif → narx → gift_id (gift uchun);
 - Faollashtirish / nofaol qilish (do'konda ko'rinishini boshqarish);
 - O'chirish (buyurtmalar bo'lsa — avtomatik nofaol qilinadi).

`gift_id` — rasmiy Telegram sovg'asining raqamli IDsi. U to'ldirilganda
xarid muvaffaqiyatli yakunlangach `services.gift_delivery` sovg'ani
foydalanuvchiga avtomatik yuboradi (`sendGift`). Rasm (`image_file_id`)
Telegram `file_id` sifatida bazada saqlanadi va do'konda ko'rsatiladi.

Bekor qilish: istalgan bosqichda /cancel
"""

from __future__ import annotations

import html
import logging
from typing import Any

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config.settings import settings
from database.queries import PRODUCT_CATEGORIES, ProductRepository, RepositoryError
from keyboards.builders import (
    CB_ADM_GIFT_ADD,
    CB_ADM_PROD_ADD_PREFIX,
    CB_ADM_PROD_DELETE_OK_PREFIX,
    CB_ADM_PROD_DELETE_PREFIX,
    CB_ADM_PROD_DETAIL_PREFIX,
    CB_ADM_PROD_EDIT_PREFIX,
    CB_ADM_PROD_LIST_PREFIX,
    CB_ADM_PROD_NEW,
    CB_ADM_PROD_TOGGLE_PREFIX,
    CB_ADM_PRODUCTS,
    PRODUCT_CATEGORY_LABELS,
    get_admin_product_list_kb,
    get_admin_products_kb,
    get_product_category_kb,
    get_product_delete_confirm_kb,
    get_product_detail_kb,
)
from states.admin_states import GiftStates, ProductStates

logger = logging.getLogger(__name__)

router = Router(name="admin_products")

# faqat adminlar uchun
router.message.filter(F.from_user.id.in_(settings.ADMIN_IDS))
router.callback_query.filter(F.from_user.id.in_(settings.ADMIN_IDS))

MAX_TITLE_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 400
MAX_PRICE_XTR = 1_000_000

PRODUCTS_MENU_TEXT = (
    "🛍 <b>MAHSULOTLARNI BOSHQARISH</b>\n\n"
    "Quyidagilarni bajarishingiz mumkin:\n"
    "├ 📋 barcha sotiladigan narsalarni bir ro'yxatda ko'rish;\n"
    "├ ✏️ nom / tavsif / narx / rasm / gift_id ni o'zgartirish;\n"
    "├ 🚫 mahsulotni nofaol qilish (do'konda yashirish);\n"
    "└ 🗑 o'chirish yoki ➕ yangi mahsulot qo'shish.\n\n"
    "<i>Faqat faol mahsulotlar xaridorlarga ko'rinadi.</i>"
)

EMPTY_LIST_TEXT = (
    "📭 <b>{label}</b> toifasida hali mahsulot yo'q.\n\n"
    "Pastdagi <b>➕ Yangi mahsulot qo'shish</b> tugmasi orqali qo'shing."
)

PRODUCT_LIST_TEXT = (
    "🛍 <b>{label}</b>\n\n"
    "Jami: <b>{total}</b> ta (faol: <b>{active}</b> ta)\n\n"
    "Mahsulotni tanlab, tahrirlash mumkin."
)

PRODUCT_DETAIL_TEXT = (
    "🛍 <b>MAHSULOT KARTASI</b>\n\n"
    "├ ID: <code>{id}</code>\n"
    "├ Toifa: <b>{label}</b>\n"
    "├ Nomi: <b>{title}</b>\n"
    "├ Narxi: <b>{price} ⭐️</b>\n"
    "├ Holati: {status}\n"
    "├ Tavsif: {description}\n"
    "├ Rasm: {image}\n"
    "└ Gift ID: <code>{gift_id}</code>"
)

ASK_TITLE_TEXT = (
    "✏️ <b>1/4 — Mahsulot nomi</b>\n\n"
    "Nomi yuboring (1–{max_len} belgi).\n"
    "Masalan: <code>100 Stars</code>\n\n"
    "Bekor qilish: /cancel"
)

ASK_DESCRIPTION_TEXT = (
    "📝 <b>2/4 — Tavsif</b>\n\n"
    "Qisqa tavsif yuboring yoki <code>-</code> deb o'tkazib yuboring.\n\n"
    "Bekor qilish: /cancel"
)

ASK_PRICE_TEXT = (
    "💰 <b>3/4 — Narx</b>\n\n"
    "Narxni <b>Telegram Stars (XTR)</b> da butun son bilan yuboring.\n"
    "Masalan: <code>100</code>\n\n"
    "Bekor qilish: /cancel"
)

ASK_GIFT_ID_TEXT = (
    "🎁 <b>4/4 — Telegram Gift ID</b>\n\n"
    "Rasmiy sovg'aning raqamli <code>gift_id</code> sini yuboring — "
    "xariddan keyin sovg'a avtomatik jo'natiladi.\n\n"
    "Hozircha kiritmaslik uchun <code>-</code> yuboring "
    "(keyin kartadan tahrirlash mumkin).\n\n"
    "Bekor qilish: /cancel"
)

ASK_EDIT_TEXT = (
    "✏️ <b>{field}</b>\n\n"
    "{hint}\n\n"
    "Bekor qilish: /cancel"
)

# --- 🎁 Giftlarni kiritish (alohida FSM) ---
GIFT_TITLE_TEXT = (
    "🎁 <b>1/5 — Gift nomi</b>\n\n"
    "Sovg'aning nomini yuboring (1–{max_len} belgi).\n"
    "Masalan: <code>Lucky Emoji</code>\n\n"
    "Bekor qilish: /cancel"
)

GIFT_PRICE_TEXT = (
    "💰 <b>2/5 — Narx</b>\n\n"
    "Narxni <b>Telegram Stars (XTR)</b> da butun son bilan yuboring.\n"
    "Masalan: <code>100</code>\n\n"
    "Bekor qilish: /cancel"
)

GIFT_DESCRIPTION_TEXT = (
    "📝 <b>3/5 — Ta'rif</b>\n\n"
    "Qisqa ta'rif yuboring yoki <code>-</code> deb o'tkazib yuboring.\n\n"
    "Bekor qilish: /cancel"
)

GIFT_IMAGE_TEXT = (
    "🖼 <b>4/5 — Rasm</b>\n\n"
    "Gift rasmini <b>foto sifatida</b> yuboring — do'konda shu rasm "
    "ko'rinadi.\n\n"
    "O'tkazib yotish uchun <code>-</code> yuboring.\n\n"
    "Bekor qilish: /cancel"
)

GIFT_ID_TEXT = (
    "🆔 <b>5/5 — Telegram Gift ID</b>\n\n"
    "Rasmiy sovg'aning raqamli <code>gift_id</code> sini yuboring — "
    "xariddan keyin sovg'a avtomatik jo'natiladi.\n\n"
    "Keyinroq kiritish uchun <code>-</code> yuboring.\n\n"
    "Bekor qilish: /cancel"
)

GIFT_SAVED_TEXT = (
    "✅ <b>Gift bazaga qo'shildi!</b>\n\n"
    "Nom: <b>{title}</b>\nNarx: <b>{price} ⭐️</b>\n"
    "Rasm: {image}\nGift ID: <code>{gift_id}</code>\n\n"
    "Endi u <b>🎁 Mayda Giftlar</b> bo'limida sotiladi."
)

ASK_IMAGE_TEXT = (
    "🖼 <b>Yangi rasm</b>\n\n"
    "Rasmni <b>foto sifatida</b> yuboring yoki <code>-</code> — "
    "rasmini o'chirish uchun.\n\n"
    "Bekor qilish: /cancel"
)

FIELD_TITLES: dict[str, str] = {
    "title": "Yangi nom",
    "description": "Yangi tavsif",
    "price": "Yangi narx (XTR)",
    "gift": "Yangi Telegram Gift ID",
    "image": "Yangi rasm",
}

FIELD_HINTS: dict[str, str] = {
    "title": "Mahsulot nomini yuboring (1–{max_len} belgi).",
    "description": "Tavsifni yuboring yoki <code>-</code> (tozalash uchun).",
    "price": "Narxni butun son bilan yuboring (Stars). Masalan: <code>100</code>",
    "gift": "Raqamli <code>gift_id</code> yuboring yoki <code>-</code> (o'chirish uchun).",
    "image": "Rasmni <b>foto sifatida</b> yuboring yoki <code>-</code> (o'chirish uchun).",
}

CANCELLED_TEXT = "❌ Mahsulot amali bekor qilindi."
INVALID_TITLE = "⚠️ Nom 1–{max_len} belgi bo'lishi kerak. Qayta yuboring."
INVALID_PRICE = (
    "⚠️ Narx 1 dan {max_price} gacha butun son bo'lishi kerak. "
    "Qayta yuboring."
)
INVALID_GIFT = "⚠️ Gift ID faqat raqamlardan iborat bo'lishi kerak. Qayta yuboring."
INVALID_IMAGE = (
    "⚠️ Rasm <b>foto</b> sifatida yuborilishi kerak (rasm emas, matn emas). "
    "Qayta yuboring yoki <code>-</code>."
)


# =============================================================
# Yordamchilar
# =============================================================
def _label(category: str) -> str:
    return PRODUCT_CATEGORY_LABELS.get(category, category)


def _parse_id(data: str, prefix: str) -> int | None:
    """`prefix` dan keyingi qismni butun songa aylantiradi."""
    raw = data[len(prefix):] if data.startswith(prefix) else ""
    return int(raw) if raw.isdigit() else None


def _split_edit_data(data: str) -> tuple[int, str] | None:
    """
    `adm_pedit_<id>_<field>` ni (id, field) ga ajratadi.
    """
    if not data.startswith(CB_ADM_PROD_EDIT_PREFIX):
        return None
    rest = data[len(CB_ADM_PROD_EDIT_PREFIX):]
    product_id_raw, _, field = rest.partition("_")
    if not product_id_raw.isdigit() or field not in FIELD_TITLES:
        return None
    return int(product_id_raw), field


def _mask_gift_id(payload: object) -> str:
    """Mahsulot payload'idan gift_id ni ko'rsatish uchun oladi."""
    if isinstance(payload, dict):
        gift_id = payload.get("gift_id")
        if gift_id:
            return str(gift_id)
        metadata = payload.get("metadata")
        if isinstance(metadata, dict) and metadata.get("gift_id"):
            return str(metadata["gift_id"])
    return "—"


def _detail_text(product: Any) -> str:
    """Mahsulot kartasi matni (HTML)."""
    description = product.description
    return PRODUCT_DETAIL_TEXT.format(
        id=product.id,
        label=_label(product.category),
        title=html.escape(str(product.title)),
        price=product.price_xtr,
        status="✅ Faol" if product.is_active else "🚫 Nofaol",
        description=html.escape(description) if description else "—",
        image="✅ mavjud" if getattr(product, "image_file_id", None) else "yo'q",
        gift_id=html.escape(_mask_gift_id(product.payload)),
    )


async def _show_products_menu(callback: CallbackQuery) -> None:
    """Toifalar ekranini ko'rsatadi (xabarni tahrirlab yoki yangi yuborib)."""
    products = await ProductRepository.get_all_products()
    counts: dict[str, int] = {}
    for product in products:
        counts[product.category] = counts.get(product.category, 0) + 1

    try:
        await callback.message.edit_text(
            PRODUCTS_MENU_TEXT, reply_markup=get_admin_products_kb(counts)
        )
    except TelegramBadRequest:
        await callback.message.answer(
            PRODUCTS_MENU_TEXT, reply_markup=get_admin_products_kb(counts)
        )


async def _show_category(callback: CallbackQuery, category: str) -> None:
    """Tanlangan toifa (yoki `all` — barchasi) mahsulotlarini ko'rsatadi."""
    all_products = await ProductRepository.get_all_products()
    if category == "all":
        products = all_products
        label = "📋 Barcha mahsulotlar"
    else:
        products = [p for p in all_products if p.category == category]
        label = _label(category)
    markup = get_admin_product_list_kb(products, category)

    if products:
        text = PRODUCT_LIST_TEXT.format(
            label=label,
            total=len(products),
            active=sum(1 for product in products if product.is_active),
        )
    else:
        text = EMPTY_LIST_TEXT.format(label=label)

    try:
        await callback.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest:
        await callback.message.answer(text, reply_markup=markup)


async def _show_detail(callback: CallbackQuery, product_id: int) -> None:
    """Mahsulot kartasini ko'rsatadi."""
    product = await ProductRepository.get_product_by_id(product_id)
    if product is None:
        await callback.answer("❌ Mahsulot topilmadi", show_alert=True)
        return
    try:
        await callback.message.edit_text(
            _detail_text(product), reply_markup=get_product_detail_kb(product)
        )
    except TelegramBadRequest:
        await callback.message.answer(
            _detail_text(product), reply_markup=get_product_detail_kb(product)
        )


# =============================================================
# 0. BEKOR QILISH
# =============================================================
@router.message(
    StateFilter(
        ProductStates.waiting_for_title,
        ProductStates.waiting_for_description,
        ProductStates.waiting_for_price,
        ProductStates.waiting_for_gift_id,
        ProductStates.waiting_for_edit_value,
        GiftStates.waiting_for_title,
        GiftStates.waiting_for_price,
        GiftStates.waiting_for_description,
        GiftStates.waiting_for_image,
        GiftStates.waiting_for_gift_id,
    ),
    F.text == "/cancel",
)
async def product_cancel(message: Message, state: FSMContext) -> None:
    """Har qanday bosqichda `/cancel` — formani yopadi."""
    await state.clear()
    await message.answer(CANCELLED_TEXT, reply_markup=get_admin_products_kb())


# =============================================================
# 1. TOIFALAR VA RO'YXAT
# =============================================================
@router.callback_query(F.data == CB_ADM_PRODUCTS)
async def products_menu(callback: CallbackQuery, state: FSMContext) -> None:
    """[ 🛍 Mahsulotlar ] — toifalar ro'yxati."""
    await state.clear()
    await callback.answer()
    await _show_products_menu(callback)


@router.callback_query(F.data.startswith(CB_ADM_PROD_LIST_PREFIX))
async def products_by_category(callback: CallbackQuery) -> None:
    """Toifa (yoki `all`) mahsulotlari ro'yxati."""
    category = callback.data[len(CB_ADM_PROD_LIST_PREFIX):]
    if category != "all" and category not in PRODUCT_CATEGORIES:
        await callback.answer("❌ Toifa topilmadi", show_alert=True)
        return
    await callback.answer()
    await _show_category(callback, category)


@router.callback_query(F.data.startswith(CB_ADM_PROD_DETAIL_PREFIX))
async def product_detail(callback: CallbackQuery) -> None:
    """Mahsulot kartasi."""
    product_id = _parse_id(callback.data, CB_ADM_PROD_DETAIL_PREFIX)
    if product_id is None:
        await callback.answer("❌ Noto'g'ri mahsulot ID", show_alert=True)
        return
    await callback.answer()
    await _show_detail(callback, product_id)


# =============================================================
# 2. YANGI MAHSULOT QO'SHISH (FSM)
# =============================================================
@router.callback_query(F.data == CB_ADM_PROD_NEW)
async def product_new(callback: CallbackQuery) -> None:
    """[ ➕ Yangi mahsulot qo'shish ] — toifani tanlash."""
    await callback.answer()
    await callback.message.answer(
        "➕ <b>YANGI MAHSULOT</b>\n\nQaysi toifaga qo'shamiz?",
        reply_markup=get_product_category_kb(),
    )


@router.callback_query(F.data.startswith(CB_ADM_PROD_ADD_PREFIX))
async def product_add_start(callback: CallbackQuery, state: FSMContext) -> None:
    """Toifa tanlandi — nom so'raladi."""
    category = callback.data[len(CB_ADM_PROD_ADD_PREFIX):]
    if category not in PRODUCT_CATEGORIES:
        await callback.answer("❌ Toifa topilmadi", show_alert=True)
        return

    await state.set_state(ProductStates.waiting_for_title)
    await state.update_data(category=category, description=None)
    await callback.answer()
    await callback.message.answer(
        f"🛍 Toifa: <b>{_label(category)}</b>\n\n"
        + ASK_TITLE_TEXT.format(max_len=MAX_TITLE_LENGTH)
    )


@router.message(ProductStates.waiting_for_title, F.text)
async def product_title(message: Message, state: FSMContext) -> None:
    """1-qadam: nomni qabul qiladi."""
    title = (message.text or "").strip()
    if not title or len(title) > MAX_TITLE_LENGTH:
        await message.answer(INVALID_TITLE.format(max_len=MAX_TITLE_LENGTH))
        return

    await state.update_data(title=title)
    await state.set_state(ProductStates.waiting_for_description)
    await message.answer(ASK_DESCRIPTION_TEXT)


@router.message(ProductStates.waiting_for_description, F.text)
async def product_description(message: Message, state: FSMContext) -> None:
    """2-qadam: tavsifni qabul qiladi (`-` — yo'q)."""
    description = (message.text or "").strip()
    if len(description) > MAX_DESCRIPTION_LENGTH:
        await message.answer(
            f"⚠️ Tavsif {MAX_DESCRIPTION_LENGTH} belgidan oshmasligi kerak. "
            "Qayta yuboring yoki <code>-</code>."
        )
        return

    if description in ("-", "—"):
        description = ""

    await state.update_data(description=description or None)
    await state.set_state(ProductStates.waiting_for_price)
    await message.answer(ASK_PRICE_TEXT)


@router.message(ProductStates.waiting_for_price, F.text)
async def product_price(message: Message, state: FSMContext) -> None:
    """3-qadam: narxni qabul qiladi; gift uchun gift_id so'raladi."""
    raw = (message.text or "").strip().replace(" ", "").replace(",", "")
    if not raw.isdigit() or not 1 <= int(raw) <= MAX_PRICE_XTR:
        await message.answer(
            INVALID_PRICE.format(max_price=MAX_PRICE_XTR)
        )
        return

    await state.update_data(price_xtr=int(raw))
    data = await state.get_data()

    if data.get("category") == "gift":
        await state.set_state(ProductStates.waiting_for_gift_id)
        await message.answer(ASK_GIFT_ID_TEXT)
        return

    await _create_product(message, state)


@router.message(ProductStates.waiting_for_gift_id, F.text)
async def product_gift_id(message: Message, state: FSMContext) -> None:
    """4-qadam (gift): `gift_id` yoki `-`."""
    raw = (message.text or "").strip()
    if raw in ("-", "—"):
        gift_id = None
    elif raw.isdigit():
        gift_id = raw
    else:
        await message.answer(INVALID_GIFT)
        return

    await state.update_data(gift_id=gift_id)
    await _create_product(message, state)


async def _create_product(message: Message, state: FSMContext) -> None:
    """To'plangan ma'lumotlar asosida mahsulot yaratadi."""
    data = await state.get_data()
    category = str(data.get("category", ""))
    gift_id = data.get("gift_id")
    payload = {"gift_id": gift_id} if gift_id else None

    try:
        product = await ProductRepository.create_product(
            category=category,
            title=str(data.get("title", "")),
            price_xtr=int(data.get("price_xtr", 0)),
            description=data.get("description"),
            payload=payload,
            image_file_id=data.get("image"),
        )
    except (ValueError, RepositoryError) as exc:
        logger.error("Mahsulot qo'shilmadi (admin=%s): %s", message.from_user.id, exc)
        await state.clear()
        await message.answer(
            "⚠️ Mahsulot qo'shilmadi. Ma'lumotlarni tekshirib, qayta urinib ko'ring."
        )
        return

    await state.clear()
    logger.info(
        "Admin mahsulot qo'shdi: admin=%s product=%s category=%s price=%s gift=%s",
        message.from_user.id, product.id, category, product.price_xtr, gift_id,
    )

    await message.answer(f"✅ Mahsulot qo'shildi: <b>{html.escape(product.title)}</b>")
    await message.answer(
        _detail_text(product), reply_markup=get_product_detail_kb(product)
    )


# =============================================================
# 2.1. 🎁 GIFTLARNI KIRITISH (alohida FSM)
# =============================================================
@router.callback_query(F.data == CB_ADM_GIFT_ADD)
async def gift_add_start(callback: CallbackQuery, state: FSMContext) -> None:
    """[ 🎁 Giftlarni kiritish ] — nom, narx, ta'rif, rasm, gift_id so'raydi."""
    await state.set_data({})
    await state.set_state(GiftStates.waiting_for_title)
    await callback.answer()
    await callback.message.answer(GIFT_TITLE_TEXT.format(max_len=MAX_TITLE_LENGTH))


@router.message(GiftStates.waiting_for_title, F.text)
async def gift_title(message: Message, state: FSMContext) -> None:
    """1/5 — gift nomi."""
    title = (message.text or "").strip()
    if not title or len(title) > MAX_TITLE_LENGTH:
        await message.answer(INVALID_TITLE.format(max_len=MAX_TITLE_LENGTH))
        return

    await state.update_data(
        category="gift", title=title, description=None, image=None, gift_id=None
    )
    await state.set_state(GiftStates.waiting_for_price)
    await message.answer(GIFT_PRICE_TEXT)


@router.message(GiftStates.waiting_for_price, F.text)
async def gift_price(message: Message, state: FSMContext) -> None:
    """2/5 — narx (XTR)."""
    raw = (message.text or "").strip().replace(" ", "").replace(",", "")
    if not raw.isdigit() or not 1 <= int(raw) <= MAX_PRICE_XTR:
        await message.answer(INVALID_PRICE.format(max_price=MAX_PRICE_XTR))
        return

    await state.update_data(price_xtr=int(raw))
    await state.set_state(GiftStates.waiting_for_description)
    await message.answer(GIFT_DESCRIPTION_TEXT)


@router.message(GiftStates.waiting_for_description, F.text)
async def gift_description(message: Message, state: FSMContext) -> None:
    """3/5 — ta'rif (`-` — o'tkazib yuborish)."""
    description = (message.text or "").strip()
    if len(description) > MAX_DESCRIPTION_LENGTH:
        await message.answer(
            f"⚠️ Ta'rif {MAX_DESCRIPTION_LENGTH} belgidan oshmasligi kerak. "
            "Qayta yuboring yoki <code>-</code>."
        )
        return

    if description in ("-", "—"):
        description = ""

    await state.update_data(description=description or None)
    await state.set_state(GiftStates.waiting_for_image)
    await message.answer(GIFT_IMAGE_TEXT)


@router.message(GiftStates.waiting_for_image, F.photo)
async def gift_image(message: Message, state: FSMContext) -> None:
    """4/5 — rasm (eng yuqori sifatdagi nusxa `file_id` sifatida saqlanadi)."""
    file_id = message.photo[-1].file_id
    await state.update_data(image=file_id)
    await state.set_state(GiftStates.waiting_for_gift_id)
    await message.answer(GIFT_ID_TEXT)


@router.message(GiftStates.waiting_for_image, F.text)
async def gift_image_skip(message: Message, state: FSMContext) -> None:
    """4/5 — rasmdan voz kechish (`-`)."""
    raw = (message.text or "").strip()
    if raw not in ("-", "—", "yo'q", "yoq"):
        await message.answer(INVALID_IMAGE)
        return

    await state.update_data(image=None)
    await state.set_state(GiftStates.waiting_for_gift_id)
    await message.answer(GIFT_ID_TEXT)


@router.message(GiftStates.waiting_for_gift_id, F.text)
async def gift_gift_id(message: Message, state: FSMContext) -> None:
    """5/5 — Telegram `gift_id` yoki `-`, so'ng bazaga yozish."""
    raw = (message.text or "").strip()
    if raw in ("-", "—"):
        gift_id = None
    elif raw.isdigit():
        gift_id = raw
    else:
        await message.answer(INVALID_GIFT)
        return

    await state.update_data(gift_id=gift_id)
    await _create_gift(message, state)


async def _create_gift(message: Message, state: FSMContext) -> None:
    """To'plangan gift ma'lumotlarini `products` jadvaliga yozadi."""
    data = await state.get_data()
    gift_id = data.get("gift_id")
    image = data.get("image")

    try:
        product = await ProductRepository.create_product(
            category="gift",
            title=str(data.get("title", "")),
            price_xtr=int(data.get("price_xtr", 0)),
            description=data.get("description"),
            payload={"gift_id": gift_id} if gift_id else None,
            image_file_id=image,
        )
    except (ValueError, RepositoryError) as exc:
        logger.error("Gift qo'shilmadi (admin=%s): %s", message.from_user.id, exc)
        await state.clear()
        await message.answer(
            "⚠️ Gift bazaga qo'shilmadi. Ma'lumotlarni tekshirib, "
            "qayta urinib ko'ring."
        )
        return

    await state.clear()
    logger.info(
        "Admin gift qo'shdi: admin=%s product=%s title=%r price=%s img=%s gift=%s",
        message.from_user.id, product.id, product.title, product.price_xtr,
        bool(image), gift_id,
    )

    await message.answer(
        GIFT_SAVED_TEXT.format(
            title=html.escape(product.title),
            price=product.price_xtr,
            image="✅ yuklandi" if image else "yo'q",
            gift_id=gift_id or "kiritilmagan",
        )
    )
    await message.answer(
        _detail_text(product), reply_markup=get_product_detail_kb(product)
    )


# =============================================================
# 3. TAHRIRLASH
# =============================================================
@router.callback_query(F.data.startswith(CB_ADM_PROD_EDIT_PREFIX))
async def product_edit_start(callback: CallbackQuery, state: FSMContext) -> None:
    """Tahrirlanadigan maydonni tanlash — yangi qiymat so'raladi."""
    parsed = _split_edit_data(callback.data)
    if parsed is None:
        await callback.answer("❌ Noto'g'ri amal", show_alert=True)
        return

    product_id, field = parsed
    product = await ProductRepository.get_product_by_id(product_id)
    if product is None:
        await callback.answer("❌ Mahsulot topilmadi", show_alert=True)
        return

    if field == "gift" and product.category != "gift":
        await callback.answer(
            "⚠️ Gift ID faqat 'Mayda Giftlar' toifasi uchun", show_alert=True
        )
        return

    await state.set_state(ProductStates.waiting_for_edit_value)
    await state.update_data(product_id=product_id, field=field)
    await callback.answer()

    hint = FIELD_HINTS[field].format(max_len=MAX_TITLE_LENGTH)
    if field == "gift":
        hint += f"\n\nHozirgi: <code>{html.escape(_mask_gift_id(product.payload))}</code>"
    if field == "image":
        current = "✅ mavjud" if product.image_file_id else "yo'q"
        await callback.message.answer(
            f"🖼 <b>Yangi rasm</b> (hozirgi: {current})\n\n"
            + FIELD_HINTS["image"]
            + "\n\nBekor qilish: /cancel"
        )
        return
    await callback.message.answer(
        ASK_EDIT_TEXT.format(field=FIELD_TITLES[field], hint=hint)
    )


@router.message(ProductStates.waiting_for_edit_value, F.photo)
async def product_edit_image(message: Message, state: FSMContext) -> None:
    """Tahrirlash bosqichida yuborilgan rasmni saqlaydi."""
    data = await state.get_data()
    product_id = int(data.get("product_id", 0))
    if str(data.get("field", "")) != "image":
        await message.answer(
            "⚠️ Bu bosqichda rasm kutilmaydi. Faqat so'ralgan qiymatni "
            "yuboring yoki /cancel."
        )
        return

    product = await ProductRepository.get_product_by_id(product_id)
    if product is None:
        await state.clear()
        await message.answer("❌ Mahsulot topilmadi (o'chirilgan bo'lishi mumkin).")
        return

    file_id = message.photo[-1].file_id
    try:
        updated = await ProductRepository.update_product(
            product_id, image_file_id=file_id
        )
    except (ValueError, RepositoryError) as exc:
        logger.error("Rasm saqlanmadi (admin=%s): %s", message.from_user.id, exc)
        await message.answer("⚠️ Rasm saqlanmadi. Qayta urinib ko'ring.")
        return

    await state.clear()
    logger.info(
        "Admin mahsulot rasm yangiladi: admin=%s product=%s",
        message.from_user.id, product_id,
    )
    await message.answer("✅ Rasm saqlandi.")
    await message.answer(
        _detail_text(updated), reply_markup=get_product_detail_kb(updated)
    )


@router.message(ProductStates.waiting_for_edit_value, F.text)
async def product_edit_apply(message: Message, state: FSMContext) -> None:
    """Yangi qiymatni tekshirib, bazaga yozadi."""
    data = await state.get_data()
    product_id = int(data.get("product_id", 0))
    field = str(data.get("field", ""))
    raw = (message.text or "").strip()

    product = await ProductRepository.get_product_by_id(product_id)
    if product is None:
        await state.clear()
        await message.answer("❌ Mahsulot topilmadi (o'chirilgan bo'lishi mumkin).")
        return

    fields: dict[str, object] = {}
    if field == "title":
        if not raw or len(raw) > MAX_TITLE_LENGTH:
            await message.answer(INVALID_TITLE.format(max_len=MAX_TITLE_LENGTH))
            return
        fields["title"] = raw
    elif field == "description":
        fields["description"] = None if raw in ("-", "—", "") else raw
    elif field == "price":
        cleaned = raw.replace(" ", "").replace(",", "")
        if not cleaned.isdigit() or not 1 <= int(cleaned) <= MAX_PRICE_XTR:
            await message.answer(INVALID_PRICE.format(max_price=MAX_PRICE_XTR))
            return
        fields["price_xtr"] = int(cleaned)
    elif field == "gift":
        payload = dict(product.payload or {})
        if raw in ("-", "—"):
            payload.pop("gift_id", None)
            metadata = payload.get("metadata")
            if isinstance(metadata, dict):
                metadata.pop("gift_id", None)
                if not metadata:
                    payload.pop("metadata", None)
        elif raw.isdigit():
            payload["gift_id"] = raw
        else:
            await message.answer(INVALID_GIFT)
            return
        fields["payload"] = payload or None
    elif field == "image":
        if raw not in ("-", "—", "yo'q"):
            await message.answer(INVALID_IMAGE)
            return
        fields["image_file_id"] = None
    else:
        await state.clear()
        await message.answer("❌ Noma'lum maydon.")
        return

    try:
        updated = await ProductRepository.update_product(product_id, **fields)
    except (ValueError, RepositoryError) as exc:
        logger.error("Mahsulot yangilanmadi (admin=%s): %s", message.from_user.id, exc)
        await message.answer("⚠️ O'zgarish saqlanmadi. Qayta urinib ko'ring.")
        return

    await state.clear()
    logger.info(
        "Admin mahsulotni tahrirladi: admin=%s product=%s fields=%s",
        message.from_user.id, product_id, list(fields),
    )
    await message.answer("✅ O'zgarish saqlandi.")
    await message.answer(
        _detail_text(updated), reply_markup=get_product_detail_kb(updated)
    )


# =============================================================
# 4. FAOLLIK VA O'CHIRISH
# =============================================================
@router.callback_query(F.data.startswith(CB_ADM_PROD_TOGGLE_PREFIX))
async def product_toggle(callback: CallbackQuery) -> None:
    """Mahsulotni faollashtirish / nofaol qilish."""
    product_id = _parse_id(callback.data, CB_ADM_PROD_TOGGLE_PREFIX)
    if product_id is None:
        await callback.answer("❌ Noto'g'ri mahsulot ID", show_alert=True)
        return

    product = await ProductRepository.get_product_by_id(product_id)
    if product is None:
        await callback.answer("❌ Mahsulot topilmadi", show_alert=True)
        return

    updated = await ProductRepository.update_product(
        product_id, is_active=not product.is_active
    )
    await callback.answer(
        "✅ Faollashtirildi" if updated.is_active else "🚫 Nofaol qilindi"
    )
    logger.info(
        "Admin mahsulot holatini o'zgartirdi: admin=%s product=%s active=%s",
        callback.from_user.id, product_id, updated.is_active,
    )
    await _show_detail(callback, product_id)


@router.callback_query(F.data.startswith(CB_ADM_PROD_DELETE_OK_PREFIX))
async def product_delete_apply(callback: CallbackQuery) -> None:
    """O'chirishni tasdiqlash."""
    product_id = _parse_id(callback.data, CB_ADM_PROD_DELETE_OK_PREFIX)
    if product_id is None:
        await callback.answer("❌ Noto'g'ri mahsulot ID", show_alert=True)
        return

    product = await ProductRepository.get_product_by_id(product_id)
    if product is None:
        await callback.answer("❌ Mahsulot topilmadi", show_alert=True)
        return

    category = product.category
    deleted = await ProductRepository.delete_product(product_id)
    logger.info(
        "Admin mahsulotni o'chirdi: admin=%s product=%s hard_delete=%s",
        callback.from_user.id, product_id, deleted,
    )

    if deleted:
        await callback.answer("🗑 Mahsulot o'chirildi", show_alert=True)
    else:
        await callback.answer(
            "⚠️ Bu mahsulot bo'yicha buyurtmalar bor — butunlay o'chirib "
            "bo'lmaydi. Mahsulot nofaol qilindi va do'konda ko'rinmaydi.",
            show_alert=True,
        )
    await _show_category(callback, category)


@router.callback_query(F.data.startswith(CB_ADM_PROD_DELETE_PREFIX))
async def product_delete_confirm(callback: CallbackQuery) -> None:
    """O'chirishdan oldin tasdiqlash so'raydi."""
    product_id = _parse_id(callback.data, CB_ADM_PROD_DELETE_PREFIX)
    if product_id is None:
        await callback.answer("❌ Noto'g'ri mahsulot ID", show_alert=True)
        return

    product = await ProductRepository.get_product_by_id(product_id)
    if product is None:
        await callback.answer("❌ Mahsulot topilmadi", show_alert=True)
        return

    await callback.answer()
    text = (
        "🗑 <b>O'CHIRISHNI TASDIQLANG</b>\n\n"
        f"Mahsulot: <b>{html.escape(product.title)}</b> "
        f"({product.price_xtr}⭐️)\n\n"
        "Agar mahsulot bo'yicha buyurtmalar bo'lsa, u butunlay o'chirilmaydi — "
        "faqat nofaol qilinadi."
    )
    try:
        await callback.message.edit_text(
            text, reply_markup=get_product_delete_confirm_kb(product)
        )
    except TelegramBadRequest:
        await callback.message.answer(
            text, reply_markup=get_product_delete_confirm_kb(product)
        )
