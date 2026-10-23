# Starssavdo — @nftyutbot

Telegram Stars, Premium, NFT, Mayda Giftlar va referal bonuslari uchun
aiogram 3.x boti. Render.com (Free Web Service) da **webhook** rejimida
24/7 ishlaydi, baza — Supabase (PostgreSQL).

## Tuzilma

| Papka | Vazifasi |
|---|---|
| `main.py` | FastAPI ilova: health check + `/webhook` + lifespan (DB, polling) |
| `config/settings.py` | `.env` dan konfiguratsiya (pydantic-settings) |
| `database/connection.py` | SQLAlchemy async engine + Supabase pool sozlamalari |
| `database/models.py`, `database/queries.py` | Jadval modellari va repository qatlami |
| `handlers/user/` | `/start`, obuna, do'kon, referal, yechib olish |
| `handlers/admin/` | Admin panel: statistika, kanallar, rassilka, mahsulotlar, foydalanuvchilar, yechim |
| `middlewares/` | Throttling + majburiy obuna tekshiruvi |
| `states/` | FSM holatlari |
| `services/` | To'lov (XTR invoice), obuna, premium emoji, tugma reyestri, gift yetkazish, komandalar menyusi |
| `render.yaml` | Render Blueprint (uvicorn start command, env o'zgaruvchilar) |
| `database/schema.py` | Yetishmayotgan jadvallarni avtomatik yaratish (`create_all`) |

## Komandalar menyusi (yozish satridagi Menyu tugmasi)

Bot har bir ishga tushishda `services/commands.py` orqali quyidagilarni
o'rnatadi (`lifespan`, webhook va polling rejimlarida bir xil):

```python
await bot.set_my_commands(BOT_COMMANDS)              # /start /menu /help ...
await bot.set_chat_menu_button(menu_button=MenuButtonCommands(type="commands"))
```

Telegram `setMyCommands` ro'yxati faol ekan yozish satrida **Menyu**
tugmasini (4 ta to'rtburchak) **avtomatik** chiqarib beradi; u bosilganda
komandalar ro'yxati ochiladi:

| Komanda | Nima qiladi |
|---|---|
| `/start` | Bosh menyu (obuna tekshiruvi bilan) |
| `/menu` | Asosiy reply menyuni qayta ochadi |
| `/help` | ℹ️ Qo'llanma |
| `/shop` | 🛍 Do'kon |
| `/balance` | ⭐️ Balansim |
| `/topup` | 💳 Balans to'ldirish |

Har bir komanda uchun `handlers/` da handler mavjud (`tests/test_commands.py`
buni avtomatik tekshiradi). Komandalar o'rnatilsa ham startup **to'xtamaydi**
— Telegram API xatosi faqat logga yoziladi.

Asosiy pastki klaviatura esa `keyboards/reply.py` da `resize_keyboard=True`
va **`is_persistent=True`** bilan quriladi: foydalanuvchi tugmalarni
yashirib qo'yganda ham menyu istalgan payt Menyu tugmasi (yoki `/menu`)
orqali qayta ochiladi.

## Ish rejimlari

| `BOT_MODE` | Kim uchun | Nima qiladi |
|---|---|---|
| `webhook` (default) | Render / production | `uvicorn` FastAPI'ni ko'taradi, Telegram update'larni `/webhook` ga yuboradi |
| `polling` | Lokal kompyuter | `set_webhook` o'chiriladi, bot `getUpdates` bilan ishlaydi (https manzil kerak emas) |

> Bitta jarayon/worker ishlating. FSM `MemoryStorage` va TTL keshlar
> jarayon ichida saqlanadi, shuning uchun `--workers 2+` holatda
> holatlar bo'linib ketadi.

## Muhit o'zgaruvchilari

| O'zgaruvchi | Majburiy | Tavsif |
|---|---|---|
| `BOT_TOKEN` | ✅ | BotFather'dan olingan token |
| `ADMIN_IDS` | ✅ | Admin Telegram IDlari, vergul bilan: `5555809414,987654321` |
| `DATABASE_URL` | ✅ | Supabase connection string (Session/Transaction pooler) |
| `WEBHOOK_URL` | ✅ (`webhook`) | Render manzili: `https://<service>.onrender.com` (oxirida `/` yo'q) |
| `WEBHOOK_SECRET` | tavsiya | Telegram `secret_token` (`A-Za-z0-9_-`, 1–256). Masalan `openssl rand -hex 32` |
| `BOT_MODE` | — | `webhook` (default) yoki `polling` |
| `DB_SSL` | — | `auto` (default), `require`, `disable` |
| `REFERRAL_REWARD_STARS` | — | Fallback referal narxi (default 10); DB `settings` jadvali ustunlik qiladi |
| `MIN_WITHDRAWAL_STARS` | — | Fallback minimal yechish (default 50) |
| `PORT` | Render beradi | Tinglanadigan port; Render o'zi qo'yadi (10000+) — qo'lda yozmang |

## Lokal ishga tushirish

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # qiymatlarni to'ldiring
# .env ichida: BOT_MODE=polling, WEBHOOK_URL= (bo'sh)
python main.py
```

`python main.py` uvicorn'ni `0.0.0.0:$PORT` da ko'taradi; health check
`http://127.0.0.1:8080/health` da javob beradi.

Bot tokeni BotFather'dan, baza connection string'i esa Supabase
Dashboard → Connect → Session pooler orqali olinadi.

## Render.com da 24/7 ishga tushirish (bosqichma-bosqich)

### 1. Repozitoriyni Render'ga ulash

1. [dashboard.render.com](https://dashboard.render.com) → **New** → **Blueprint**.
2. GitHub akkauntini ulang va `https://github.com/revilend/Starssavdo`
   repozitoriysini tanlang.
3. Render `render.yaml` ni o'qiydi va `nftyutbot` nomli **free** web
   service yaratishni taklif qiladi → **Apply**.

### 2. Environment o'zgaruvchilarini kiritish

Service → **Environment** bo'limida (`sync: false` bo'lgani uchun Render
so'raydi yoki qo'lda qo'shasiz):

| Key | Value |
|---|---|
| `BOT_TOKEN` | `123456789:AA...` (BotFather) |
| `ADMIN_IDS` | `5555809414,<ikkinchi_admin_id>` |
| `DATABASE_URL` | `postgresql://postgres.<ref>:<parol>@aws-0-<region>.pooler.supabase.com:6543/postgres` |
| `WEBHOOK_URL` | `https://<service>.onrender.com` (deploy'dan keyin ma'lum bo'ladi) |
| `WEBHOOK_SECRET` | `openssl rand -hex 32` natijasi |

`BOT_MODE=webhook` va `PYTHON_VERSION=3.11.9` blueprint'da avtomatik
qo'yiladi. `PORT` ni **yozmang** — Render o'zi beradi.

Build: `pip install -r requirements.txt`
Start: `uvicorn main:app --host 0.0.0.0 --port $PORT`

### 3. Deploy va webhookni tekshirish

1. **Manual Deploy → Deploy latest commit**.
2. Logda quyidagilar ko'rinishi kerak:
   `PostgreSQL bilan ulanish muvaffaqiyatli` va
   `Webhook o'rnatildi: https://<service>.onrender.com/webhook`.
3. Brauzerda `https://<service>.onrender.com/health` →
   `{"status":"ok","bot":"@nftyutbot"}`.
4. Webhook holati:

```bash
curl "https://api.telegram.org/bot<BOT_TOKEN>/getWebhookInfo"
```

`url` to'g'ri bo'lishi, `pending_update_count` o'smasligi va
`last_error_message` bo'lmasligi kerak.

### 4. UptimeRobot bilan uyg'oq saqlash

Render **free** web service 15 daqiqa harakatsizlikdan keyin uxlaydi.
Webhook rejimida bu update'larni yo'qotadi, shuning uchun tashqi ping shart:

1. [uptimerobot.com](https://uptimerobot.com) → ro'yxatdan o'ting →
   **Add New Monitor**.
2. **Monitor Type:** `HTTP(s)`
3. **Friendly Name:** `nftyutbot`
4. **URL:** `https://<service>.onrender.com/health`
5. **Monitoring Interval:** `5 minutes`
6. **Create Monitor**.

UptimeRobot har 5 daqiqada `/health` ga ping yuboradi → Render service
uyg'oq qoladi va Telegram update'lari darhol qabul qilinadi.
Bepul plan oyiga ~750 soat monitoring beradi — bitta monitor uchun yetarli.

### 5. Keyingi deploy'lar

`autoDeploy: false` — har push'dan keyin **Manual Deploy** qiling yoki
dashboard'da avtomatik deploy'ni yoqing. Deploy paytida Render eski
jarayonni to'xtatadi: `lifespan` shutdown webhookni o'chiradi, keyin
yangi jarayon uni qayta o'rnatadi. Telegram 409 ("terminated by other
getUpdates request") xatosi polling va webhook aralashganda chiqadi —
production'da faqat `BOT_MODE=webhook` ishlating.

## Endpointlar

| Manzil | Metod | Javob |
|---|---|---|
| `/` | GET | `200` — `Bot ishlayapti` (oddiy matn) |
| `/health` | GET | `200 {"status": "ok", "bot": "@nftyutbot"}` |
| `/webhook` | POST | Telegram update → Dispatcher; `200 {"ok": true}` |

`/` — dummy server funksiyasi: Render birinchi so'rovni shu yerga yuboradi,
server ochiq portni eshitishi uchun shu yetarli. Ikkala rejimda
(`BOT_MODE=webhook` va `polling`) shu FastAPI ilova `0.0.0.0:$PORT` da
tinglaydi — alohida aiohttp server ishga tushirish shart emas (port ikki
marta band bo'lardi). `polling` rejimida esa webhook avval o'chiriladi
(`delete_webhook`), keyin `dp.start_polling()` shu loopda fon vazifasi
bo'lib ishlaydi — alohida `render_polling.py` fayli shu sababdan olib
tashlandi.

`/webhook` xatti-harakatlari:

* `WEBHOOK_SECRET` o'rnatilgan bo'lsa `X-Telegram-Bot-Api-Secret-Token`
  sarlavhasi tekshiriladi; mos kelmasa — **403**.
* Buzilgan JSON yoki noto'g'ri `Update` — **400**.
* Handlerdagi kutilmagan xato — **500** (Telegram qayta yuboradi; buyurtma
  va referal mukofotlari idempotent).

## Bot API 9.4: rangli tugmalar va premium emoji

`services/emoji_loader.py` bot ishga tushganda quyidagi sticker to'plamlaridan
`custom_emoji_id` larni yuklaydi (topilmasa bot ishlashda davom etadi):

| To'plam | Ishlatilishi |
|---|---|
| `SystemicDuck`, `CastilosDuck` | asosiy menyu (Do'kon, Balans, Taklif, Giftlar, Admin) |
| `statuspack_uz` | ✅ A'zo bo'ldim, Tasdiqlash |
| `business_emojis` | 💳 to'lov va 💸 yechib olish |

Tugmalar `services/button_registry.py` orqali quriladi va quyidagi ranglarni
oladi (Bot API 9.4 `style`):

* `primary` (ko'k) — Do'kon, Balansim, Giftlar, Premium, NFT, navigatsiya;
* `success` (yashil) — Do'stlarni taklif qilish, Balans to'ldirish, A'zo bo'ldim, Yechib olish, Tasdiqlash;
* `danger` (qizil) — Admin Panel, Rad etish, Yopish, Chiqish.

> Custom emoji faqat **bot egasida Telegram Premium** bo'lsa ko'rinadi — bu
> Telegram cheklovi; aks holda matndagi oddiy emoji qoladi.

### Admin panel: 🎛 Tugnalar va Emojilar

Admin panel → **🎛 Tugnalar va Emojilar**: istalgan tugmani tanlab,

* nomini o'zgartirish (handler'lar `ButtonText(key)` filtri bilan ishlaydi,
  shuning uchun nom o'zgarsa ham tugma ishlaydi);
* Premium emoji yuborish (`message.entities` dagi `custom_emoji_id` olinadi)
yoki `custom_emoji_id` raqamini kiritish;
* **♻️ Default holatga qaytarish**.

O'zgarishlar `settings` jadvalida `btn:<key>:text` / `btn:<key>:icon`
sifatida saqlanadi va darhol barcha klaviaturalarga qo'llanadi.

## Balans to'ldirish — 2 usul

Foydalanuvchi **💳 Balans to'ldirish** tugmasini bosganda ikkita usul
taklif qilinadi:

* **⚡️ Avtomatik to'lov (Telegram Stars / XTR)** — foydalanuvchi Stars
  miqdorini kiritadi, bot `send_invoice(currency="XTR")` yuboradi.
  `successful_payment` kelganda balans **admin aralashuvisiz darhol**
  qo'shiladi (`deposits` yozuvi + `transactions` audit), takroriy to'lov
  ikki marta hisoblanmaydi.
* **💳 Uzcard / Humo kartaga o'tkazish** — unikal summa + chek (quyida).

## Karta orqali balans to'ldirish (UZCARD / HUMO) — 100% avtomatik

1. Admin panel → **💳 Karta ma'lumotlari** tugmasi (yoki eski yo'l:
   **⭐️ Referal narxini sozlash** → **💳 To'lov kartasi**): karta raqami
   (16–19 raqam) va egasi kiritiladi
   (`payment_card_number`, `payment_card_holder`). Shu menyuda
   **⭐️ 1 Stars narxi** (`star_price_uzs`, standart 250 so'm) va
   **📢 Xabarnoma guruhi ID** (`card_log_chat_id`) ham sozlanadi.
2. **💳 Balans to'ldirish** → **💳 Uzcard / Humo kartaga o'tkazish** →
   tayyor miqdor tugmasi (50/100/250/500/1000 ⭐️) yoki o'z miqdori
   kiritiladi (min 10 ⭐️). Bot summani hisoblaydi:
   `stars × star_price_uzs + 1..99 so'm unikal kod` (masalan 100 ⭐️ va
   250 so'm → **25 043 so'm**). Unikal kod takrorlanmaydi.
3. Foydalanuvchi aynan shu summani kartaga o'tkazadi. `@CardXabarBot` /
   `@HUMOcardbot` xabari bot a'zo bo'lgan **har qanday guruh/kanalga**
   tushganda bot matndan summani ajratadi (`10 037`, `10037`, `10,037`,
   NBSP — barchasi) va `pending` so'rovni topib balansni **admin
   aralashuvisiz atomik** to'ldiradi (`deposits` + `deposit` audit
   yozuvi), foydalanuvchiga «🎉 To'lovingiz avtomatik qabul qilindi!»
   xabarini yuboradi.
4. Zaxira yo'l: foydalanuvchi chek skrinshotini yuborsa, chek o'sha
   so'rovga biriktiriladi va barcha adminlarga **[✅ Tasdiqlash] /
   [❌ Rad etish]** bilan boradi. Rad etilsa balans o'zgarmaydi.
5. Ikki marta hisoblanmaydi — so'rov `pending` holatidan chiqadi
   (avtomatik tasdiqlash ham, admin tasdig'i ham idempotent).

Sozlash: admin panel → **⭐️ Referal narxini sozlash** bo'limi
(`star_price_uzs`, `card_log_chat_id`). `.env` dagi `SOM_PER_STAR` va
`CARD_LOG_CHAT_ID` faqat baza qiymati yo'q bo'lsa fallback bo'ladi.

> **Telegram cheklovi:** botlar boshqa botlar yuborgan **guruh** xabarlarini
> olmaydi. Shuning uchun jurnal sifatida **kanal** ishlating (bank boti
> kanalga post qiladi, bizning bot kanal admini bo'ladi) yoki to'lov
> xabarini botga qo'lda forward qiling. Bu holatda ham chek + admin yo'li
> ishlaydi.

## 🛍 Do'kon — faqat bot balansi orqali

Mahsulot tugmasi bosilganda buyurtma darhol **bot balansidan** to'lanadi
(`orders` + `purchase` audit atomik yozuv), Stars yetarli bo'lmasa
foydalanuvchiga «❌ Balansingizda Stars yetarli emas!» xabari va
**[💳 Balans to'ldirish]** tugmasi yuboriladi. `gift_id` mavjud bo'lsa
sovg'a `sendGift` bilan avtomatik jo'natiladi va adminga xarid haqida
xabarnoma boradi. Telegram Stars (XTR) invoysi endi faqat balansni
to'ldirishda ishlatiladi, mahsulot xaridida emas.

`deposits`, `transactions`, `users` jadvallari va yetishmayotgan barcha
ustunlar (`pay_amount`, `payment_comment`, `users.total_earned_stars`, ...)
startupda `ensure_schema()` orqali avtomatik yaratiladi/qo'shiladi — mavjud
ma'lumotlarga tegmaydi. `products` jadvali bo'sh bo'lsa, `seed_default_products()`
har bir toifa (`stars`, `premium`, `nft`, `gift`) uchun boshlang'ich
mahsulotlarni qo'shadi.

## 🛍 Admin: mahsulotlarni boshqarish

Admin panelda uchta tegishli tugma bor.

### 🎁 Giftlarni kiritish (`adm_gift_add`)

Yangi sovg'ani bosqichma-bosqich kiritish (FSM):

1. **Nom** (1–64 belgi); 2. **Narx** (XTR, butun son);
3. **Ta'rif** (`-` — o'tkazib yuborish); 4. **Rasm** — foto sifatida
   (fayl `file_id` sifatida `products.image_file_id` ga saqlanadi,
   `-` — o'tkazib yuborish); 5. **Telegram Gift ID** (`-` — keyinroq).

Ma'lumot `products` jadvaliga `category='gift'` sifatida yoziladi va darhol
**🎁 Mayda Giftlar** bo'limida sotila boshlanadi.

### 🛍 Mahsulotlarni boshqarish (`adm_products`)

* **📋 Barcha mahsulotlar** — bazadagi barcha sotiladigan narsalar
  (stars, premium, nft, gift) bitta ro'yxatda; yuqorida esa toifalar
  (soni bilan) ko'rsatiladi;
* mahsulotni tanlab **nomini, tavsifini, narxini, rasmini** (yangi foto
  yuborish yoki `-` bilan o'chirish) va **`gift_id`** ni tahrirlash;
* **🚫 Nofaol qilish / ✅ Faollashtirish** — do'konda ko'rinishini boshqarish;
* **🗑 O'chirish** — buyurtmasi yo'q mahsulot butunlay o'chiriladi, buyurtmasi
  bor mahsulot tarixni saqlash uchun faqat nofaol qilinadi.

Rasmli mahsulot do'konda **foto sifatida** (tavsifda nomi va narxi bilan)
ko'rsatiladi.

### 💳 Karta ma'lumotlari (`adm_card`)

Karta raqami va egasini kiriting — foydalanuvchi **💳 Balans to'ldirish**
ni bosganda aynan shu raqam ko'rsatiladi.

Har qanday formani `/cancel` bilan bekor qilish mumkin. O'zgarishlar darhol
do'konda ko'rinadi — botni qayta ishga tushirish shart emas.

### SQL migratsiya (yangi ustunlar)

Bot startupda `ensure_schema()` orqali quyidagi buyruqlarni **avtomatik**
bajaradi (Supabase/Postgres'da xavfsiz, `IF NOT EXISTS` idempotent):

```sql
-- Mahsulot rasmi (admin 'Giftlarni kiritish' yoki kartadan kiritadi)
ALTER TABLE products ADD COLUMN IF NOT EXISTS image_file_id TEXT;
```

Kerak bo'lsa qo'lda ishga tushirishingiz mumkin (yana bir marta bajarish
xavfsiz). Yangi jadval yaratilishi shart emas — giftlar `products` jadvalida
saqlanadi; `payment_card_number` / `payment_card_holder` esa `settings`
jadvalida kalit sifatida o'qiladi (yozish avtomatik, migratsiya kerak emas).

## Giftlarni avtomatik yuborish (`sendGift`)

Mahsulot `payload` (JSON) maydonida rasmiy Telegram `gift_id` bo'lsa, to'lov
muvaffaqiyatli yakunlangach sovg'a avtomatik jo'natiladi:

```json
{"gift_id": "5170145012310081615"}
```

`metadata` ichida ham qabul qilinadi: `{"metadata": {"gift_id": "..."}}`.

* `sendGift` sovg'a narxini **botning Stars balansidan** yechadi — botda
  yetarli Stars bo'lishi kerak.
* `gift_id` yo'q bo'lsa yoki Telegram xato qaytarsa, buyurtma adminga
  yuboriladi (qo'lda yetkazish) va foydalanuvchi chekni oladi.

`gift_id` larni `getAvailableGifts` orqali bilib olish mumkin. Qiymatni
admin panel orqali kiriting: **🛍 Mahsulotlar** → mahsulot kartasi →
**🎁 Gift ID** (yoki yangi mahsulot qo'shishda 4-qadam). SQL shart emas.

## Supabase ulanishi

* `?sslmode=require` connection string'i avtomatik `ssl="require"` ga
  aylantiriladi (asyncpg `sslmode` argumentini qabul qilmaydi).
* Transaction Pooler (`port 6543`) uchun `statement_cache_size=0`
  qo'llaniladi (PgBouncer prepared statement'larni qo'llab-quvvatlamaydi).
* Supabase hostida TLS avtomatik yoqiladi; lokal (SSL'siz) Postgres uchun
  `.env` da `DB_SSL=disable` qo'yiladi.

## Testlar

```bash
python -m pytest tests/ -q
```

Yangi imkoniyatlar testi:

```bash
python -m pytest tests/test_features_api94.py -q
```

Alohida tekshiruvlar (aiosqlite talab qiladi):

```bash
pip install aiosqlite
python tests/test_queries.py
```
