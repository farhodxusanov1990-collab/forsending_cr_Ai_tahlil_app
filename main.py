"""Asosiy server: FastAPI + rejalashtiruvchi (APScheduler)."""
import os
import json
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

import database as db
import market_data
import ai_council
import trader
import news
import sentiment
import telegram_bot
import journal_ai

TZ = "Asia/Tashkent"


def now_hm():
    """Hozirgi vaqt, Toshkent bo'yicha, soat:daqiqa — jurnal xabarlarida ko'rsatish uchun."""
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo(TZ)).strftime("%H:%M")


def global_on():
    """Umumiy kalit. O'chiq bo'lsa — hech qanday tahlil, kuzatuvchi yoki savdo ishlamaydi."""
    return db.get_setting("global_enabled", "1") == "1"
_status = {"running": False, "last_error": None, "trader_log": None, "trader_fast_log": None,
           "telegram_log": None, "step": None, "coin": None, "step_i": 0, "step_n": 0}


def _set_step(text, coin=None, i=None, n=None):
    _status["step"] = text
    if coin is not None:
        _status["coin"] = coin
    if i is not None:
        _status["step_i"] = i
    if n is not None:
        _status["step_n"] = n
_lock = threading.Lock()


# ---------- Tahlil jarayoni ----------

def run_analysis(mode="auto", only_coin=None, kind="swing"):
    with _lock:
        if _status["running"]:
            return
        if not global_on():
            return
        _status["running"] = True
        _status["last_error"] = None
    try:
        if only_coin:
            coins = [only_coin]
        elif mode == "auto":
            coins = db.list_coins(auto_only=True)   # faqat avto-tahlil yoqilganlar
        else:
            coins = db.list_coins()                  # qo'lda "barchasi" — butun ro'yxat
        profile = db.get_setting("profile", "")
        try:
            enabled = json.loads(db.get_setting("ai_enabled", "{}"))
        except Exception:
            enabled = {}
        analysts = [a for a, on in enabled.items() if on] or ["qwen", "deepseek"]
        total = len(coins)
        for idx, coin in enumerate(coins, start=1):
            _set_step("Bozor ma'lumotlari olinmoqda…", coin=coin, i=idx, n=total)
            if kind == "long":
                snapshot = market_data.coin_snapshot_longterm(coin)
            else:
                snapshot = market_data.coin_snapshot(coin)
            sline = sentiment.snapshot_line()
            if sline:
                snapshot += "\n" + sline
            try:
                price = market_data.exchange().fetch_ticker(f"{coin}/USDT")["last"]
            except Exception:
                price = None
            _set_step(f"AI kengashi tahlil qilmoqda ({', '.join(analysts)})…")
            result = ai_council.run_council(snapshot, profile, horizon=kind, analysts=analysts,
                                            on_step=lambda t: _set_step(t))
            _set_step("Saqlanmoqda…")
            aid = db.save_analysis(coin, result["summary"], result["discussion"],
                                   mode=mode, kind=kind, price=price)
            # Telegram: swing tahlil "Sotib ol" desa — AI Trader yoqiq-yo'qligidan
            # qat'i nazar xabar yuboriladi (bu alohida, mustaqil xususiyat)
            if (kind == "swing" and result.get("verdict")
                    and result["verdict"].get("action") == "BUY"
                    and db.get_setting("telegram_enabled", "0") == "1"):
                try:
                    telegram_bot.send_message(
                        telegram_bot.format_buy_alert(coin, result["verdict"], price, kind="swing"))
                except Exception as te:
                    _status["telegram_log"] = f"Telegram xatosi: {ai_council._sanitize(str(te))[:200]}"
            # AI Trader: faqat swing xulosalar bo'yicha, va faqat tezkor savdo
            # o'chiq bo'lsa (tezkor yoniq bo'lsa — savdo undan boshqariladi)
            if (kind == "swing" and result.get("verdict")
                    and db.get_setting("trader_fast_enabled", "0") != "1"):
                try:
                    log = trader.process(coin, result["verdict"], price, aid, session="swing")
                    if log:
                        _status["trader_log"] = log
                        db.set_setting("trader_last_decision", json.dumps({
                            "coin": coin, "kind": kind, "mode": mode, "log": log,
                            "at": datetime.now(timezone.utc).isoformat()}))
                except Exception as te:
                    log = f"{coin}: AI Trader xatosi: {ai_council._sanitize(str(te))[:300]}"
                    _status["trader_log"] = log
                    db.set_setting("trader_last_decision", json.dumps({
                        "coin": coin, "kind": kind, "mode": mode, "log": log,
                        "at": datetime.now(timezone.utc).isoformat()}))
    except Exception as e:
        _status["last_error"] = ai_council._sanitize(str(e))
    finally:
        _status["running"] = False
        _status["step"] = None
        _status["coin"] = None


