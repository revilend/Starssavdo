"""
states/order_states.py
======================
UZS do'kon buyurtmasi FSM holatlari.

Premium va NFT mahsulotlari uchun qo'shimcha ma'lumot so'raladi:
 - Premium: foydalanuvchining @username'i (Fragment orqali ulanadi);
 - NFT: TON hamyon manzili (transfer uchun).

Stars va Giftlar qo'shimcha ma'lumot talab qilmaydi — buyurtma darhol
karta to'lovi xabariga o'tadi.
"""

from aiogram.fsm.state import State, StatesGroup


class OrderStates(StatesGroup):
    """UZS buyurtma ma'lumotlarini yig'ish."""

    waiting_for_username = State()  # Premium uchun @username
    waiting_for_wallet = State()    # NFT uchun TON hamyon
