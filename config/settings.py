"""
config/settings.py
===================
Loyiha konfiguratsiyasi: `.env` faylidagi barcha parametrlarni
`pydantic-settings` yordamida yuklaydi va tekshiradi.

Xususiyatlar:
 - Har bir o'zgaruvchi tip bilan e'lon qilingan (type-safe).
 - ADMIN_IDS: vergul bilan ajratilgan satrni `list[int]` ga o'tkazuvchi validator.
 - Singleton `settings` obyekti: butun loyiha bo'ylab bitta nusxa ishlatiladi.

Ishlatish:
    from config.settings import settings
    print(settings.BOT_TOKEN)
"""

from __future__ import annotations

import re
from functools import lru_cache

from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """
    `.env` faylidagi barcha parametrlarni yuklovchi asosiy konfiguratsiya modeli.

    pydantic-settings har bir maydon nomini `.env` dagi mos o'zgaruvchi bilan
    avtomatik ravishda moslashtiradi (case-insensitive).
    """

    # pydantic-settings konfiguratsiyasi:
    #  - env_file=".env" -> qo'shimcha .env faylini yuklaydi
    #  - extra="ignore" -> .env ichidagi notanish o'zgaruvchilarni e'tibors qoldiradi
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ------------------------- Telegram -------------------------
    BOT_TOKEN: str = Field(
        ...,
        description="BotFather'dan olingan Telegram bot tokeni",
    )

    # ADMIN_IDS asosiy sifatida str bo'lib keladi, keyin validator orqali
    # list[int] ga o'tkaziladi (quyida field_validator ko'rsatilgan).
    #
    # NoDecode (pydantic-settings >= 2.3): murakkab tipni .env dan JSON emas,
    # xom satr sifatida qabul qilishga majbur qiladi. Aks holda pydantic-settings
    # "111,222" ni JSON deb hisoblab, field_validator dan oldin xato berardi.
    ADMIN_IDS: Annotated[list[int], NoDecode] = Field(
        ...,
        description="Admin Telegram IDlari ro'yxati (vergul bilan ajratilgan)",
    )

    # ------------------------- Bazа -----------------------------
    DATABASE_URL: str = Field(
        ...,
        description="Supabase PostgreSQL connection string",
    )

    DB_SSL: str = Field(
        default="auto",
        description=(
            "PostgreSQL SSL (TLS) rejimi: 'auto' (Supabase/URL bo'yicha "
            "avtomatik), 'require' (majburiy TLS) yoki 'disable'"
        ),
    )

    # ----------------------- Stars / Ekonomika ------------------
    REFERRAL_REWARD_STARS: int = Field(
        default=10,
        description="Referal taklif uchun beriladigan yutuq (Telegram Stars)",
        ge=0,
    )

    MIN_WITHDRAWAL_STARS: int = Field(
        default=50,
        description="Minimal yechib olish miqdori (Telegram Stars)",
        ge=1,
    )

    SOM_PER_STAR: int = Field(
        default=300,
        description=(
            "Karta to'lovida 1 ⭐️ narxi (so'm) — admin panelda ham "
            "o'zgartiriladi. To'lov summasi `stars * SOM_PER_STAR + 1..99` "
            "tasodifiy unikal kod sifatida hisoblanadi"
        ),
        ge=1,
    )

    CARD_NUMBER: str = Field(
        default="9860170111243573",
        description="To'lov kartasi raqami (HUMO) — bazadagi sozlama bo'lmasa ishlatiladi",
    )

    CARD_HOLDER: str = Field(
        default="Murodjon Amirov",
        description="To'lov kartasi egasining F.I.SH. — bazadagi sozlama bo'lmasa",
    )

    DEPOSIT_TTL_SECONDS: int = Field(
        default=900,
        description=(
            "Karta to'lovi so'rovining amal qilish muddati (soniya). "
            "Standart 900 = 15 daqiqa; muddat o'tsa so'rov 'expired' bo'ladi"
        ),
        ge=60,
        le=86_400,
    )

    # ------------------ Telethon userbot (@humocardbot) ----------
    TELEGRAM_API_ID: int = Field(
        default=0,
        description=(
            "Telethon userbot uchun API ID (my.telegram.org). 0 bo'lsa "
            "userbot ishga tushmaydi — bot guruh/kanal jurnali bilan ishlaydi"
        ),
        ge=0,
    )

    TELEGRAM_API_HASH: str = Field(
        default="",
        description="Telethon userbot uchun API HASH (my.telegram.org)",
    )

    TELEGRAM_SESSION_STRING: str = Field(
        default="",
        description=(
            "Telethon StringSession (shaxsiy profil sessiyasi). Bo'sh bo'lsa "
            "userbot ishga tushmaydi"
        ),
    )

    HUMOCARD_BOT_USERNAME: str = Field(
        default="humocardbot",
        description=(
            "To'lov xabarlarini yuboradigan bot username'i (Telethon shu "
            "botdan kelgan xabarlarni eshitadi)"
        ),
    )

    CARD_LOG_CHAT_ID: int = Field(
        default=0,
        description=(
            "Karta to'lovlari jurnali (yopiq guruh/kanal) ID. Shu chatga "
            "@CardXabarBot / @HUMOcardbot yuborgan to'lov xabarlari avtomatik "
            "o'qiladi. 0 — avtomatik tasdiqlash o'chirilgan (faqat chek + admin)"
        ),
    )

    # --------------------- HUMO karta (env'dan, hardcode YO'Q) -----------------
    # Bu o'zgaruvchilar .env orqali (pydantic-settings = os.getenv'ga teng)
    # yuklanadi; karta ma'lumotlari hech qachon .py fayl ichiga yozilmaydi.
    HUMO_CARD_NUMBER: str = Field(
        default="",
        description=(
            "HUMO karta raqami (faqat .env'da: HUMO_CARD_NUMBER). Bo'sh bo'lsa "
            "CARD_NUMBER qiymati ishlatiladi"
        ),
    )

    HUMO_CARD_HOLDER: str = Field(
        default="",
        description=(
            "HUMO karta egasi F.I.SH. (faqat .env'da: HUMO_CARD_HOLDER). Bo'sh "
            "bo'lsa CARD_HOLDER qiymati ishlatiladi"
        ),
    )

    # ------------------- Telethon (humo_listener uchun aliaslar) ---------------
    TG_API_ID: int = Field(
        default=0,
        description=(
            "Telethon API ID (my.telegram.org). .env'dan o'qiladi; bo'sh bo'lsa "
            "TELEGRAM_API_ID qiymati ishlatiladi"
        ),
        ge=0,
    )

    TG_API_HASH: str = Field(
        default="",
        description=(
            "Telethon API HASH (my.telegram.org). .env'dan o'qiladi; bo'sh "
            "bo'lsa TELEGRAM_API_HASH qiymati ishlatiladi"
        ),
    )

    TG_SESSION_NAME: str = Field(
        default="humo_listener_session",
        description=(
            "Telethon sessiya fayli nomi (*.session .gitignore'da). Bo'sh bo'lsa "
            "standart 'humo_listener_session' ishlatiladi"
        ),
    )

    # ------------------------- Webhook / Server -----------------
    BOT_MODE: str = Field(
        default="webhook",
        description=(
            "Ish rejimi: 'webhook' (Render/production — FastAPI) yoki "
            "'polling' (lokal ishlash uchun, webhook URL talab qilmaydi)"
        ),
    )

    WEBHOOK_URL: str = Field(
        default="",
        description=(
            "Telegram webhook manzili (masalan, https://bot.onrender.com). "
            "BOT_MODE=webhook bo'lganda majburiy."
        ),
    )

    WEBHOOK_PATH: str = Field(
        default="/webhook",
        description="Webhook endpoint path'i",
    )

    WEBHOOK_SECRET: str = Field(
        default="",
        description=(
            "Telegram secret_token: webhook so'rovlari shu sarlavha bilan "
            "imzolanadi (ixtiyoriy, lekin production'da tavsiya etiladi)"
        ),
    )

    PORT: int = Field(
        default=8080,
        description="Server tinglaydigan port",
        ge=1,
        le=65535,
    )

    # ------------------------ Yordamchi xossalar ----------------
    @property
    def USERBOT_ENABLED(self) -> bool:
        """Telethon userbot uchun barcha majburiy qiymatlar kiritilganmi."""
        return bool(
            self.TELEGRAM_API_ID
            and self.TELEGRAM_API_HASH.strip()
            and self.TELEGRAM_SESSION_STRING.strip()
        )

    # Faol qiymat: HUMO_CARD_* bustir bo'lsa CARD_* (env) qaytadi —
    # karta ma'lumotlarining yagona manbasi faqat .env (os.getenv'e teng).
    @property
    def CARD_NUMBER_ACTIVE(self) -> str:
        """Karta raqami: HUMO_CARD_NUMBER -> CARD_NUMBER (env backup)."""
        return (self.HUMO_CARD_NUMBER or self.CARD_NUMBER).strip()

    @property
    def CARD_HOLDER_ACTIVE(self) -> str:
        """Karta egasi F.I.SH.: HUMO_CARD_HOLDER -> CARD_HOLDER (env backup)."""
        return (self.HUMO_CARD_HOLDER or self.CARD_HOLDER).strip()

    @property
    def TG_API_ID_ACTIVE(self) -> int:
        """Telethon API ID: TG_API_ID -> TELEGRAM_API_ID (env backup)."""
        return self.TG_API_ID or self.TELEGRAM_API_ID

    @property
    def TG_API_HASH_ACTIVE(self) -> str:
        """Telethon API HASH: TG_API_HASH -> TELEGRAM_API_HASH (env backup)."""
        return (self.TG_API_HASH or self.TELEGRAM_API_HASH).strip()

    # ------------------------ Validatorlar ----------------------
    @field_validator("ADMIN_IDS", mode="before")
    @classmethod
    def _parse_admin_ids(cls, value: object) -> list[int]:
        """
        ADMIN_IDS ni `list[int]` holatiga o'tkazadi.

        Qabul qiladigan formatlar:
          - "123456789,987654321"  -> [123456789, 987654321]
          - "123456789"            -> [123456789]
          - [123456789, 987654321]  -> [123456789, 987654321] (allaqachon ro'yxat)
          - 123456789              -> [123456789] (yakka son)

        Bo'sh qatorlar va bo'shliqlar avtomatik tozalanadi.
        """
        # 1) Allaqachon ro'yxat bo'lsa - faqat int ga tekshiramiz
        if isinstance(value, list):
            return [int(item) for item in value]

        # 2) Yakka son bo'lsa - bitta elementli ro'yxatga aylantiramiz
        if isinstance(value, int):
            return [value]

        # 3) Satr bo'lsa - vergul bo'yicha ajratamiz va int ga o'tkazamiz
        if isinstance(value, str):
            parts = [part.strip() for part in value.split(",")]
            return [int(part) for part in parts if part]

        # 4) Boshqa formatlar uchun xato qaytaramiz
        raise TypeError(
            f"ADMIN_IDS noto'g'ri formatda: {value!r}. "
            "Masalan: '123456789,987654321' kutilmoqda."
        )

    @field_validator("WEBHOOK_PATH")
    @classmethod
    def _validate_webhook_path(cls, value: str) -> str:
        """Webhook path majburan `/` bilan boshlanishi kerakligini ta'minlaydi."""
        if not value.startswith("/"):
            return f"/{value}"
        return value

    @field_validator("BOT_MODE")
    @classmethod
    def _validate_bot_mode(cls, value: str) -> str:
        """`BOT_MODE` faqat `webhook` yoki `polling` bo'lishi mumkin."""
        mode = (value or "").strip().lower()
        if mode not in {"webhook", "polling"}:
            raise ValueError(
                f"BOT_MODE noto'g'ri: {value!r}. 'webhook' yoki 'polling' kutilmoqda."
            )
        return mode

    @field_validator("WEBHOOK_URL")
    @classmethod
    def _normalize_webhook_url(cls, value: str) -> str:
        """
        Webhook URL oxiridagi `/` ni olib tashlaydi (path bilan qo'shilganda
        `//webhook` hosil bo'lmasligi uchun) va formatini tekshiradi.
        """
        url = (value or "").strip().rstrip("/")
        if url and not url.startswith(("https://", "http://")):
            raise ValueError(
                "WEBHOOK_URL http:// yoki https:// bilan boshlanishi kerak "
                f"(olingan: {url!r})."
            )
        return url

    @field_validator("WEBHOOK_SECRET")
    @classmethod
    def _validate_webhook_secret(cls, value: str) -> str:
        """Telegram secret_token talabi: 1-256 belgi, A-Za-z0-9_- dan iborat."""
        secret = (value or "").strip()
        if not secret:
            return ""
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", secret):
            raise ValueError(
                "WEBHOOK_SECRET faqat A-Za-z0-9_- belgilaridan iborat bo'lishi "
                "va 1-256 belgi oralig'ida bo'lishi kerak."
            )
        return secret


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Singleton factory: `Settings` obyektini bir marta yaratadi va keshlaydi.

    lru_cache(maxsize=1) tufayli bir nechta chaqiruv ham doim bitta obyekt qaytaradi.
    """
    return Settings()  # type: ignore[call-arg]


# Butun loyiha bo'ylab ishlatiladigan bitta singleton obyekt.
# import qilish: from config.settings import settings
settings = get_settings()
