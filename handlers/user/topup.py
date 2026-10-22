"""
handlers/user/topup.py
======================
Karta (UZCARD / HUMO) orqali balans to'ldirish oqimi.

Oqim:
 1. "💳 Balans to'ldirish" (reply tugma) yoki `topup_start` callback:
      - admin kiritgan karta rekvizitlari tekshiriladi
        (`payment_card_number`, `payment_card_holder`);
      - foydalanuvchidan Stars miqdori so'raladi
        (`TopupStates.waiting_for_amount`).
 2. Miqdor tekshiriladi (10..1 000 000) va karta raqami ko'rsatiladi;
    foydalanuvchidan to'lov cheki (rasm) so'raladi
    (`TopupStates.waiting_for_receipt`).
 3. Chek kelganda `DepositRepository.create_deposit` orqali `pending`
    so'rov yaratiladi va **barcha adminlarga** rasm + [✅ Tasdiqlash] /
    [❌ Rad etish] tugmalari bilan yuboriladi.
 4. Admin tasdiqlasa — balans avtomatik qo'shiladi
    (`handlers/admin/deposits.py`).

Xavfsizlik:
 - Balans faqat admin tasdig'idan keyin, atomik (`FOR UPDATE`) qo'shiladi.
 - Har bir so'rov bazada saqlanadi — ikki marta tasdiqlab bo'lmaydi.
 - Chek `file_id` sifatida saqlanadi (keyin tekshirish uchun).
"""

from __future__ import annotations

import html
import logging
from typing import Any

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config.settings import settings
from database.queries import (
    SETTING_CARD_HOLDER,
    SETTING_CARD_NUMBER,
    DepositRepository,
    RepositoryError,
    SettingsRepository,
    UserRepository,
)
from keyboards.inline import (
    CB_TOPUP_CANCEL,
    CB_TOPUP_START,
    get_deposit_decision_kb,
    get_topup_cancel_kb,
)
from services.button_registry import ButtonText
from states.topup_states import TopupStates

logger = logging.getLogger(__name__)

router = Router(name="user_topup")

# To'ldirish chegaralari (Stars)
MIN_TOPUP_STARS = 10
MAX_TOPUP_STARS = 1_000_000

# Standart ogohlantirishlar
NO_CARD_TEXT = (
    "⚠️ <b>Karta rekvizitlari hali kiritilmagan.</b>\n\n"
    "Hozircha balansni karta orqali to'ldirib bo'lmaydi. "
    "Administrator bilan bog'laning."
)


def _format_card_number(number: str) -> str:
    """Karta raqamini 4 tadan guruhlab ko'rsatadi (8600 1234 5678 9012)."""
    digits = "".join(ch for ch in number if ch.isdigit())
    groups = [digits[index:index + 4] for index in range(0, len(digits), 4)]
    return " ".join(groups) if groups else number


async def _card_details() -> tuple[str, str]:
    """Admin kiritgan karta raqami va egasini qaytaradi (bo'sh bo'lishi mumkin)."""
    number = await SettingsRepository.get_setting(SETTING_CARD_NUMBER, "")
    holder = await SettingsRepository.get_setting(SETTING_CARD_HOLDER, "")
    return str(number or "").strip(), str(holder or "").strip()


async def _begin_topup(target: Message, state: FSMContext) -> None:
    """To'ldirish oqimini boshlaydi (miqdor so'raydi)."""
    number, holder = await _card_details()
    if not number:
        await target.answer(NO_CARD_TEXT)
        return

    user_id = target.from_user.id if target.from_user else target.chat.id
    user = await UserRepository.get_user(user_id)
    balance = user.stars_balance if user else 0

    await state.set_state(TopupStates.waiting_for_amount)
    await target.answer(
        "💳 <b>Karta orqali balans to'ldirish</b>\n\n"
        f"Joriy balans: <b>{balance} ⭐️</b>\n"
        f"Minimal to'ldirish: <b>{MIN_TOPUP_STARS} ⭐️</b>\n\n"
        "To'ldirmoqchi bo'lgan Stars miqdorini butun son ko'rinishida "
        "yuboring (masalan: <code>100</code>).\n\n"
        f"Karta: <code>{_format_card_number(number)}</code>"
        + (f"\nEgasi: <b>{html.escape(holder)}</b>" if holder else ""),
        parse_mode="HTML",
        reply_markup=get_topup_cancel_kb(),
    )
    logger.info("Topup boshlandi: user=%s", user_id)