# ---------- "Nima bo'ldi?" jarayoni ----------

def run_whatsup(only_coin=None):
    with _lock:
        if _status["running"]:
            return
        if not global_on():
            return
        _status["running"] = True
        _status["last_error"] = None
    try:
        profile = db.get_setting("profile", "")
        try:
            enabled = json.loads(db.get_setting("ai_enabled", "{}"))
        except Exception:
            enabled = {}
        analysts = [a for a, on in enabled.items() if on] or ["qwen", "deepseek"]

        if only_coin:
            # Bitta koin bo'yicha
            coin = only_coin
            _set_step("Narx harakati va yangiliklar yig'ilmoqda…", coin=coin, i=1, n=1)
            move_data = market_data.move_snapshot(coin)
            headlines, news_note = news.get_headlines(coin)
            jpos = next((p for p in db.journal_positions_list() if p["coin"] == coin), None)
            open_ai = [t for t in db.list_ai_trades() if not t["exit_price"] and t["coin"] == coin]
            pos = []
            if jpos:
                pos.append(f"treyderning {coin} dan {jpos['qty']:g} ta bor (o'rtacha ${jpos['avg_price']:g} dan)")
            if open_ai:
                pos.append(f"AI Trader'ning {coin} bo'yicha ochiq pozitsiyasi bor")
            positions_note = ("Pozitsiyalar: " + "; ".join(pos)) if pos else "Ochiq pozitsiya yo'q."
            scope = "coin"
            try:
                price = market_data.exchange().fetch_ticker(f"{coin}/USDT")["last"]
            except Exception:
                price = None
        else:
            # Butun bozor bo'yicha
            coin = "BOZOR"
            _set_step("Butun bozor ma'lumotlari yig'ilmoqda…", coin=coin, i=1, n=1)
            move_data = market_data.market_overview(db.list_coins())
            headlines, news_note = news.get_headlines(None)
            open_all = [p["coin"] for p in db.journal_positions_list()]
            positions_note = (f"Treyderning portfelidagi koinlar: {', '.join(open_all)}"
                              if open_all else "Ochiq pozitsiya yo'q.")
            scope = "market"
            price = None

        sline = sentiment.snapshot_line()
        if sline:
            move_data += "\n" + sline

        _set_step(f"AI kengashi tahlil qilmoqda ({', '.join(analysts)})…")
        result = ai_council.run_flash(coin, move_data, headlines, news_note,
                                      positions_note, profile, analysts, scope=scope,
                                      on_step=lambda t: _set_step(t))
        _set_step("Saqlanmoqda…")
        db.save_analysis(coin, result["summary"], result["discussion"],
                         mode="manual", kind="news", price=price)
    except Exception as e:
        _status["last_error"] = ai_council._sanitize(str(e))
    finally:
        _status["running"] = False
        _status["step"] = None
        _status["coin"] = None


# ---------- Rejalashtiruvchi ----------

scheduler = BackgroundScheduler(timezone=TZ)


def run_watch():
    """AI Trader kuzatuvchisi: stop va maqsadlarni tekshiradi. AI ishlatilmaydi,
    shuning uchun tahlil qulfiga (_lock) bog'liq emas — parallel ishlayveradi."""
    if not global_on():
        return
    try:
        logs = trader.watch()
        if logs:
            _status["trader_log"] = " | ".join(logs)
    except Exception as e:
        _status["trader_log"] = f"Kuzatuvchi xatosi: {ai_council._sanitize(str(e))[:200]}"


