"""AI Trader — hakam xulosasi asosida avtomatik spot savdo.

Rejimlar:
  paper — sinov (qog'oz savdo): haqiqiy pulsiz, faqat yozib boriladi
  real  — Binance hisobida haqiqiy market buyurtma (kalitda faqat savdo ruxsati bo'lsin!)

KIRISH qarorini AI qiladi (tahlildan keyin chaqiriladi):
  BUY  → quyidagi barcha shartlar bajarilsa pozitsiya ochadi
  SELL → shu koinda ochiq pozitsiya bo'lsa — yopadi
  WAIT → hech narsa qilmaydi

Kirish shartlari (hammasi bajarilishi shart):
  - hakam stop narxini bergan bo'lsin (stopsiz savdo yo'q)
  - hakam kamida bitta maqsad bergan bo'lsin
  - xatar "yuqori" bo'lmasin
  - ishonch darajasi trader_min_conf dan kam bo'lmasin
  - shu koinda ochiq pozitsiya bo'lmasin
  - ochiq pozitsiyalar soni trader_max_open dan kam bo'lsin
  - shu koin sovish muddatida bo'lmasin (trader_cooldown_h)
  - bugun trader_max_stops martadan ko'p stop ishlamagan bo'lsin
  - kunlik savdolar soni trader_max_daily dan oshmagan bo'lsin

CHIQISH qarorini raqamlar qiladi (watch() har necha daqiqada):
  narx stopga tushdi    → yopadi (close_reason='stop')
  narx 1-maqsadga yetdi → to'liq yopadi (close_reason='target')

Faqat spot. Short yo'q.
"""
import os
import json
import ccxt
from datetime import datetime, timedelta, timezone

import database as db
import market_data
import ai_council

# Haqiqiy rejimda sotishda miqdorni shu koeffitsientga ko'paytiramiz.
# Sabab: Binance xarid komissiyasini koinning o'zidan ushlab qoladi,
# shuning uchun hisobda sotib olingan miqdordan sal kamroq qoladi.
SELL_SAFETY = 0.998


def _real_exchange():
    key = os.environ.get("BINANCE_API_KEY")
    secret = os.environ.get("BINANCE_SECRET")
    if not key or not secret:
        raise RuntimeError("BINANCE_API_KEY / BINANCE_SECRET sozlanmagan")
    return ccxt.binance({"apiKey": key, "secret": secret, "enableRateLimit": True})


def test_connection():
    """Binance kalitlarini tekshiradi (faqat balans o'qish)."""
    try:
        ex = _real_exchange()
        bal = ex.fetch_balance()
        usdt = bal.get("USDT", {}).get("free", 0)
        return {"ok": True, "usdt_free": usdt}
    except Exception as e:
        msg = str(e)
        for env in ("BINANCE_API_KEY", "BINANCE_SECRET"):
            v = os.environ.get(env)
            if v:
                msg = msg.replace(v, "***")
        return {"ok": False, "error": msg[:300]}


def _num(x):
    """Xavfsiz songa aylantirish: None, bo'sh satr, matn — hammasi None bo'ladi."""
    try:
        v = float(x)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def _qty_of(tr):
    info = {}
    try:
        info = json.loads(tr["order_info"] or "{}")
    except Exception:
        pass
    return info.get("qty") or (tr["amount_usd"] / tr["entry_price"])


def _sell_real(coin, qty, price):
    """Haqiqiy rejimda sotish. Qaytadi: bajarilgan narx."""
    ex = _real_exchange()
    order = ex.create_order(f"{coin}/USDT", "market", "sell", qty * SELL_SAFETY)
    return order.get("average") or price


# ---------- Kirish/chiqish qarori (tahlildan keyin chaqiriladi) ----------

