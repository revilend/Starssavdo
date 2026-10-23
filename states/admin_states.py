"""
states/admin_states.py
======================
Admin panel uchun FSM holatlari (aiogram 3.x StatesGroup).

Guruhlar:
 - AddChannelStates       : majburiy (OP) kanalni bot ichidan qo'shish
     1. waiting_for_chat_id      -> kanal chat_id (-100...)
     2. waiting_for_title        -> kanal nomi
     3. waiting_for_invite_link  -> taklif havolasi
 - BroadcastStates         : ommaviy xabar (rassilka)
     1. waiting_for_content  -> matn/rasm/video qabul qilinadi
     2. waiting_for_confirm  -> tasdiqlash (✅ Yuborish / ❌ Bekor qilish)
 - ChangeSettingsStates    : tizim sozlamalarini o'zgartirish
     1. waiting_for_referral_reward  -> bir referal uchun Stars (standart 5)
     2. waiting_for_min_withdrawal   -> minimal yechib olish miqdori
 - ManualBalanceStates     : foydalanuvchi balansini qo'lda o'zgartirish
     1. waiting_for_user_id  -> nixon foydalanuvchi IDsi
     2. waiting_for_amount   -> qo'shish/ayirish miqdori (+100 / -50)
 - ProductStates           : mahsulot qo'shish / tahrirlash
 - GiftStates              : '🎁 Giftlarni kiritish' (nom, narx, ta'rif, rasm, gift_id)
 - PaymentCardStates       : to'lov kartasi rekvizitlari va tizim qiymatlari

Ishlatish:
    from states.admin_states import AddChannelStates
    await state.set_state(AddChannelStates.waiting_for_chat_id)
"""

from aiogram.fsm.state import State, StatesGroup


class AddChannelStates(StatesGroup):
    """Majburiy (OP) kanalni bot ichidan qo'shish oqimi."""

    waiting_for_chat_id = State()      # Kanal chat_id (-100...)
    waiting_for_title = State()        # Kanal nomi
    waiting_for_invite_link = State()  # Taklif havolasi (https://t.me/+...)


class BroadcastStates(StatesGroup):
    """Ommaviy xabar (rassilka) oqimi."""

    waiting_for_content = State()  # Matn, rasm yoki video qabul qilish
    waiting_for_confirm = State()  # Tasdiqlash (yuborish/bekor qilish)


class ChangeSettingsStates(StatesGroup):
    """Tizim sozlamalarini o'zgartirish (referal narxi, minimal yechish)."""

    waiting_for_referral_reward = State()  # Bitta referal uchun Stars
    waiting_for_min_withdrawal = State()   # Minimal yechib olish miqdori


class ManualBalanceStates(StatesGroup):
    """Foydalanuvchi balansini qo'lda o'zgartirish (+/- Stars)."""

    waiting_for_user_id = State()  # Nixo'langan foydalanuvchi IDsi
    waiting_for_amount = State()   # Miqdor: "+100" (qo'shish) yoki "-50" (ayirish)


class ButtonEditStates(StatesGroup):
    """Admin panelda tugma matni va premium emojisini o'zgartirish."""

    waiting_for_text = State()   # Yangi tugma nomi
    waiting_for_emoji = State()  # Premium emoji (custom_emoji entity) xabari


class ProductStates(StatesGroup):
    """Do'kon mahsulotlarini qo'shish / tahrirlash (admin panel)."""

    waiting_for_title = State()        # Mahsulot nomi
    waiting_for_description = State()  # Tavsif ("-" — o'tkazib yuborish)
    waiting_for_price = State()        # Narx (so'm)
    waiting_for_gift_id = State()      # Telegram gift_id (gift toifasi uchun)
    waiting_for_edit_value = State()   # Tahrirlanayotgan maydonning yangi qiymati


class GiftStates(StatesGroup):
    """'🎁 Giftlarni kiritish' — alohida gift kiritish oqimi (admin)."""

    waiting_for_title = State()        # 1. Gift nomi
    waiting_for_price = State()        # 2. Narx (so'm)
    waiting_for_description = State()  # 3. Ta'rif ("-" — o'tkazib yuborish)
    waiting_for_image = State()        # 4. Rasm ("-" — o'tkazib yuborish)
    waiting_for_gift_id = State()      # 5. Telegram gift_id ("-" — keyinroq)


class PaymentCardStates(StatesGroup):
    """To'lov sozlamalari (UZCARD/HUMO karta, narx, xabarnoma guruhi)."""

    waiting_for_number = State()     # Karta raqami (16-19 raqam)
    waiting_for_holder = State()     # Karta egasining F.I.SH.
    waiting_for_star_price = State()  # 1 Stars narxi (so'm)
    waiting_for_log_chat = State()    # Xabarnoma guruhi/kanali chat ID
