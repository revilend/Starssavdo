"""
handlers/user/topup.py
======================
Balans to'ldirish — 2 usul bilan.

Oqim:
 1. "💳 Balans to'ldirish" (reply tugma) yoki `topup_start` callback:
      usullar menyusi (`TopupStates.waiting_for_method`):
        a) ⚡️ **Avtomatik to'lov** — Telegram Stars (XTR) invoysi;
        b) 💳 **Uzcard / Humo kartaga o'tkazish** — unikal summa + chek.
 2. ⚡️ Avtomatik to'lov (`topup_auto`):
      foydalanuvchi Stars miqdorini kiritadi -> `pending` deposit yaratiladi
      -> `bot.send_invoice(currency="XTR")` yuboriladi.
      `successful_payment` kelganda balans **admin aralashuvisiz** qo'shiladi
      (`DepositRepository.confirm_deposit`, idempotent).
 3. 💳 Karta (`topup_card`): miqdor so'raladi (10..1 000 000), so'ng
    `stars * SOM_PER_STAR + 1..99` unikal summa va karta raqami ko'rsatiladi
    (`TopupStates.waiting_for_receipt`).
      - `CARD_LOG_CHAT_ID` jurnaliga aynan shu summa tushsa — balans
        avtomatik qo'shiladi (`handlers/user/card_auto.py`);
      - foydalanuvchi chek (skrinshot) yuborsa — u mavjud so'rovga
        biriktiriladi va barcha adminlarga [✅ Tasdiqlash] / [❌ Rad etish]
        tugmalari bilan yuboriladi (zaxira yo'l).

Xavfsizlik:
 - Balans faqat to'lov tasdiqlangach, atomik (`FOR UPDATE`) qo'shiladi.
 - Har bir so'rov bazada saqlanadi — ikki marta hisoblanmaydi
   (`DepositStateError`).
 - Invoys payload'i `topup:{deposit_id}:{user_id}` — pre_checkout'da
   egasi va so'rov holati tekshiriladi.
"""

from __future__ import annotations

import html
import logging
from typing import Any

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, PreCheckoutQuery
from sqlalchemy.exc import SQLAlchemyError

from config.settings import settings
from database.queries import (
    AUTO_DEPOSIT_ADMIN_ID,
    DEPOSIT_APPROVED,
    DEPOSIT_PENDING,
    DEPOSIT_REJECTED,
    SETTING_CARD_HOLDER,
    SETTING_CARD_NUMBER,
    DepositRepository,
    DepositStateError,
    RepositoryError,
    SettingsRepository,
    UserRepository,
)
from keyboards.inline import (
    CB_TOPUP_AUTO,
    CB_TOPUP_CANCEL,
    CB_TOPUP_CARD,
    CB_TOPUP_START,
    get_deposit_decision_kb,
    get_topup_cancel_kb,
    get_topup_methods_kb,
)
from services.button_registry import ButtonText
from services.card_payment import format_som
from services.payments import (
    TOPUP_PAYLOAD_PREFIX,
    build_topup_payload,
    parse_topup_payload,
    send_stars_invoice,
)
from states.topup_states import TopupStates

logger = logging.getLogger(__name__)

router = Router(name="user_topup")

# To'ldirish chegaralari (Stars)
MIN_TOPUP_STARS = 10
MAX_TOPUP_STARS = 1_000_000

# Usullar menyusi matni
METHODS_TEXT = (
    "💳 <b>Balans to'ldirish</b>\n\n"
    "Joriy balans: <b>{balance} ⭐️</b>\n\n"
    "To'lov usulini tanlang 👇\n\n"
    "⚡️ <b>Avtomatik to'lov</b> — Telegram Stars bilan to'lang, balans "
    "darhol qo'shiladi (admin kutilmaydi).\n"
    "💳 <b>Karta</b> — Uzcard / Humo kartaga o'tkazma qiling."
)