def schedule_watch():
    """Kuzatuvchini qayta rejalashtiradi (oraliq sozlamada o'zgarsa)."""
    try:
        scheduler.remove_job("trader_watch")
    except Exception:
        pass
    if not global_on():
        return
    try:
        minutes = max(1, int(db.get_setting("trader_watch_min", "5")))
    except Exception:
        minutes = 5
    scheduler.add_job(run_watch, IntervalTrigger(minutes=minutes, timezone=TZ),
                      id="trader_watch", max_instances=1, coalesce=True)


_fast_lock = threading.Lock()
_fast_running = {"v": False}


def run_fast_trade():
    """Tezkor savdo tsikli — bosh tahlildan butunlay mustaqil. Bir vaqtda faqat
    bitta nusxa ishlaydi (rejalashtiruvchi va qo'lda tugma to'qnashmasligi uchun)."""
    if not global_on():
        return
    with _fast_lock:
        if _fast_running["v"]:
            return
        _fast_running["v"] = True
    try:
        logs = trader.run_fast_cycle()
        _status["trader_fast_log"] = (
            f"{now_hm()} — " + (" | ".join(logs) if logs else "koinlar tekshirildi, savdo ochilmadi"))
    except Exception as e:
        _status["trader_fast_log"] = f"{now_hm()} — xato: {ai_council._sanitize(str(e))[:200]}"
    finally:
        _fast_running["v"] = False


def schedule_fast():
    """Tezkor savdo tsiklini qayta rejalashtiradi. O'chiq bo'lsa — vazifani olib tashlaydi."""
    try:
        scheduler.remove_job("trader_fast")
    except Exception:
        pass
    if not global_on() or db.get_setting("trader_fast_enabled", "0") != "1":
        return
    try:
        minutes = max(1, int(db.get_setting("trader_fast_interval_min", "20")))
    except Exception:
        minutes = 20
    scheduler.add_job(run_fast_trade, IntervalTrigger(minutes=minutes, timezone=TZ),
                      id="trader_fast", max_instances=1, coalesce=True)


def run_telegram_poll():
    """Telegram'dan 'kim yozdi' deb so'raydi — yangi /start berganlarni ro'yxatga qo'shadi."""
    if not global_on() or db.get_setting("telegram_enabled", "0") != "1":
        return
    try:
        new_users = telegram_bot.poll_updates()
        if new_users:
            _status["telegram_log"] = f"{now_hm()} — yangi obunachi: " + ", ".join(new_users)
    except Exception as e:
        _status["telegram_log"] = f"Telegram so'rov xatosi: {ai_council._sanitize(str(e))[:150]}"


def schedule_telegram_poll():
    """Telegram so'rovini qayta rejalashtiradi. O'chiq bo'lsa — vazifani olib tashlaydi."""
    try:
        scheduler.remove_job("telegram_poll")
    except Exception:
        pass
    if not global_on() or db.get_setting("telegram_enabled", "0") != "1":
        return
    scheduler.add_job(run_telegram_poll, IntervalTrigger(minutes=1, timezone=TZ),
                      id="telegram_poll", max_instances=1, coalesce=True)


def reschedule():
    """Faqat tahlil vazifalarini qayta rejalashtiradi — kuzatuvchiga tegmaydi."""
    for key in ("schedule_morning", "schedule_evening"):
        try:
            scheduler.remove_job(key)
        except Exception:
            pass
    if not global_on() or db.get_setting("auto_enabled", "1") != "1":
        return
    for key in ("schedule_morning", "schedule_evening"):
        t = db.get_setting(key, "09:00")
        h, m = t.split(":")
        scheduler.add_job(run_analysis, CronTrigger(hour=int(h), minute=int(m), timezone=TZ),
                          id=key, kwargs={"mode": "auto"})


@asynccontextmanager
async def lifespan(app):
    db.init_db()
    reschedule()
    schedule_watch()
    schedule_fast()
    schedule_telegram_poll()
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="Kripto tahlil", lifespan=lifespan)


# ---------- Modellar ----------

class CoinIn(BaseModel):
    symbol: str

class FeedbackIn(BaseModel):
    applied: bool | None = None
    outcome: str | None = None       # 'good' | 'bad' | 'clear'
    note: str | None = None

class TradeOpenIn(BaseModel):
    coin: str
    entry_price: float
    amount_usd: float
    note: str | None = None
    analysis_id: int | None = None

class TradeCloseIn(BaseModel):
    exit_price: float


