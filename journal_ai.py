"""Kripto jurnal — Binance skrinshotidan savdo ma'lumotini o'qish (Claude vision).

Faqat o'qiydi va JSON qaytaradi — hech narsani bazaga yozmaydi. Saqlash
foydalanuvchi tasdiqlagandan keyin main.py orqali amalga oshadi.
"""
import os
import re
import json
import requests

TIMEOUT = 60

SYSTEM = """Sen Binance ilovasining savdo tafsilotlari skrinshotidan (rus yoki ingliz
tilida bo'lishi mumkin) aniq raqamlarni o'qib, qat'iy JSON qaytaruvchi yordamchisan.
Faqat JSON qaytar — boshqa hech qanday matn, izoh yoki ``` belgilarisiz.

Skrinshotda odatda quyidagilar bo'ladi: juftlik (masalan SUI/USDT), turi
(Купить/Продать yoki Buy/Sell), miqdor (Количество), narx (Цена), jami summa
(Всего), komissiya (Комиссия — qaysi valyutada ekani ham muhim), sana-vaqt,
buyurtma raqami (№ ордера / Order No).

Qat'iy shu formatda qaytar:
{
  "side": "buy" yoki "sell",
  "coin": "SUI",
  "qty": 142.1,
  "price": 0.722,
  "usd_value": 102.5962,
  "fee": 0.1421,
  "fee_asset": "SUI",
  "order_id": "8810145231",
  "created_at": "2026-08-31T10:46:29",
  "confidence": "high" yoki "low"
}

Qoidalar:
- Raqamlarni skrinshotda aynan ko'ringanidek ol — o'zing hisoblama, taxmin qilma.
- "usd_value" — "Всего" / "Total" qatoridagi summa (USDT da).
- "coin" — juftlikning birinchi qismi (SUI/USDT -> "SUI"), USDT yozilmaydi.
- created_at ISO formatda: YYYY-MM-DDTHH:MM:SS.
- Agar biror maydonni aniq o'qiy olmasang — o'sha maydonni null qil va
  "confidence": "low" qo'y. Bu skrinshot emas, boshqa narsa bo'lsa yoki savdo
  ma'lumoti umuman topilmasa — barcha maydonlarni null qil."""


def _sanitize(text):
    key = os.environ.get("ANTHROPIC_API_KEY")
    return text.replace(key, "***") if key else text


def extract_trade(image_b64: str, media_type: str = "image/png") -> dict:
    """Rasmdan savdo ma'lumotini o'qiydi. Xatoda RuntimeError ko'taradi."""
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise RuntimeError("ANTHROPIC_API_KEY sozlanmagan")
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01"},
        json={
            "model": os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5"),
            "max_tokens": 500,
            "system": SYSTEM,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "image",
                     "source": {"type": "base64", "media_type": media_type, "data": image_b64}},
                    {"type": "text", "text": "Shu skrinshotdagi savdo ma'lumotini JSON qilib ber."},
                ],
            }],
        }, timeout=TIMEOUT)
    if r.status_code >= 400:
        raise RuntimeError(_sanitize(f"HTTP {r.status_code}: {r.text[:300]}"))
    text = "".join(b.get("text", "") for b in r.json()["content"]).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        data = json.loads(text)
    except Exception:
        raise RuntimeError("AI javobini o'qib bo'lmadi: " + text[:200])
    return data
