"""
states/payout_states.py
=======================
Stars yechib olish (payout) oqimi uchun FSM holatlari.

Oqim:
 1. Foydalanuvchi "💸 Starslarni yechib olish" tugmasini bosadi
    -> `PayoutStates.waiting_for_amount` holatiga o'tadi.
 2. Miqdor kiritiladi va tekshiriladi (butun son, min <= amount <= balance)
    -> `PayoutStates.waiting_for_destination` holatiga o'tadi.
 3. Manzil (TON hamyon yoki @username) kiritiladi -> `state.clear()`
    va so'rov bazaga yoziladi (atomik hold bilan).

Ishlatish:
    from states.payout_states import PayoutStates

    await state.set_state(PayoutStates.waiting_for_amount)
    await state.clear()
"""

from aiogram.fsm.state import State, StatesGroup


class PayoutStates(StatesGroup):
    """Stars yechib olish so'rovi uchun FSM holatlari."""

    waiting_for_amount = State()      # Yechmoqchi bo'lgan Stars miqdori
    waiting_for_destination = State()  # TON hamyon manzili yoki Telegram @username