"""
handlers.admin paketi — admin panel handlerlari (aiogram Router'lar).

Modullar:
 - panel.py       : asosiy admin menyusi va jonli statistika
 - channels.py    : majburiy (OP) kanallarni boshqarish
 - broadcast.py   : ommaviy xabar (rassilka)
 - users_manage.py: referal narxi va foydalanuvchi balansi
 - buttons.py     : tugmalar va premium emojilarni boshqarish
 - products.py    : do'kon mahsulotlari (qo'shish/tahrirlash/o'chirish)
 - withdrawals.py : yechib olish so'rovlari (tasdiqlash/rad etish)

Ulashish (main.py):
    from handlers.admin.panel import router as admin_panel_router
    dp.include_router(admin_panel_router)
"""