# Standart ogohlantirishlar
NO_CARD_TEXT = (
    "⚠️ <b>Karta rekvizitlari hali kiritilmagan.</b>\n\n"
    "Hozircha balansni karta orqali to'ldirib bo'lmaydi. "
    "Administrator bilan bog'laning yoki ⚡️ avtomatik to'lovdan foydalaning."
)

AUTO_AMOUNT_TEXT = (
    "⚡️ <b>Avtomatik to'lov (Telegram Stars)</b>\n\n"
    "Balansingizga qo'shmoqchi bo'lgan Stars miqdorini kiriting "
    "(masalan: <code>100</code>).\n\n"
    f"Minimal: <b>{MIN_TOPUP_STARS} ⭐️</b>\n"
    f"Maksimal: <b>{MAX_TOPUP_STARS} ⭐️</b>\n\n"
    "To'lov Telegram Stars balansingizdan yechiladi va bot balansingizga "
    "darhol qo'shiladi."
)

AUTO_INVOICE_SENT_TEXT = (
    "⚡️ <b>To'lov oynasi yuborildi!</b>\n\n"
    "├ Miqdor: <b>{amount} ⭐️</b>\n"
    "├ So'rov: <code>#{deposit_id}</code>\n"
    "└ Holat: 🕓 <i>to'lov kutilmoqda</i>\n\n"
    "To'lovni yakunlasangiz, balans <b>avtomatik</b> qo'shiladi."
)

AUTO_SUCCESS_TEXT = (
    "✅ <b>To'lov qabul qilindi — balans to'ldirildi!</b>\n\n"
    "├ Miqdor: <b>+{amount} ⭐️</b>\n"
    "├ Yangi balans: <b>{balance} ⭐️</b>\n"
    "└ So'rov: <code>#{deposit_id}</code>\n\n"
    "Xarid qilishingiz mumkin! 🛍"
)

