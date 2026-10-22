# Starssavdo — @nftyutbot

Telegram Stars, Premium, NFT, Mayda Giftlar va referal bonuslari uchun
aiogram 3.x boti. Supabase (PostgreSQL) + Render.com (Free Web Service) uchun
tayyor.

## Tuzilma

| Papka | Vazifasi |
|---|---|
| `main.py` | Kirish nuqtasi: health check serveri + polling (24/7) |
| `config/settings.py` | `.env` dan konfiguratsiya (pydantic-settings) |
| `database/connection.py` | SQLAlchemy async engine + Supabase pool sozlamalari |
| `database/models.py`, `database/queries.py` | Jadval modellari va repository qatlami |
| `handlers/user/` | `/start`, obuna, do'kon, referal, yechib olish |
| `handlers/admin/` | Admin panel: statistika, kanallar, rassilka, foydalanuvchilar, yechim |
| `middlewares/` | Throttling + majburiy obuna tekshiruvi |
| `states/` | FSM holatlari |
| `services/` | To'lov (XTR invoice) va kanal obunasi servislari |

## Lokal ishga tushirish

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # qiymatlarni to'ldiring
python main.py
```

Bot tokeni BotFather'dan, baza connection string'i esa Supabase
Dashboard -> Connect -> Session pooler orqali olinadi.

## Render.com da 24/7 ishga tushirish

1. **Render Dashboard -> New -> Blueprint** -> repozitoriy:
   `https://github.com/revilend/Starssavdo`. `render.yaml` fayli
   `nftyutbot` nomli **free** web service'ini yaratadi.
2. **Environment** bo'limida `BOT_TOKEN`, `ADMIN_IDS`, `DATABASE_URL`
   qiymatlarini kiriting (`sync: false` — Render o'zi so'raydi).
3. `PYTHON_VERSION` `3.11.9` bo'ladi, build: `pip install -r requirements.txt`,
   start: `python main.py`.

### Health check

Render "Free" web service uchun doimiy ochiq HTTP port talab qiladi.
`main.py` ichida yengil `aiohttp` serveri ishlaydi:

| Manzil | Javob |
|---|---|
| `GET /` | `200 OK` — `{"status": "ok", "bot": "@nftyutbot"}` |
| `GET /health` | `200 OK` — `{"status": "ok", "bot": "@nftyutbot"}` |

Server `0.0.0.0:$PORT` da (Render `PORT` ni o'zi beradi, default 8080)
ishlaydi va polling bilan parallel ravishda javob beradi. UptimeRobot
har 5 daqiqada `https://<service>.onrender.com/health` manziliga ping
yuborsa, Render service'ini uyg'oq holda saqlaydi.

### Supabase ulanishi

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