class JournalReadIn(BaseModel):
    image_base64: str
    media_type: str = "image/png"


class JournalConfirmIn(BaseModel):
    side: str                      # buy | sell | earn
    coin: str
    qty: float
    price: float = 0
    usd_value: float = 0
    fee: float = 0
    fee_asset: str | None = None
    order_id: str | None = None
    note: str | None = None
    created_at: str | None = None

class SettingsIn(BaseModel):
    fee_rate: str | None = None
    profile: str | None = None
    schedule_morning: str | None = None
    schedule_evening: str | None = None
    ai_enabled: str | None = None
    auto_enabled: str | None = None
    trader_enabled: str | None = None
    trader_mode: str | None = None
    trader_max_usd: str | None = None
    trader_max_daily: str | None = None
    trader_min_conf: str | None = None
    trader_max_open: str | None = None
    trader_cooldown_h: str | None = None
    trader_max_stops: str | None = None
    trader_watch_min: str | None = None
    trader_fast_enabled: str | None = None
    trader_fast_interval_min: str | None = None
    trader_fast_max_usd: str | None = None
    trader_fast_max_daily: str | None = None
    trader_fast_min_conf: str | None = None
    trader_fast_max_open: str | None = None
    trader_fast_cooldown_h: str | None = None
    trader_fast_max_stops: str | None = None
    global_enabled: str | None = None
    telegram_enabled: str | None = None
    telegram_notify_fast: str | None = None


class AiFeedbackIn(BaseModel):
    ai: str
    verdict: str        # 'good' | 'bad' | 'clear'


def period_since(period: str):
    now = datetime.now(timezone.utc)
    if period == "month":
        return (now - timedelta(days=30)).isoformat()
    if period == "year":
        return (now - timedelta(days=365)).isoformat()
    return None


# ---------- Sahifa ----------

@app.get("/")
def index():
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "index.html"))


@app.get("/manifest.json")
def manifest():
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "manifest.json"),
                        media_type="application/manifest+json")


@app.get("/sw.js")
def service_worker():
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "sw.js"),
                        media_type="application/javascript")


# ---------- Koinlar ----------

@app.get("/api/coins")
def get_coins():
    return db.list_coins_full()


class AutoIn(BaseModel):
    auto: bool


@app.post("/api/coins/{symbol}/auto")
def post_coin_auto(symbol: str, body: AutoIn):
    db.set_coin_auto(symbol.upper(), body.auto)
    return {"ok": True}


class StarredIn(BaseModel):
    starred: bool


@app.post("/api/coins/{symbol}/starred")
def post_coin_starred(symbol: str, body: StarredIn):
    db.set_coin_starred(symbol.upper(), body.starred)
    return {"ok": True}


class ReorderIn(BaseModel):
    symbols: list[str]


@app.post("/api/coins/reorder")
def post_coins_reorder(body: ReorderIn):
    db.reorder_coins(body.symbols)
    return {"ok": True}


@app.post("/api/coins")
def post_coin(body: CoinIn):
    sym = body.symbol.strip().upper()
    if not sym.isalnum():
        raise HTTPException(400, "Noto'g'ri belgi")
    if not market_data.symbol_exists(sym):
        raise HTTPException(404, f"{sym}/USDT juftligi Binance'da topilmadi")
    db.add_coin(sym)
    return {"ok": True}


@app.delete("/api/coins/{symbol}")
def del_coin(symbol: str):
    db.remove_coin(symbol.upper())
    return {"ok": True}


# ---------- Tahlillar ----------

@app.get("/api/analyses")
def get_analyses(limit: int = 30, offset: int = 0, kind: str | None = None):
    return db.list_analyses(limit, offset, kind)


@app.post("/api/analyze")
def post_analyze(background: BackgroundTasks, coin: str | None = None, kind: str = "swing"):
    if not global_on():
        raise HTTPException(400, "Umumiy kalit o'chiq — avval yoqing")
    if _status["running"]:
        raise HTTPException(409, "Tahlil allaqachon ketmoqda")
    if kind not in ("swing", "long"):
        raise HTTPException(400, "kind faqat 'swing' yoki 'long' bo'ladi")
    if coin and not market_data.symbol_exists(coin.upper()):
        raise HTTPException(404, "Koin topilmadi")
    background.add_task(run_analysis, "manual", coin.upper() if coin else None, kind)
    return {"started": True}