ADMIN_AUTO_TOPUP_TEXT = (
    "⚡️ <b>AVTOMATIK STARS TO'LOVI</b>\n\n"
    "├ Foydalanuvchi: <code>{user_id}</code>\n"
    "├ Miqdor: <b>+{amount} ⭐️</b>\n"
    "└ So'rov: <code>#{deposit_id}</code>\n\n"
    "Balans admin aralashuvisiz qo'shildi."
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


def _user_id_of(target: Message) -> int:
    """Message'dan foydalanuvchi ID'sini oladi."""
    return target.from_user.id if target.from_user else target.chat.id


# =============================================================
# 1. USULLAR MENYUSI
# =============================================================
async def _begin_topup(target: Message, state: FSMContext) -> None:
    """To'ldirish usullarini taklif qiladi (avtomatik yoki karta)."""
    user_id = _user_id_of(target)
    user = await UserRepository.get_user(user_id)
    balance = user.stars_balance if user else 0
    number, _holder = await _card_details()

    text = METHODS_TEXT.format(balance=balance)
    if number:
        text += f"\n\n💳 Karta: <code>{_format_card_number(number)}</code>"

    await state.set_state(TopupStates.waiting_for_method)
    await target.answer(
        text,
        parse_mode="HTML",
        reply_markup=get_topup_methods_kb(),
    )
    logger.info("Topup usullari ko'rsatildi: user=%s", user_id)


async def _begin_card_topup(target: Message, state: FSMContext) -> None:
    """Karta orqali to'ldirish: miqdor so'raydi."""
    number, holder = await _card_details()
    if not number:
        await state.clear()
        await target.answer(NO_CARD_TEXT, parse_mode="HTML")
        return

    user_id = _user_id_of(target)
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
    logger.info("Karta topup boshlandi: user=%s", user_id)


# =============================================================
# 2. REPLY TUGMA VA CALLBACK — BOSHLASH
# =============================================================
@router.message(StateFilter("*"), ButtonText("menu_topup"))
@router.message(StateFilter("*"), F.text.contains("Balans to'ldirish"))
async def topup_start_message(
    message: Message, state: FSMContext | None = None
) -> None:
    """💳 Balans to'ldirish — FSM holatidan qat'i nazar darhol ishlaydi."""
    if state is None:
        return
    await state.clear()
    await _begin_topup(message, state)


@router.callback_query(F.data == CB_TOPUP_START)
async def topup_start_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    await state.clear()
    await _begin_topup(callback.message, state)


@router.callback_query(F.data == CB_TOPUP_CARD)
async def topup_card_callback(callback: CallbackQuery, state: FSMContext) -> None:
    """💳 Uzcard / Humo kartaga o'tkazish usuli."""
    await callback.answer()
    await _begin_card_topup(callback.message, state)


@router.callback_query(F.data == CB_TOPUP_AUTO)
async def topup_auto_callback(callback: CallbackQuery, state: FSMContext) -> None:
    """⚡️ Avtomatik to'lov — Stars miqdorini so'raydi."""
    await callback.answer()
    await state.set_state(TopupStates.waiting_for_auto_amount)
    await callback.message.answer(
        AUTO_AMOUNT_TEXT,
        parse_mode="HTML",
        reply_markup=get_topup_cancel_kb(),
    )


# =============================================================
# 3. BEKOR QILISH (state handler'laridan OLDIN)
# =============================================================
@router.callback_query(F.data == CB_TOPUP_CANCEL)
async def topup_cancel_callback(callback: CallbackQuery, state: FSMContext) -> None:
    """[ ❌ Yopish ] — holatni tozalaydi."""
    await state.clear()
    await callback.answer("Bekor qilindi")
    await callback.message.answer("❌ Balans to'ldirish bekor qilindi.")


@router.message(
    StateFilter(
        TopupStates.waiting_for_method,
        TopupStates.waiting_for_auto_amount,
        TopupStates.waiting_for_amount,
        TopupStates.waiting_for_receipt,
    ),
    F.text == "/cancel",
)
async def topup_cancel_text(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("❌ Balans to'ldirish bekor qilindi.")


# =============================================================
# 4. AVTOMATIK TO'LOV — MIQDOR + XTR INVOYS
# =============================================================
def _parse_amount(raw: str) -> int | None:
    """Matndan butun son ajratadi (bo'shliqlar olib tashlanadi)."""
    text = (raw or "").strip().replace(" ", "")
    if not text.isdigit():
        return None
    return int(text)


@router.message(TopupStates.waiting_for_auto_amount, F.text)
async def topup_auto_amount(message: Message, state: FSMContext) -> None:
    """Miqdorni tekshiradi va Telegram Stars (XTR) invoysini yuboradi."""
    amount = _parse_amount(message.text or "")
    if amount is None:
        await message.answer(
            "⚠️ Iltimos, miqdorni faqat butun son ko'rinishida yuboring "
            "(masalan: <code>100</code>).",
            parse_mode="HTML",
            reply_markup=get_topup_cancel_kb(),
        )
        return

    if amount < MIN_TOPUP_STARS:
        await message.answer(
            f"⚠️ Minimal to'ldirish — <b>{MIN_TOPUP_STARS} ⭐️</b>.",
            parse_mode="HTML",
            reply_markup=get_topup_cancel_kb(),
        )
        return
    if amount > MAX_TOPUP_STARS:
        await message.answer(
            f"⚠️ Maksimal to'ldirish — <b>{MAX_TOPUP_STARS} ⭐️</b>.",
            parse_mode="HTML",
            reply_markup=get_topup_cancel_kb(),
        )
        return

    user_id = message.from_user.id if message.from_user else message.chat.id
    try:
        deposit = await DepositRepository.create_deposit(user_id=user_id, amount=amount)
    except (RepositoryError, ValueError) as exc:
        logger.warning("Avtomatik topup deposit yaratilmadi (user=%s): %s", user_id, exc)
        await state.clear()
        await message.answer(
            "⚠️ So'rovni saqlab bo'lmadi. Iltimos, /start bosib qaytadan "
            "urinib ko'ring.",
            reply_markup=get_topup_cancel_kb(),
        )
        return
    except SQLAlchemyError:
        logger.exception("Avtomatik topup deposit DB xatosi (user=%s)", user_id)
        await state.clear()
        await message.answer(
            "⚠️ Bazada xatolik yuz berdi. Keyinroq urinib ko'ring.",
            reply_markup=get_topup_cancel_kb(),
        )
        return

    try:
        await send_stars_invoice(
            bot=message.bot,
            chat_id=message.chat.id,
            title=f"{amount} Stars",
            description=f"Bot balansini {amount} ⭐️ ga to'ldirish",
            payload=build_topup_payload(deposit.id, user_id),
            amount_xtr=amount,
        )
    except (TelegramAPIError, ValueError, RuntimeError) as exc:
        logger.exception(
            "Topup invoysi yuborilmadi (user=%s deposit=%s): %s",
            user_id, deposit.id, exc,
        )
        # Bekor qilingan so'rovni rad etamiz — keyingi urinishda yangi so'rov.
        try:
            await DepositRepository.reject_deposit(
                deposit.id, AUTO_DEPOSIT_ADMIN_ID, "Invoys yuborilmadi"
            )
        except Exception:  # noqa: BLE001
            logger.warning("Topup so'rovini rad etib bo'lmadi: %s", deposit.id)
        await state.clear()
        await message.answer(
            "⚠️ To'lov oynasini yuborib bo'lmadi. Keyinroq urinib ko'ring.",
            reply_markup=get_topup_cancel_kb(),
        )
        return

    await state.clear()
    await message.answer(
        AUTO_INVOICE_SENT_TEXT.format(amount=amount, deposit_id=deposit.id),
        parse_mode="HTML",
    )
    logger.info(
        "Topup XTR invoys yuborildi: user=%s deposit=%s amount=%s",
        user_id, deposit.id, amount,
    )


@router.pre_checkout_query(
    F.invoice_payload.startswith(f"{TOPUP_PAYLOAD_PREFIX}:")
)
async def topup_pre_checkout(query: PreCheckoutQuery) -> None:
    """Topup invoysi uchun pre_checkout — 10 soniya ichida javob beriladi."""
    parsed = parse_topup_payload(query.invoice_payload)
    if parsed is None:
        await query.answer(ok=False, error_message="Noto'g'ri to'lov so'rovi!")
        return

    deposit_id, owner_id = parsed
    if owner_id != query.from_user.id:
        await query.answer(
            ok=False, error_message="To'lov boshqa foydalanuvchiga tegishli!"
        )
        return

    deposit = await DepositRepository.get_deposit(deposit_id)
    if (
        deposit is None
        or deposit.user_id != query.from_user.id
        or deposit.status != DEPOSIT_PENDING
    ):
        await query.answer(
            ok=False,
            error_message="So'rov topilmadi yoki allaqachon qayta ishlangan!",
        )
        return

    await query.answer(ok=True)
    logger.info(
        "Topup pre_checkout tasdiqlandi: user=%s deposit=%s",
        query.from_user.id, deposit_id,
    )


@router.message(
    F.successful_payment.invoice_payload.startswith(f"{TOPUP_PAYLOAD_PREFIX}:")
)
async def topup_successful_payment(message: Message) -> None:
    """
    Avtomatik to'lov muvaffaqiyatli yakunlangach balansni darhol to'ldiradi.

    Idempotent: takroriy `successful_payment` balansni ikki marta oshirmaydi
    (`DepositStateError`).
    """
    payment = message.successful_payment
    if payment is None or message.from_user is None:
        return

    parsed = parse_topup_payload(payment.invoice_payload)
    if parsed is None:
        logger.error(
            "Topup successful_payment: noto'g'ri payload=%r user=%s",
            payment.invoice_payload, message.from_user.id,
        )
        return

    deposit_id, _owner_id = parsed
    try:
        deposit, new_balance = await DepositRepository.confirm_deposit(
            deposit_id, AUTO_DEPOSIT_ADMIN_ID
        )
    except DepositStateError:
        await message.answer(
            "✅ Bu to'lov allaqachon hisobga olingan — balans ikki marta "
            "qo'shilmadi."
        )
        return
    except (RepositoryError, SQLAlchemyError):
        logger.exception(
            "Topup to'lovini tasdiqlashda xatolik: deposit=%s charge=%s",
            deposit_id, payment.telegram_payment_charge_id,
        )
        await message.answer(
            "⚠️ To'lov qabul qilindi, lekin balansni yangilashda xatolik "
            "bo'ldi. Administrator tez orada tekshiradi."
        )
        return

    await message.answer(
        AUTO_SUCCESS_TEXT.format(
            amount=deposit.amount,
            balance=new_balance,
            deposit_id=deposit.id,
        ),
        parse_mode="HTML",
    )

    for admin_id in settings.ADMIN_IDS:
        try:
            await message.bot.send_message(
                admin_id,
                ADMIN_AUTO_TOPUP_TEXT.format(
                    user_id=deposit.user_id,
                    amount=deposit.amount,
                    deposit_id=deposit.id,
                ),
                parse_mode="HTML",
            )
        except TelegramAPIError:
            logger.warning("Adminga topup xabari yetmadi: %s", admin_id)

    logger.info(
        "Topup avtomatik tasdiqlandi: user=%s deposit=%s amount=%s",
        deposit.user_id, deposit.id, deposit.amount,
    )


# =============================================================
# 5. KARTA — MIQDOR
# =============================================================
@router.message(TopupStates.waiting_for_amount, F.text)
async def topup_amount(message: Message, state: FSMContext) -> None:
    """Miqdorni tekshiradi va karta rekvizitlari bilan chek so'raydi."""
    amount = _parse_amount(message.text or "")
    if amount is None:
        await message.answer(
            "⚠️ Iltimos, miqdorni faqat butun son ko'rinishida yuboring "
            "(masalan: <code>100</code>).",
            parse_mode="HTML",
            reply_markup=get_topup_cancel_kb(),
        )
        return

    if amount < MIN_TOPUP_STARS:
        await message.answer(
            f"⚠️ Minimal to'ldirish — <b>{MIN_TOPUP_STARS} ⭐️</b>.",
            parse_mode="HTML",
            reply_markup=get_topup_cancel_kb(),
        )
        return
    if amount > MAX_TOPUP_STARS:
        await message.answer(
            f"⚠️ Maksimal to'ldirish — <b>{MAX_TOPUP_STARS} ⭐️</b>.",
            parse_mode="HTML",
            reply_markup=get_topup_cancel_kb(),
        )
        return

    number, holder = await _card_details()
    if not number:
        await state.clear()
        await message.answer(NO_CARD_TEXT, parse_mode="HTML")
        return

    # Unikal summa bilan so'rov yaratamiz: `stars * SOM_PER_STAR + 1..99`.
    # To'lov avtomatik aniqlanishi uchun so'rov to'lovdan OLDIN bazada bo'lishi
    # shart. Jurnal chat sozlanmagan bo'lsa (CARD_LOG_CHAT_ID=0) avtomatik
    # tasdiqlash o'chirilgan — so'rov faqat chek kelganda yaratiladi.
    user_id = message.from_user.id if message.from_user else message.chat.id
    deposit = None
    if settings.CARD_LOG_CHAT_ID:
        try:
            deposit = await DepositRepository.create_deposit_with_unique_sum(
                user_id=user_id,
                amount=amount,
                som_per_star=settings.SOM_PER_STAR,
            )
        except (RepositoryError, ValueError) as exc:
            logger.warning("Unikal summa yaratilmadi (user=%s): %s", user_id, exc)
        except SQLAlchemyError:
            logger.exception("Unikal summa DB xatosi (user=%s)", user_id)

    await state.update_data(
        amount=amount,
        deposit_id=deposit.id if deposit else None,
        pay_amount=deposit.pay_amount if deposit else None,
    )
    await state.set_state(TopupStates.waiting_for_receipt)

    holder_line = f"\n👤 Egasi: <b>{html.escape(holder)}</b>" if holder else ""

    sum_block = ""
    auto_line = "⚠️ Chek yuborilmaguncha balans qo'shilmaydi."
    if deposit and deposit.pay_amount:
        code = deposit.pay_amount - amount * settings.SOM_PER_STAR
        sum_block = (
            "\n💵 To'lov summasi: "
            f"<b>{format_som(deposit.pay_amount)} so'm</b>\n"
            f"<i>({amount} ⭐️ = {format_som(amount * settings.SOM_PER_STAR)} "
            f"so'm + {code} so'm unikal kod)</i>\n"
        )
        auto_line = (
            "✅ Aynan yuqoridagi summani o'tkazing — to'lov jurnal orqali "
            "<b>avtomatik</b> tasdiqlanadi (admin kutilmaydi).\n"
            "🧾 Chek (skrinshot) yuborsangiz ham bo'ladi — u zaxira "
            "tekshiruv sifatida adminlarga boradi."
        )

    await message.answer(
        "🧾 <b>To'lov cheki</b>\n\n"
        f"To'ldiriladigan miqdor: <b>{amount} ⭐️</b>\n"
        f"{sum_block}\n"
        "Quyidagi kartaga o'tkazma qiling:\n"
        f"💳 <code>{_format_card_number(number)}</code>{holder_line}\n\n"
        f"{auto_line}",
        parse_mode="HTML",
        reply_markup=get_topup_cancel_kb(),
    )


# =============================================================
# 6. KARTA — CHEK (rasm) — so'rovni biriktirish + adminlarga yuborish
# =============================================================
async def _submit_receipt(
    message: Message, state: FSMContext, file_id: str
) -> None:
    """
    Chekni qabul qiladi va uni mavjud so'rovga biriktiradi (zaxira yo'l).

    So'rov miqdor kiritilganda yaratilgan bo'ladi (`deposit_id` state'da).
    Agar u allaqachon avtomatik tasdiqlangan bo'lsa — chek qabul qilinmaydi.
    """
    data = await state.get_data()
    amount = int(data.get("amount", 0))
    deposit_id = data.get("deposit_id")

    if amount <= 0:
        await state.clear()
        await message.answer(
            "⚠️ Xatolik yuz berdi. Iltimos, to'ldirishni qaytadan boshlang.",
            reply_markup=get_topup_cancel_kb(),
        )
        return

    user = message.from_user
    deposit = None
    try:
        if deposit_id:
            deposit = await DepositRepository.attach_receipt(int(deposit_id), file_id)
        if deposit is None:
            deposit = await DepositRepository.create_deposit(
                user_id=user.id,
                amount=amount,
                receipt_file_id=file_id,
                pay_amount=data.get("pay_amount"),
            )
    except (RepositoryError, ValueError) as exc:
        logger.warning("Deposit yaratishda xatolik: %s", exc)
        await message.answer(
            "⚠️ So'rovni saqlab bo'lmadi. Iltimos, keyinroq urinib ko'ring.",
            reply_markup=get_topup_cancel_kb(),
        )
        return
    except SQLAlchemyError:
        logger.exception("Deposit yaratishda DB xatosi (user=%s)", user.id)
        await message.answer(
            "⚠️ Bazada xatolik yuz berdi. Iltimos, keyinroq urinib ko'ring.",
            reply_markup=get_topup_cancel_kb(),
        )
        return

    if deposit.status == DEPOSIT_APPROVED:
        await state.clear()
        await message.answer(
            "✅ <b>Bu to'lov allaqachon avtomatik tasdiqlangan!</b>\n\n"
            f"├ Miqdor: <b>+{deposit.amount} ⭐️</b>\n"
            f"└ So'rov: <code>#{deposit.id}</code>\n\n"
            "Balansingiz to'ldirilgan — chek yuborish shart emas.",
            parse_mode="HTML",
        )
        return

    if deposit.status == DEPOSIT_REJECTED:
        await state.clear()
        await message.answer(
            "❌ <b>Bu so'rov allaqachon rad etilgan.</b>\n\n"
            'Yangi to\'ldirish uchun "💳 Balans to\'ldirish" tugmasini '
            "qaytadan bosing.",
            parse_mode="HTML",
        )
        return

    await state.clear()

    som_line = (
        f"├ To'lov summasi: <b>{format_som(deposit.pay_amount)} so'm</b>\n"
        if deposit.pay_amount
        else ""
    )
    await message.answer(
        "✅ <b>Chek qabul qilindi!</b>\n\n"
        f"├ Miqdor: <b>{amount} ⭐️</b>\n"
        f"{som_line}"
        f"├ So'rov: <code>#{deposit.id}</code>\n"
        "└ Holat: 🕓 <i>admin tekshiruvi kutilmoqda</i>\n\n"
        "To'lov jurnal orqali kelganda balans <b>avtomatik</b> qo'shiladi; "
        "aks holda admin chekni tasdiqlaydi.",
        parse_mode="HTML",
    )

    await _notify_admins_about_deposit(message, deposit, amount)
    logger.info(
        "Deposit cheki qabul qilindi: id=%s user=%s amount=%s",
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
            parse_mode="HTML",
            reply_markup=get_topup_cancel_kb(),
        )
        return
    await _submit_receipt(message, state, document.file_id)


@router.message(TopupStates.waiting_for_receipt)
async def topup_receipt_invalid(message: Message) -> None:
    """Boshqa kontent turi kelsa — qayta so'raydi."""
    await message.answer(
        "⚠️ Chekni <b>rasm</b> ko'rinishida yuboring (skrinshot).",
        parse_mode="HTML",
        reply_markup=get_topup_cancel_kb(),
    )


@router.message(TopupStates.waiting_for_method)
async def topup_method_invalid(message: Message) -> None:
    """Usul tanlanmagan — tugmalarni eslatib o'tadi."""
    await message.answer(
        "Iltimos, to'lov usulini yuqoridagi tugmalardan birini bosib tanlang 👇",
        parse_mode="HTML",
        reply_markup=get_topup_methods_kb(),
    )


# =============================================================
# 7. ADMINLARGA XABAR (karta cheki)
# =============================================================
async def _notify_admins_about_deposit(
    message: Message, deposit: Any, amount: int
) -> None:
    """Barcha adminlarga chekni tasdiqlash/rad etish tugmalari bilan yuboradi."""
    user = message.from_user
    if user is None:
        return
    if user.username:
        who = f"@{html.escape(user.username)}"
    else:
        who = html.escape(user.first_name or str(user.id))

    pay_amount = getattr(deposit, "pay_amount", None)
    pay_line = (
        f"├ To'lov summasi: <b>{format_som(pay_amount)} so'm</b>\n"
        if pay_amount
        else ""
    )
    caption = (
        "💳 <b>YANGI BALANS TO'LDIRISH SO'ROVI</b>\n\n"
        f"├ Foydalanuvchi: {who} (ID <code>{user.id}</code>)\n"
        f"├ Miqdor: <b>{amount} ⭐️</b>\n"
        f"{pay_line}"
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