def process(coin, verdict, price, analysis_id, session="swing"):
    """verdict — hakamning to'liq JSON xulosasi (dict) yoki None.

    session — "swing" (bosh tahlil) yoki "fast" (tezkor, mustaqil savdo).
    Ikkalasi butunlay alohida: alohida limitlar, alohida sovish muddati,
    alohida ochiq pozitsiya hisobi. Faqat Binance hisobi (mode) umumiy.

    Natija: nima qilingani (yoki nega qilinmagani) haqida qisqa matn, yoki None.
    """
    s = db.all_settings()
    if s.get("trader_enabled") != "1":
        return None
    if not verdict or not price:
        return None

    action = verdict.get("action")
    if action not in ("BUY", "SELL"):
        conf = _num(verdict.get("confidence")) or 0
        return f"{coin}: hakam KUTISH dedi (ishonch {conf:.0f}%) — harakat qilinmadi"

    mode = s.get("trader_mode", "paper")
    fee = float(s.get("fee_rate", "0.1"))
    open_tr = db.ai_open_for_coin(coin, mode, session=session)

    # ---------- SELL ----------
    if action == "SELL":
        if not open_tr:
            return None
        exit_price = _sell_real(coin, _qty_of(open_tr), price) if mode == "real" else price
        db.ai_close(open_tr["id"], exit_price, reason="ai")
        return f"{coin}: AI savdo yopildi (hakam 'Sot' dedi)"

    # ---------- BUY ----------
    pre = "trader_fast_" if session == "fast" else "trader_"
    max_usd = float(s.get(pre + "max_usd", "20"))
    max_daily = int(s.get(pre + "max_daily", "3"))
    min_conf = float(s.get(pre + "min_conf", "70"))
    max_open = int(s.get(pre + "max_open", "2"))
    cooldown_h = float(s.get(pre + "cooldown_h", "24"))
    max_stops = int(s.get(pre + "max_stops", "2"))

    stop = _num(verdict.get("stop"))
    targets = [t for t in (_num(x) for x in (verdict.get("targets") or [])) if t]
    conf = _num(verdict.get("confidence")) or 0
    risk = (verdict.get("risk") or "").strip().lower()

    if open_tr:
        return f"{coin}: pozitsiya allaqachon ochiq — o'tkazib yuborildi"
    if stop is None:
        return f"{coin}: hakam himoya narxi (stop) bermadi — savdo ochilmadi"
    if stop >= price:
        return f"{coin}: stop joriy narxdan yuqori — savdo ochilmadi"
    if not targets:
        return f"{coin}: hakam maqsad bermadi — savdo ochilmadi"
    if risk == "yuqori":
        return f"{coin}: xatar yuqori — savdo ochilmadi"
    if conf < min_conf:
        return f"{coin}: ishonch {conf:.0f}% (kerak: {min_conf:.0f}%) — savdo ochilmadi"
    if db.ai_open_count(mode, session=session) >= max_open:
        return f"{coin}: ochiq pozitsiyalar limiti ({max_open}) to'lgan — savdo ochilmadi"
    if db.ai_stops_today(mode, session=session) >= max_stops:
        return f"{coin}: bugun {max_stops} marta stop ishladi — ertagacha to'xtatildi"
    if db.ai_trades_today(mode, session=session) >= max_daily:
        return f"{coin}: kunlik savdo limiti to'lgan — o'tkazib yuborildi"

    last_closed = db.ai_last_closed_at(coin, mode, session=session)
    if last_closed and cooldown_h > 0:
        try:
            t = datetime.fromisoformat(last_closed)
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            left = (t + timedelta(hours=cooldown_h)) - datetime.now(timezone.utc)
            if left.total_seconds() > 0:
                return (f"{coin}: sovish muddati tugamagan "
                        f"({left.total_seconds() / 3600:.1f} soat qoldi) — savdo ochilmadi")
        except Exception:
            pass

    # Barcha shartlar bajarildi — pozitsiya ochamiz
    if mode == "real":
        ex = _real_exchange()
        order = ex.create_order(f"{coin}/USDT", "market", "buy", None, None,
                                {"quoteOrderQty": max_usd})
        entry = order.get("average") or price
        cost = order.get("cost") or max_usd
        qty = order.get("filled") or (cost / entry)
        db.ai_open(coin, entry, cost, fee, mode, analysis_id,
                   json.dumps({"qty": qty, "order_id": order.get("id")}),
                   stop=stop, targets=targets, session=session)
    else:
        db.ai_open(coin, price, max_usd, fee, mode, analysis_id, None,
                   stop=stop, targets=targets, session=session)

    return (f"{coin}: AI savdo ochildi ({'haqiqiy' if mode == 'real' else 'sinov'}, "
            f"${max_usd:g}, stop ${stop:g}, maqsad ${targets[0]:g})")


# ---------- Kuzatuvchi (AI ishlatilmaydi) ----------