@app.post("/api/whatsup")
def post_whatsup(background: BackgroundTasks, coin: str | None = None):
    if not global_on():
        raise HTTPException(400, "Umumiy kalit o'chiq — avval yoqing")
    if _status["running"]:
        raise HTTPException(409, "Tahlil allaqachon ketmoqda")
    if coin and not market_data.symbol_exists(coin.upper()):
        raise HTTPException(404, "Koin topilmadi")
    background.add_task(run_whatsup, coin.upper() if coin else None)
    return {"started": True}


@app.get("/api/sentiment")
def get_sentiment():
    return sentiment.fetch(8)


@app.get("/api/schedule")
def get_schedule():
    jobs = []
    watch_next = None
    fast_next = None
    for j in scheduler.get_jobs():
        if not j.next_run_time:
            continue
        when = j.next_run_time.strftime("%Y-%m-%d %H:%M")
        if j.id == "trader_watch":
            watch_next = when          # kuzatuvchi alohida — tahlil ro'yxatiga qo'shilmaydi
        elif j.id == "trader_fast":
            fast_next = when           # tezkor savdo ham alohida
        elif j.id == "telegram_poll":
            pass                       # telegram so'rovi ro'yxatga umuman qo'shilmaydi
        else:
            jobs.append(when)
    return {"enabled": db.get_setting("auto_enabled", "1") == "1",
            "next_runs": sorted(jobs),
            "watch_next": watch_next,
            "watch_min": db.get_setting("trader_watch_min", "5"),
            "fast_enabled": db.get_setting("trader_fast_enabled", "0") == "1",
            "fast_next": fast_next,
            "fast_min": db.get_setting("trader_fast_interval_min", "20")}


@app.get("/api/status")
def get_status():
    return _status


@app.post("/api/analyses/{aid}/feedback")
def post_feedback(aid: int, body: FeedbackIn):
    outcome = body.outcome
    if outcome == "clear":
        outcome = ""
    db.set_feedback(aid, applied=body.applied,
                    outcome=(None if outcome is None else (outcome or None)),
                    note=body.note)
    # 'clear' bo'lsa NULL ga qaytarish
    if body.outcome == "clear":
        conn = db.get_db()
        conn.execute("UPDATE analyses SET outcome=NULL WHERE id=?", (aid,))
        conn.commit()
        conn.close()
    return {"ok": True}


@app.get("/api/stats/analyses")
def stats_analyses(period: str = "all"):
    return db.analyses_stats(period_since(period))


@app.post("/api/analyses/{aid}/ai_feedback")
def post_ai_feedback(aid: int, body: AiFeedbackIn):
    if body.verdict not in ("good", "bad", "clear"):
        raise HTTPException(400, "verdict: good | bad | clear")
    db.set_ai_feedback(aid, body.ai, body.verdict)
    return {"ok": True}


@app.get("/api/stats/ai")
def stats_ai(period: str = "all", kind: str | None = None):
    return db.ai_accuracy_stats(period_since(period), kind)


@app.get("/api/stats/fast_summary")
def stats_fast_summary(period: str = "all"):
    return db.fast_analysis_summary(period_since(period))


@app.post("/api/ai/test")
def ai_test():
    """Barcha AI'larni mitti so'rov bilan tekshiradi."""
    try:
        enabled = json.loads(db.get_setting("ai_enabled", "{}"))
    except Exception:
        enabled = {}
    ids = [a for a, on in enabled.items() if on] + ["claude"]
    return [ai_council.test_provider(pid) for pid in dict.fromkeys(ids)]


# ---------- AI Trader ----------

@app.get("/api/trader/trades")
def trader_trades():
    return db.list_ai_trades()


@app.get("/api/trader/stats")
def trader_stats(period: str = "all"):
    return db.ai_trader_stats(period_since(period))


@app.get("/api/trader/session_stats")
def trader_session_stats(session: str = "fast", period: str = "all"):
    if session not in ("swing", "fast"):
        raise HTTPException(400, "session faqat 'swing' yoki 'fast' bo'ladi")
    return db.ai_session_stats(session, period_since(period))


