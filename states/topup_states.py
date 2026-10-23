"""
states/topup_states.py
======================
Balans to'ldirish oqimi holatlari.

Oqim (2 usul):
 1. "💳 Balans to'ldirish" -> `TopupStates.waiting_for_method`
    (usul tanlash: ⚡️ avtomatik XTR to'lov yoki 💳 karta).
 2a. ⚡️ Avtomatik to'lov -> `TopupStates.waiting_for_auto_amount`
     (foydalanuvchi Stars miqdorini kiritadi) -> `send_invoice` (XTR).
 2b. 💳 Karta -> `TopupStates.waiting_for_amount`
     (foydalanuvchi Stars miqdorini kiritadi).
 3. Karta oqimida miqdor tekshiriladi ->
    `TopupStates.waiting_for_receipt` (karta rekvizitlari + chek rasmi).
 4. Chek rasmi kelganda so'rov bazaga yoziladi va adminlarga yuboriladi.

Bekor qilish: "❌ Yopish" tugmasi (`topup_cancel`) yoki /cancel.
"""

from aiogram.fsm.state import State, StatesGroup


class TopupStates(StatesGroup):
    """Balans to'ldirish so'rovi uchun FSM holatlari."""

    waiting_for_method = State()        # To'lov usulini tanlash
    waiting_for_auto_amount = State()   # Avtomatik XTR to'lov miqdori
    waiting_for_amount = State()        # Karta orqali to'ldiriladigan Stars
    waiting_for_receipt = State()       # To'lov cheki (rasm)
