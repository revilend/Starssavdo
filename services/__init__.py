"""
services paketi — bot biznes-logikasi servislari.

Modullar:
 - channel_checker : majburiy homiy kanallar a'zoligini tekshirish (OP)
 - payments        : Telegram Stars (XTR) invoice servisi
 - emoji_loader    : Bot API 9.4 premium emoji ID'larini yuklash
 - button_registry : tugma matni/rangi/emojisi (DB'siz, sof)
 - button_store    : tugma override'larini settings jadvalida saqlash
 - gift_delivery   : Telegram sovg'alarini avtomatik yuborish (sendGift)

Diqqat: bu fayl ataylab modul qayta eksport qilmaydi — `services` paketini
import qilish (masalan `services.button_registry`) og'ir DB/config
zanjirini tortmasligi kerak, aks holda klaviaturalarni `.env`siz import
qilib bo'lmaydi.

To'g'ridan-to'g'ri modul import qiling:
    from services.channel_checker import check_user_subscription
    from services.emoji_loader import load_custom_emojis
"""
