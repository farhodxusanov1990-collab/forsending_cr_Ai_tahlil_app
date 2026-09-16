"""Telegram bildirishnomalari — botga /start bergan har bir foydalanuvchiga yuboriladi.

Sozlash: faqat @BotFather'dan olingan TELEGRAM_BOT_TOKEN kerak — chat_id qidirish
shart emas. Bot vaqti-vaqti bilan (rejalashtiruvchi orqali) "menga kim yozdi" deb
so'rab turadi (poll_updates()) va yangi yozganlarni eslab qoladi. Signal chiqqanda
ro'yxatdagilarning HAMMASIGA yuboriladi.
"""
import os
import re
import requests

import database as db

TIMEOUT = 15


def _token():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN sozlanmagan")
    return token


def _send_to(token, chat_id, text):
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
              "disable_web_page_preview": True},
        timeout=TIMEOUT)
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")


def poll_updates():
    """Telegram'dan yangi xabarlarni so'raydi (/start va boshqa har qanday xabar).
    Yangi yozgan har kimni bazaga qo'shadi va tabriklaydi. Qaytadi: yangi
    qo'shilganlar ro'yxati (ism/username)."""
    token = _token()
    offset = int(db.get_setting("telegram_update_offset", "0"))
    r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates",
                     params={"offset": offset, "timeout": 0}, timeout=TIMEOUT)
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
    updates = r.json().get("result", [])
    if not updates:
        return []

    new_users = []
    max_id = offset - 1
    for u in updates:
        max_id = max(max_id, u["update_id"])
        msg = u.get("message")
        if not msg:
            continue
        chat = msg.get("chat", {})
        chat_id = chat.get("id")
        if not chat_id:
            continue
        username = chat.get("username") or chat.get("first_name") or str(chat_id)
        if db.telegram_chat_add(chat_id, username):
            new_users.append(username)
            try:
                _send_to(token, chat_id,
                        "✅ Ro'yxatdan o'tdingiz. Bosh tahlil \"Sotib ol\" desa, shu yerga xabar keladi.")
            except Exception:
                pass   # tabrik xabari ketmasa ham — ro'yxatga qo'shilgani muhim

    db.set_setting("telegram_update_offset", str(max_id + 1))
    return new_users


def send_message(text):
    """Ro'yxatdagi barcha foydalanuvchilarga yuboradi."""
    token = _token()
    chats = db.telegram_chats_list()
    if not chats:
        raise RuntimeError("Hali hech kim botga /start bermagan")
    sent, failed = 0, []
    for c in chats:
        try:
            _send_to(token, c["chat_id"], text)
            sent += 1
        except Exception as e:
            failed.append(f"{c.get('username') or c['chat_id']}: {str(e)[:80]}")
    if sent == 0:
        raise RuntimeError("Hech kimga yuborilmadi: " + "; ".join(failed[:3]))
    return {"sent": sent, "failed": failed}


def test_connection():
    """Tokenni tekshiradi, yangi /start berganlarni darhol tekshiradi, sinov xabari yuboradi."""
    try:
        token = _token()
        r = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=TIMEOUT)
        if r.status_code >= 400:
            return {"ok": False, "error": f"Token noto'g'ri (HTTP {r.status_code})"}
        bot_name = r.json().get("result", {}).get("username", "?")
        poll_updates()
        chats = db.telegram_chats_list()
        if not chats:
            return {"ok": False,
                    "error": (f"Bot @{bot_name} ishlayapti, lekin hali hech kim /start bermagan. "
                             "Telegram'da botni toping, /start bosing, so'ng qayta tekshiring.")}
        result = send_message(f"✅ Sinov xabari. Bot: @{bot_name}. Ro'yxatda {len(chats)} kishi.")
        return {"ok": True, "bot": bot_name, "users": len(chats), "sent": result["sent"]}
    except Exception as e:
        msg = str(e)
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        if token:
            msg = msg.replace(token, "***")
        return {"ok": False, "error": msg[:300]}


def _clean_comment(comment):
    """Rang teglarini Telegram uchun soddalashtiradi."""
    comment = re.sub(r"\[!\](.*?)\[/!\]", r"👉 \1", comment, flags=re.S)
    comment = re.sub(r"\[/?[-+~]\]", "", comment)
    return comment.strip()


def format_buy_alert(coin, v, price, kind="swing"):
    conf = round(float(v.get("confidence") or 0))
    risk = v.get("risk", "-")
    stop = v.get("stop")
    targets = v.get("targets") or []
    entry_lo, entry_hi = v.get("entry_low"), v.get("entry_high")
    label = "Tezkor savdo" if kind == "fast" else "Bosh tahlil"

    lines = [f"🟢 <b>{coin}/USDT — SOTIB OL</b>  <i>({label})</i>"]
    if price:
        lines.append(f"Joriy narx: ${price:g}")
    lines.append(f"Ishonch: {conf}% · Xatar: {risk}")
    if entry_lo and entry_hi:
        lines.append(f"Kirish zonasi: ${entry_lo:g} – ${entry_hi:g}")
    elif entry_lo or entry_hi:
        lines.append(f"Kirish zonasi: ${(entry_lo or entry_hi):g}")
    if stop:
        lines.append(f"Stop: ${stop:g}")
    if targets:
        lines.append("Maqsad: " + ", ".join(f"${t:g}" for t in targets))
    comment = _clean_comment(v.get("comment") or "")
    if comment:
        lines.append("")
        lines.append(comment[:500])
    return "\n".join(lines)
