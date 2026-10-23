"""
services/card_payment.py
========================
Karta to'lovlari jurnalidan summani aniqlash (Bot API 9.4 karta tizimi).

Nega kerak:
 - Foydalanuvchi karta orqali to'ldirishda summa `stars * SOM_PER_STAR` ga
   **40..100 so'm unikal kod** qo'shib hisoblanadi (masalan 30 000 -> 30 047).
   Shu kod orqali to'lovni aynan qaysi foydalanuvchiga tegishli ekanini
   bilib olamiz.
 - `@CardXabarBot` / `@HUMOcardbot` yuboradigan xabarlarda summa turli
   formatlarda keladi: `10 037`, `10 037 so'm`, `10037`, `10,037`,
   `10.037`, NBSP (`1 000 037`) va h.k. — barchasi bitta songa keltirilishi
   kerak.

Modul faqat matn bilan ishlaydi (DB/aiogram import qilmaydi) — testlash oson.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# Summani ajratish: 3 xonali guruhlar bo'shliq (oddiy, NBSP, thin space),
# apostrof, vergul yoki nuqta bilan ajratilgan bo'lishi mumkin.
# Misollar: "10 037", "10037", "10,037", "10.037", "1\u00a0000\u202f037"
_GROUPED = r"\d{1,3}(?:[\s\u00a0\u202f\u2009'’,.]\d{3})+"
_PLAIN = r"\d+"
_AMOUNT_RE = re.compile(rf"(?:{_GROUPED}|{_PLAIN})")

# Faqat raqam va guruh ajratgichlarini qoldiramiz
_SEPARATORS_RE = re.compile(r"[\s\u00a0\u202f\u2009'’,.]")


def extract_amounts(text: str | None) -> list[int]:
    """
    Matndagi barcha sonlarni (summalarni) butun son ko'rinishida qaytaradi.

    Tartib saqlanadi, takrorlar olib tashlanadi. Karta raqami kabi uzun
    raqamlar ham son sifatida qaytadi — moslashtirish `find_payment_amount`
    da aniq tenglik bo'yicha bo'ladi.
    """
    if not text:
        return []

    amounts: list[int] = []
    for match in _AMOUNT_RE.finditer(text):
        digits = _SEPARATORS_RE.sub("", match.group(0))
        if not digits.isdigit():
            continue
        value = int(digits)
        if value not in amounts:
            amounts.append(value)
    return amounts


def find_payment_amount(
    text: str | None, candidates: Iterable[int]
) -> int | None:
    """
    Matndagi summalar orasidan `candidates` ga mos keladiganini topadi.

    Returns:
        Mos keladigan yagona summa yoki `None` (moslik yo'q yoki bir nechta
        nomzod birdan uchragan — noaniq holatda balans qo'shilmaydi).
    """
    wanted = {int(value) for value in candidates if value}
    if not wanted or not text:
        return None

    matched = [amount for amount in extract_amounts(text) if amount in wanted]
    if len(matched) != 1:
        return None
    return matched[0]


def format_som(value: int) -> str:
    """
    Summani o'qishga qulay ko'rinishda qaytaradi: 10037 -> "10 037".
    """
    return f"{int(value):,}".replace(",", " ")


def extract_payment_text(message_text: str | None) -> str | None:
    """
    Xabar matnini tozalab qaytaradi (chek/xizmat xabarlari uchun).

    `None` yoki faqat bo'shliq bo'lsa — `None`.
    """
    text = (message_text or "").strip()
    return text or None