@app.post("/api/trader/close/{tid}")
def trader_close(tid: int):
    tr = db.ai_trade_by_id(tid)
    if not tr:
        raise HTTPException(404, "Topilmadi")
    try:
        price = market_data.exchange().fetch_ticker(f"{tr['coin']}/USDT")["last"]
        trader.close_manual(tid, price)
    except Exception as e:
        raise HTTPException(500, ai_council._sanitize(str(e))[:300])
    return {"ok": True}


@app.get("/api/trader/binance_test")
def trader_binance_test():
    return trader.test_connection()


@app.post("/api/telegram/test")
def telegram_test():
    return telegram_bot.test_connection()


@app.get("/api/telegram/log")
def telegram_log():
    return {"log": _status.get("telegram_log")}


@app.post("/api/trader/watch")
def trader_watch_now():
    """Kuzatuvchini darhol ishga tushiradi (kutmasdan tekshirish uchun)."""
    if not global_on():
        raise HTTPException(400, "Umumiy kalit o'chiq — avval yoqing")
    logs = trader.watch()
    return {"logs": logs}


@app.post("/api/trader/fast_now")
def trader_fast_now():
    """Tezkor savdo tsiklini darhol ishga tushiradi (kutmasdan tekshirish uchun)."""
    if not global_on():
        raise HTTPException(400, "Umumiy kalit o'chiq — avval yoqing")
    if db.get_setting("trader_fast_enabled", "0") != "1":
        raise HTTPException(400, "Tezkor savdo o'chiq — avval yoqing")
    logs = trader.run_fast_cycle()
    _status["trader_fast_log"] = (
        f"{now_hm()} — " + (" | ".join(logs) if logs else "koinlar tekshirildi, savdo ochilmadi"))
    return {"logs": logs}


@app.get("/api/trader/log")
def trader_log():
    decision = None
    raw = db.get_setting("trader_last_decision")
    if raw:
        try:
            decision = json.loads(raw)
        except Exception:
            decision = None
    return {"log": _status.get("trader_log"), "fast_log": _status.get("trader_fast_log"),
            "decision": decision}


# ---------- Kripto jurnal (rasm asosida) ----------

def _fee_to_usd(fee, fee_asset, price):
    if not fee:
        return 0.0
    fee_asset = (fee_asset or "").upper()
    if fee_asset in ("USDT", "USD", "USDC", "BUSD", ""):
        return round(fee, 8)
    return round(fee * price, 8)   # koinning o'zida ushlangan komissiya — narxiga ko'paytiramiz


@app.post("/api/journal/read")
def journal_read(body: JournalReadIn):
    """Skrinshotni o'qiydi, hech narsani saqlamaydi — faqat tasdiqlash uchun qaytaradi."""
    try:
        draft = journal_ai.extract_trade(body.image_base64, body.media_type)
    except Exception as e:
        raise HTTPException(500, str(e)[:300])
    dup = db.journal_order_exists(draft.get("order_id")) if draft.get("order_id") else None
    pos = db.journal_position(draft["coin"].upper()) if draft.get("coin") else None
    return {"draft": draft, "duplicate": dup, "current_position": pos}


@app.post("/api/journal/confirm")
def journal_confirm(body: JournalConfirmIn):
    if body.side not in ("buy", "sell", "earn"):
        raise HTTPException(400, "side faqat 'buy', 'sell' yoki 'earn' bo'lishi mumkin")
    if body.qty <= 0:
        raise HTTPException(400, "miqdor musbat bo'lishi kerak")
    if body.side == "sell" and body.price <= 0:
        raise HTTPException(400, "sotishda narx musbat bo'lishi kerak")
    if body.price < 0 or body.usd_value < 0:
        raise HTTPException(400, "narx va summa manfiy bo'lmasligi kerak")
    if body.order_id and db.journal_order_exists(body.order_id):
        raise HTTPException(409, "Bu buyurtma allaqachon qo'shilgan")

    coin = body.coin.strip().upper()
    fee_usd = _fee_to_usd(body.fee, body.fee_asset, body.price)

    if body.side in ("buy", "earn"):
        # Earn — bepul kelgan koin (masalan Binance Earn/Staking). Xarid bilan bir xil
        # pulga qo'shiladi: miqdor va tannarx (odatda $0) umumiy pozitsiyaga qo'shiladi,
        # shu bilan o'rtacha narx avtomatik pasayadi — alohida hisob-kitob kerak emas.
        eid = db.journal_add_buy(coin, body.qty, body.price, body.usd_value, body.fee,
                                 body.fee_asset, fee_usd, body.order_id, body.note,
                                 body.created_at, side=body.side)
        return {"id": eid, "side": body.side}
    else:
        eid, net_usd, over_sold = db.journal_add_sell(
            coin, body.qty, body.price, body.usd_value, body.fee,
            body.fee_asset, fee_usd, body.order_id, body.note, body.created_at)
        return {"id": eid, "side": "sell", "net_usd": net_usd, "over_sold": over_sold}