def watch():
    """Ochiq pozitsiyalarni stop va maqsad bo'yicha tekshiradi.

    Ikkala rejim ham kuzatiladi — rejim almashtirilsa, eski ochiq
    pozitsiyalar himoyasiz qolmasligi uchun.
    Qaytadi: bajarilgan ishlar ro'yxati (matnlar).
    """
    trades = [t for t in db.ai_open_trades() if t.get("stop") or t.get("targets")]
    if not trades:
        return []

    pairs = sorted({f"{t['coin']}/USDT" for t in trades})
    prices = {}
    try:
        tickers = market_data.exchange().fetch_tickers(pairs)
        for p, t in tickers.items():
            if t.get("last"):
                prices[p.split("/")[0]] = t["last"]
    except Exception:
        for p in pairs:                       # guruh so'rovi ishlamasa — bittalab
            try:
                prices[p.split("/")[0]] = market_data.exchange().fetch_ticker(p)["last"]
            except Exception:
                continue

    logs = []
    for tr in trades:
        price = prices.get(tr["coin"])
        if not price:
            continue
        stop = _num(tr.get("stop"))
        try:
            targets = json.loads(tr.get("targets") or "[]")
        except Exception:
            targets = []
        target = _num(targets[0]) if targets else None

        if stop and price <= stop:
            reason = "stop"
        elif target and price >= target:
            reason = "target"
        else:
            continue

        try:
            exit_price = _sell_real(tr["coin"], _qty_of(tr), price) if tr["mode"] == "real" else price
            db.ai_close(tr["id"], exit_price, reason=reason)
            word = "himoya narxi ishladi" if reason == "stop" else "maqsadga yetdi"
            logs.append(f"{tr['coin']}: {word}, ${exit_price:g} da yopildi")
        except Exception as e:
            logs.append(f"{tr['coin']}: yopib bo'lmadi — {str(e)[:120]}")
    return logs


def close_manual(trade_id, current_price):
    """Sahifadan qo'lda yopish."""
    tr = db.ai_trade_by_id(trade_id)
    if not tr or tr["exit_price"]:
        raise RuntimeError("Savdo topilmadi yoki allaqachon yopilgan")
    exit_price = (_sell_real(tr["coin"], _qty_of(tr), current_price)
                  if tr["mode"] == "real" else current_price)
    db.ai_close(trade_id, exit_price, reason="manual")


# ---------- Tezkor savdo (skalping/kun ichi) — bosh tahlildan mustaqil ----------

def run_fast_cycle():
    """Bosh (swing) tahlildan butunlay mustaqil tsikl. Har chaqirilganda:
    bo'sh joy bor koinlarni qisqa muddatli ma'lumot bilan tahlil qiladi va
    process(..., session="fast") orqali qaror qabul qiladi.

    Faqat 'trader_enabled' VA 'trader_fast_enabled' ikkalasi ham yoniq bo'lsa ishlaydi.
    Qaytadi: bajarilgan ishlar haqida matnlar ro'yxati.
    """
    s = db.all_settings()
    if s.get("trader_enabled") != "1" or s.get("trader_fast_enabled") != "1":
        return []

    mode = s.get("trader_mode", "paper")
    max_open = int(s.get("trader_fast_max_open", "2"))
    if db.ai_open_count(mode, session="fast") >= max_open:
        return ["Tezkor: ochiq pozitsiyalar limiti to'lgan — bu safar tahlil qilinmadi"]

    coins = db.list_coins(auto_only=True)
    if not coins:
        return []

    profile = s.get("profile", "")
    try:
        enabled = json.loads(s.get("ai_enabled", "{}"))
    except Exception:
        enabled = {}
    analysts = [a for a, on in enabled.items() if on] or ["qwen", "deepseek"]

    logs = []
    for coin in coins:
        if db.ai_open_count(mode, session="fast") >= max_open:
            break   # navbatdagi koinlarni tekshirishning ma'nosi yo'q — joy qolmadi
        if db.ai_open_for_coin(coin, mode, session="fast"):
            continue   # bu koinda allaqachon tezkor pozitsiya ochiq

        try:
            snapshot = market_data.move_snapshot(coin)
            price = market_data.exchange().fetch_ticker(f"{coin}/USDT")["last"]
        except Exception as e:
            logs.append(f"{coin}: ma'lumot olinmadi — {str(e)[:100]}")
            continue

        result = ai_council.run_council(snapshot, profile, horizon="fast", analysts=analysts)
        aid = db.save_analysis(coin, result["summary"], result["discussion"],
                               mode="auto", kind="fast", price=price)
        if result.get("verdict"):
            log = process(coin, result["verdict"], price, aid, session="fast")
            if log:
                logs.append(log)
    return logs
