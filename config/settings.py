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
        default=250,
        description=(
            "Karta to'lovida 1 ⭐️ narxi (so'm). To'lov summasi "
            "`stars * SOM_PER_STAR + 1..99` unikal kod sifatida hisoblanadi"
        ),
        ge=1,
    )

    CARD_LOG_CHAT_ID: int = Field(
        default=0,
        description=(
            "Karta to'lovlari jurnali (yopiq guruh/kanal) ID. Shu chatga "
            "@CardXabarBot / @HUMOcardbot yuborgan to'lov xabarlari avtomatik "
            "o'qiladi. 0 — avtomatik tasdiqlash o'chirilgan (faqat chek + admin)"
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