@app.get("/api/journal/positions")
def journal_positions():
    """Ochiq pozitsiyalar, joriy narx va noreal foyda/zarar bilan."""
    positions = db.journal_positions_list()
    for p in positions:
        try:
            price = market_data.exchange().fetch_ticker(f"{p['coin']}/USDT")["last"]
            p["current_price"] = price
            p["unrealized_usd"] = round(price * p["qty"] - p["cost_usd"], 2)
            p["unrealized_pct"] = round((price / p["avg_price"] - 1) * 100, 2) if p["avg_price"] else 0
        except Exception:
            p["current_price"] = None
            p["unrealized_usd"] = None
            p["unrealized_pct"] = None
    return positions


# Qoldiq qiymati shu summadan kam bo'lsa — savdo yopilgan deb hisoblanadi.
# Sabab: sotilgandan keyin hisobda qolgan tiyin-chaqa ("chang") ochiq
# pozitsiya emas, uni ko'rsatish faqat chalg'itadi.
DUST_USD = 1.0


@app.get("/api/journal/portfolio")
def journal_portfolio():
    """Barcha koinlar: amaldagilar va yopilganlar birga, ajratish uchun belgi bilan."""
    items = db.journal_portfolio()
    for a in items:
        price = None
        if a["qty"] > 0:
            try:
                price = market_data.exchange().fetch_ticker(f"{a['coin']}/USDT")["last"]
            except Exception:
                price = None
        a["current_price"] = price
        value = (price * a["qty"]) if (price and a["qty"] > 0) else 0.0
        a["value_usd"] = round(value, 2)
        if price and a["qty"] > 0 and a["avg_price"]:
            a["unrealized_usd"] = round(value - a["cost_usd"], 2)
            a["unrealized_pct"] = round((price / a["avg_price"] - 1) * 100, 2)
        else:
            a["unrealized_usd"] = None
            a["unrealized_pct"] = None
        # chang qoldiq: miqdor bor, lekin qiymati $1 dan kam
        a["dust"] = bool(a["qty"] > 0 and value < DUST_USD)
        a["open"] = bool(a["qty"] > 0 and value >= DUST_USD)
        a["partial"] = bool(a["open"] and a["sold_qty"] > 0)
    return items


@app.get("/api/journal/summary")
def journal_summary():
    """Yuqoridagi kartalar uchun qisqa ko'rsatkichlar."""
    items = journal_portfolio()
    counts = db.journal_entry_counts()
    return {
        "open_count": sum(1 for a in items if a["open"]),
        "closed_count": sum(1 for a in items if not a["open"]),
        "coin_count": len(items),
        "entry_count": counts["total"],
        "buy_count": counts["buys"],
        "sell_count": counts["sells"],
    }


@app.get("/api/journal/entries")
def journal_entries(coin: str | None = None, limit: int = 100):
    if coin:
        return db.journal_entries_for_coin(coin, limit)
    return db.journal_all_entries(limit)


class JournalEditIn(BaseModel):
    qty: float
    price: float = 0
    usd_value: float = 0
    fee: float = 0
    fee_asset: str | None = None
    order_id: str | None = None
    note: str | None = None
    created_at: str | None = None


@app.put("/api/journal/entries/{entry_id}")
def journal_edit(entry_id: int, body: JournalEditIn):
    if body.qty <= 0:
        raise HTTPException(400, "miqdor musbat bo'lishi kerak")
    if body.price < 0 or body.usd_value < 0:
        raise HTTPException(400, "narx va summa manfiy bo'lmasligi kerak")
    r = db.journal_edit_entry(entry_id, body.qty, body.price, body.usd_value, body.fee,
                              body.fee_asset, body.order_id, body.note,
                              body.created_at or None)
    if r is None:
        raise HTTPException(404, "Yozuv topilmadi")
    return r