# =============================================================
# 1. BOSHLASH (reply tugma yoki inline callback)
# =============================================================
@router.message(ButtonText("menu_topup"))
async def topup_start_message(message: Message, state: FSMContext) -> None:
    await _begin_topup(message, state)


@router.callback_query(F.data == CB_TOPUP_START)
async def topup_start_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await _begin_topup(callback.message, state)


# =============================================================
# 2. BEKOR QILISH (state handler'laridan OLDIN)
# =============================================================
@router.callback_query(F.data == CB_TOPUP_CANCEL)
async def topup_cancel_callback(callback: CallbackQuery, state: FSMContext) -> None:
    """[ ❌ Yopish ] — holatni tozalaydi."""
    await state.clear()
    await callback.answer("Bekor qilindi")
    await callback.message.answer("❌ Balans to'ldirish bekor qilindi.")


@router.message(TopupStates.waiting_for_amount, F.text == "/cancel")
@router.message(TopupStates.waiting_for_receipt, F.text == "/cancel")
async def topup_cancel_text(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("❌ Balans to'ldirish bekor qilindi.")


# =============================================================
# 3. MIQDOR
# =============================================================
@router.message(TopupStates.waiting_for_amount, F.text)
async def topup_amount(message: Message, state: FSMContext) -> None:
    """Miqdorni tekshiradi va karta rekvizitlari bilan chek so'raydi."""
    raw = (message.text or "").strip().replace(" ", "")
    if not raw.isdigit():
        await message.answer(
            "⚠️ Iltimos, miqdorni faqat butun son ko'rinishida yuboring "
            "(masalan: <code>100</code>).",
            reply_markup=get_topup_cancel_kb(),
        )
        return

    amount = int(raw)
    if amount < MIN_TOPUP_STARS:
        await message.answer(
            f"⚠️ Minimal to'ldirish — <b>{MIN_TOPUP_STARS} ⭐️</b>.",
            reply_markup=get_topup_cancel_kb(),
        )
        return
    if amount > MAX_TOPUP_STARS:
        await message.answer(
            f"⚠️ Maksimal to'ldirish — <b>{MAX_TOPUP_STARS} ⭐️</b>.",
            reply_markup=get_topup_cancel_kb(),
        )
        return

    number, holder = await _card_details()
    if not number:
        await state.clear()
        await message.answer(NO_CARD_TEXT)
        return

    await state.update_data(amount=amount)
    await state.set_state(TopupStates.waiting_for_receipt)

    holder_line = f"\n👤 Egasi: <b>{html.escape(holder)}</b>" if holder else ""
    await message.answer(
        "🧾 <b>To'lov cheki</b>\n\n"
        f"To'ldiriladigan miqdor: <b>{amount} ⭐️</b>\n\n"
        "Quyidagi kartaga o'tkazma qiling:\n"
        f"💳 <code>{_format_card_number(number)}</code>{holder_line}\n\n"
        "To'lovni amalga oshirgach, <b>chekni (skrinshot)</b> shu chatga "
        "rasm qilib yuboring.\n\n"
        "⚠️ Chek yuborilmaguncha balans qo'shilmaydi.",
        parse_mode="HTML",
        reply_markup=get_topup_cancel_kb(),
    )


# =============================================================
# 4. CHEK (rasm) — so'rov yaratish + adminlarga yuborish
# =============================================================
async def _submit_receipt(
    message: Message, state: FSMContext, file_id: str
) -> None:
    """Chekni qabul qiladi, `pending` deposit yaratadi va adminlarga yuboradi."""
    data = await state.get_data()
    amount = int(data.get("amount", 0))

    if amount <= 0:
        await state.clear()
        await message.answer(
            "⚠️ Xatolik yuz berdi. Iltimos, to'ldirishni qaytadan boshlang.",
            reply_markup=get_topup_cancel_kb(),
        )
        return

    user = message.from_user
    try:
        deposit = await DepositRepository.create_deposit(
            user_id=user.id,
            amount=amount,
            receipt_file_id=file_id,
        )
    except (RepositoryError, ValueError) as exc:
        logger.warning("Deposit yaratishda xatolik: %s", exc)
        await message.answer(
            "⚠️ So'rovni saqlab bo'lmadi. Iltimos, keyinroq urinib ko'ring.",
            reply_markup=get_topup_cancel_kb(),
        )
        return

    await state.clear()

    await message.answer(
        "✅ <b>Chek qabul qilindi!</b>\n\n"
        f"├ Miqdor: <b>{amount} ⭐️</b>\n"
        f"├ So'rov: <code>#{deposit.id}</code>\n"
        "└ Holat: 🕓 <i>admin tekshiruvi kutilmoqda</i>\n\n"
        "Tasdiqlangach balansingiz avtomatik to'ldiriladi."
    )

    await _notify_admins_about_deposit(message, deposit, amount)
    logger.info(
        "Deposit so'rovi yuborildi: id=%s user=%s amount=%s",
        deposit.id, user.id, amount,
    )


@router.message(TopupStates.waiting_for_receipt, F.photo)
async def topup_receipt_photo(message: Message, state: FSMContext) -> None:
    """Chek rasmi (photo) qabul qilinadi."""
    if not message.photo:
        return
    await _submit_receipt(message, state, message.photo[-1].file_id)


@router.message(TopupStates.waiting_for_receipt, F.document)
async def topup_receipt_document(message: Message, state: FSMContext) -> None:
    """Chek fayl (rasm hujjati) sifatida yuborilsa ham qabul qilinadi."""
    document = message.document
    if document is None or not (document.mime_type or "").startswith("image/"):
        await message.answer(
            "⚠️ Iltimos, chekni <b>rasm</b> qilib yuboring (skrinshot).",
            reply_markup=get_topup_cancel_kb(),
        )
        return
    await _submit_receipt(message, state, document.file_id)


@router.message(TopupStates.waiting_for_receipt)
async def topup_receipt_invalid(message: Message) -> None:
    """Boshqa kontent turi kelsa — qayta so'raydi."""
    await message.answer(
        "⚠️ Chekni <b>rasm</b> ko'rinishida yuboring (skrinshot).",
        reply_markup=get_topup_cancel_kb(),
    )


# =============================================================
# 5. ADMINLARGA XABAR
# =============================================================
async def _notify_admins_about_deposit(
    message: Message, deposit: Any, amount: int
) -> None:
    """Barcha adminlarga chekni tasdiqlash/rad etish tugmalari bilan yuboradi."""
    user = message.from_user
    if user.username:
        who = f"@{html.escape(user.username)}"
    else:
        who = html.escape(user.first_name or str(user.id))

    caption = (
        "💳 <b>YANGI BALANS TO'LDIRISH SO'ROVI</b>\n\n"
        f"├ Foydalanuvchi: {who} (ID <code>{user.id}</code>)\n"
        f"├ Miqdor: <b>{amount} ⭐️</b>\n"
        f"└ So'rov: <code>#{deposit.id}</code>\n\n"
        "Chekni tekshirib, qaror qabul qiling 👇"
    )
    markup = get_deposit_decision_kb(deposit.id)

    for admin_id in settings.ADMIN_IDS:
        try:
            await message.bot.send_photo(
                chat_id=admin_id,
                photo=deposit.receipt_file_id,
                caption=caption,
                parse_mode="HTML",
                reply_markup=markup,
            )
        except TelegramAPIError as exc:
            logger.warning(
                "Admin %s ga chek yuborilmadi, matn bilan urinamiz: %s",
                admin_id, exc,
            )
            try:
                await message.bot.send_message(
                    admin_id, caption, parse_mode="HTML", reply_markup=markup
                )
            except TelegramAPIError as fallback_exc:
                logger.warning(
                    "Admin %s ga to'ldirish xabari yetmadi: %s",
                    admin_id, fallback_exc,
                )
