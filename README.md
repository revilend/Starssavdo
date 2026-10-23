# NFT YUT — UZS Telegram bot

Telegram Premium, Stars, NFT va Mayda Giftlarni **SO'MDA** sotadigan,
**HUMO karta** orqali avtomatik to'lov qabul qiladigan production bot.
To'lovlar `@humocardbot` xabarlari asosida **Telethon userbot** orqali
tasdiqlanadi; server Render.com da **FastAPI webhook** ko'rinishida 24/7
ishlaydi (UptimeRobot uchun `/health`).

## Imkoniyatlar

- 🛒 **Do'kon** — barcha narxlar so'mda, har bir mahsulot uchun alohida
  UZS buyurtma va karta to'lovi.
- 💳 **Avtomatik to'lov** — har bir buyurtmaga `+40..100 so'm` unikal kod
  qo'shiladi, 5 daqiqalik taymer (`expires_at`) va `@humocardbot`
  xabarini real vaqtda o'qish.
- 💎 **Premium** — @username so'raladi, to'lovdan keyin adminga
  `[✅ Fragment orqali yuborildi]` tugmasi boradi.
- ⭐️ **Stars** — to'lov tasdiqlanishi bilan balansga avtomatik qo'shiladi.
- 🐥 **Giftlar** — Telegram `sendGift` orqali darhol yuboriladi.
- 🖼 **NFT** — TON hamyon so'raladi, adminga transfer uchun xabar boradi.
- ⭐ **Balansim** — Stars balansi va so'mdagi qiymati, yechib olish
  (karta / TON / @username, min 50 ⭐) va tranzaksiyalar tarixi.
- 👥 **Referal** — do'st homiy kanallarga a'zo bo'lgach mukofot;
  1 soatda 15 tadan ortiq so'rov anti-fraud bilan cheklanadi.
- ⚙️ **Admin panel** — so'mdagi tushum statistikasi, buyurtmalarni
  tasdiqlash, kanallar, narxlar, balans, rassilka.

## Katalog (standart)

| Toifa | Mahsulot | Narx |
|---|---|---|
| 💎 Premium | 3 oy / 6 oy / 12 oy | 135 000 / 195 000 / 330 000 so'm |
| ⭐️ Stars | 50 / 100 / 250 / 500 / 1000 | 15 000 / 30 000 / 75 000 / 150 000 / 300 000 so'm |
| 🐥 Giftlar | 15 / 25 / 50 / 100 ⭐ | 4 500 / 7 500 / 15 000 / 30 000 so'm |
| 🖼 NFT | Bronze / Silver / Gold | 150 000 / 300 000 / 500 000 so'm |

Katalog `database/seed.py` da; `catalog_version=2` bilan bir marta
yoziladi, keyin admin panelda tahrirlanadi (narxlar so'mda).

## To'lov qanday ishlaydi

1. Foydalanuvchi mahsulotni tanlaydi (Premium/NFT uchun qo'shimcha
   ma'lumot kiritadi).
2. `orders` jadvalida `pending` buyurtma yaratiladi:
   `pay_amount = price_uzs + 40..100`, `expires_at = now + 5 daqiqa`.
   Payment xabarida karta va **aynan to'lanadigan summa** ko'rsatiladi.
3. `@humocardbot` xabari (`🎉 To'ldirish ➕ 135 068 UZS 💳 HUMOCARD *3573`)
   Telethon userbot yoki bot jurnal chati orqali o'qiladi.
4. Summa faol buyurtmaga moslansa, `OrderRepository.pay_order_by_amount`
   atomik `paid` qiladi va mahsulot turiga qarab yetkaziladi.
5. 5 daqiqa o'tib to'lanmasa, `services/deposit_expiry` +
   `services/order_expiry` buyurtmani `expired` qiladi va xabarni
   «⏳ To'lov muddati tugadi» holatiga o'zgartiradi. Kechikkan to'lov
   hisoblanmaydi.

Karta: `9860 1701 1124 3573` — **Murodjon Amirov**
(`payment_card_number` / `payment_card_holder` yoki `.env`).

## Tuzilma