@app.delete("/api/journal/entries/{entry_id}")
def journal_delete(entry_id: int):
    ok = db.journal_delete_entry(entry_id)
    if not ok:
        raise HTTPException(404, "Yozuv topilmadi")
    return {"ok": True}


@app.get("/api/journal/stats")
def journal_stats_ep(period: str = "all"):
    return db.journal_stats(period_since(period))


# ---------- Eski qo'lda jurnal (hozircha ishlatilmaydi, kelajakda kerak bo'lishi mumkin) ----------

@app.get("/api/trades")
def get_trades():
    return db.list_trades()


@app.post("/api/trades")
def post_trade(body: TradeOpenIn):
    fee = float(db.get_setting("fee_rate", "0.1"))
    tid = db.open_trade(body.coin.strip().upper(), body.entry_price, body.amount_usd,
                        fee, body.note, body.analysis_id)
    return {"id": tid}


@app.post("/api/trades/{tid}/close")
def post_close(tid: int, body: TradeCloseIn):
    db.close_trade(tid, body.exit_price)
    return {"ok": True}


@app.delete("/api/trades/{tid}")
def del_trade(tid: int):
    db.delete_trade(tid)
    return {"ok": True}


@app.get("/api/stats/trades")
def stats_trades(period: str = "all"):
    return db.trades_stats(period_since(period))


@app.get("/api/prices")
def get_prices():
    """Ochiq savdolar (qo'lda + AI) uchun joriy narxlar."""
    open_coins = {t["coin"] for t in db.list_trades() if not t["exit_price"]}
    open_coins |= {t["coin"] for t in db.list_ai_trades() if not t["exit_price"]}
    out = {}
    for c in open_coins:
        try:
            out[c] = market_data.exchange().fetch_ticker(f"{c}/USDT")["last"]
        except Exception:
            out[c] = None
    return out


@app.get("/api/watch")
def get_watch(tf: str = "1d", limit: int = 30):
    """Kuzatuv ro'yxati: joriy narx, 24s o'zgarish va grafik uchun narx qatori."""
    if tf not in ("1h", "4h", "1d"):
        raise HTTPException(400, "tf faqat 1h, 4h yoki 1d")
    limit = max(5, min(limit, 120))
    ex = market_data.exchange()
    out = []
    for c in db.list_coins():
        try:
            pair = f"{c}/USDT"
            t = ex.fetch_ticker(pair)
            ohlcv = ex.fetch_ohlcv(pair, tf, limit=limit)
            out.append({
                "coin": c,
                "price": t["last"],
                "change24": t.get("percentage"),
                "series": [{"t": o[0], "c": o[4]} for o in ohlcv],
            })
        except Exception as e:
            out.append({"coin": c, "error": str(e)})
    return out


# ---------- Sozlamalar ----------

@app.get("/api/settings")
def get_settings():
    return db.all_settings()


@app.post("/api/settings")
def post_settings(body: SettingsIn):
    changed_schedule = False
    changed_watch = False
    changed_fast = False
    changed_global = False
    changed_telegram = False
    for k, v in body.model_dump(exclude_none=True).items():
        db.set_setting(k, v)
        if k.startswith("schedule_") or k == "auto_enabled":
            changed_schedule = True
        if k == "trader_watch_min":
            changed_watch = True
        if k in ("trader_fast_enabled", "trader_fast_interval_min"):
            changed_fast = True
        if k == "global_enabled":
            changed_global = True
        if k == "telegram_enabled":
            changed_telegram = True
    if changed_global:
        # umumiy kalit o'zgarsa — barchasi qayta baholanishi kerak
        reschedule(); schedule_watch(); schedule_fast(); schedule_telegram_poll()
    else:
        if changed_schedule:
            reschedule()
        if changed_watch:
            schedule_watch()
        if changed_fast:
            schedule_fast()
        if changed_telegram:
            schedule_telegram_poll()
    return {"ok": True}


app.mount("/static", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static")))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
