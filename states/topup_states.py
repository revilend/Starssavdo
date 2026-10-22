"""
states/topup_states.py
======================
Karta (UZCARD/HUMO) orqali balans to'ldirish oqimi holatlari.

Oqim:
 1. "💳 Balans to'ldirish" -> `TopupStates.waiting_for_amount`
    (foydalanuvchi Stars miqdorini kiritadi).
 2. Miqdor tekshiriladi -> `TopupStates.waiting_for_receipt`
    (karta rekvizitlari ko'rsatiladi va chek rasmi so'raladi).
 3. Chek rasmi kelganda so'rov bazaga yoziladi va adminlarga yuboriladi.

Bekor qilish: "❌ Yopish" tugmasi (`topup_cancel`) yoki /cancel.
"""

from aiogram.fsm.state import State, StatesGroup


class TopupStates(StatesGroup):
    """Balans to'ldirish so'rovi uchun FSM holatlari."""

    waiting_for_amount = State()    # To'ldiriladigan Stars miqdori
    waiting_for_receipt = State()   # To'lov cheki (rasm)