| Papka / fayl | Vazifasi |
|---|---|
| `main.py` | FastAPI: `/`, `/health`, `/webhook`; lifespan (DB, webhook/polling, expiry worker, userbot) |
| `config/settings.py` | `.env` konfiguratsiyasi (pydantic-settings) |
| `database/` | Modellar, repository, sxema migratsiyasi, UZS katalog seed |
| `handlers/user/` | `/start`, obuna, do'kon va UZS checkout, balans, topup, payout, referal |
| `handlers/admin/` | Statistika, buyurtmalar, kanallar, mahsulotlar, balans, rassilka |
| `middlewares/` | Throttling + majburiy obuna |
| `services/` | `order_payments` (to'lov/yetkazish), `deposit_credit`, `order_expiry`, `userbot` (Telethon), `gift_delivery`, `card_payment` |
| `keyboards/`, `states/` | Reply/inline klaviaturalar va FSM holatlari |
| `tests/` | 78+ avtomatik test (pytest) |

## Asosiy menyu

```
[ 🛒 Do'kon ]                    [ ⭐ Balansim ]
[ 👥 Do'stlarni taklif qilish ]  [ 🐥 Mayda Giftlar ]
[ 💳 Balans to'ldirish ]
[ ℹ️ Qo'llanma ]
[ ⚙️ Admin Panel ]  (faqat adminlar)
```

Komandalar: `/start`, `/menu`, `/help`, `/shop`, `/balance`, `/topup`.

## Muhit o'zgaruvchilari

| O'zgaruvchi | Majburiy | Tavsif |
|---|---|---|
| `BOT_TOKEN` | ✅ | BotFather tokeni |
| `ADMIN_IDS` | ✅ | Admin IDlari, vergul bilan |
| `DATABASE_URL` | ✅ | Supabase PostgreSQL (Session/Transaction pooler) |
| `WEBHOOK_URL` | ✅ (`webhook`) | `https://<service>.onrender.com` |
| `WEBHOOK_SECRET` | tavsiya | `A-Za-z0-9_-`, 1–256 belgi |
| `BOT_MODE` | — | `webhook` (default) yoki `polling` |
| `DB_SSL` | — | `auto` / `require` / `disable` |
| `CARD_NUMBER` | — | HUMO karta (default `9860170111243573`) |
| `CARD_HOLDER` | — | Karta egasi (default `Murodjon Amirov`) |
| `SOM_PER_STAR` | — | 1 ⭐ = 300 so'm (DB `star_price_uzs` ustunlik qiladi) |
| `DEPOSIT_TTL_SECONDS` | — | Buyurtma muddati (default 300 = 5 daqiqa) |
| `TELEGRAM_API_ID` | ixtiyoriy | Telethon API ID (`my.telegram.org/apps`) |
| `TELEGRAM_API_HASH` | ixtiyoriy | Telethon API HASH |
| `TELEGRAM_SESSION_STRING` | ixtiyoriy | Telethon StringSession |
| `HUMOCARD_BOT_USERNAME` | — | Default `humocardbot` |

### Telethon userbot

Bot API boshqa bot yuborgan guruh xabarlarini ko'rmaydi, shuning uchun
`@humocardbot` xabarlarini shaxsiy profil (MTProto) o'qiydi:

```bash
# 1) https://my.telegram.org/apps -> API_ID, API_HASH
# 2) bir martalik sessiya (lokal, repo'ga tushmaydi):
python - <<'PY'
from telethon import TelegramClient
from telethon.sessions import StringSession
with TelegramClient(StringSession(), API_ID, 'API_HASH') as c:
    print(c.session.save())
PY
```

`.env`ga `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `TELEGRAM_SESSION_STRING`
yozing. Bo'sh bo'lsa userbot ishga tushmaydi — bot jurnal chat
(`CARD_LOG_CHAT_ID`) rejimida ishlayveradi.

> Session string — shaxsiy profil kaliti. Uni chatga, kodga yoki repo'ga
> yozmang; Render → Environment (yoki lokal `.env`) da saqlang.

## Lokal ishga tushirish

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # BOT_TOKEN, ADMIN_IDS, DATABASE_URL ...
# .env: BOT_MODE=polling, WEBHOOK_URL= (bo'sh)
python main.py
```

Health: `http://127.0.0.1:8080/health`.

## Render.com

1. Dashboard → **New** → **Blueprint** → `revilend/Starssavdo`.
2. Environment: `BOT_TOKEN`, `ADMIN_IDS`, `DATABASE_URL`, `WEBHOOK_URL`,
   `WEBHOOK_SECRET`, `TELEGRAM_API_ID/HASH/SESSION_STRING`.
3. Start: `uvicorn main:app --host 0.0.0.0 --port $PORT`;
   health check `/health`.
4. UptimeRobot bilan `https://<service>.onrender.com/health` ni har
   5 daqiqada ping qiling — free instance uxlab qolmaydi.
5. Deploydan keyin bot startupida `ensure_schema()` yangi ustunlarni
   qo'shadi, `seed_default_products()` UZS katalogini va kartani yozadi.

## Testlar

```bash
python -m pytest -q
python tests/test_uzs_orders.py      # UZS oqimi (katalog, to'lov, yetkazish)
python tests/test_payments_shop.py   # eski XTR/balans va do'kon
```

Testlar SQLite'da ishlaydi (`aiosqlite`), production esa PostgreSQL.
