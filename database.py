"""SQLite ma'lumotlar bazasi qatlami."""
import sqlite3
import json
import os
from datetime import datetime, timezone

DB_PATH = os.environ.get("DB_PATH", "data.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS coins (
        symbol TEXT PRIMARY KEY,
        added_at TEXT NOT NULL,
        auto INTEGER NOT NULL DEFAULT 1,              -- 1=avtomatik tahlilga kiradi, 0=faqat kuzatuv
        starred INTEGER NOT NULL DEFAULT 0,           -- 1=yulduzcha bilan belgilangan (yuqorida turadi)
        sort_order INTEGER                            -- foydalanuvchi belgilagan tartib
    );
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS analyses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL,
        coin TEXT NOT NULL,
        mode TEXT NOT NULL DEFAULT 'auto',          -- auto | manual
        kind TEXT NOT NULL DEFAULT 'swing',          -- swing | long
        summary TEXT NOT NULL,                       -- yakuniy xulosa
        discussion TEXT,                             -- JSON: barcha AI fikrlari
        applied INTEGER,                             -- NULL=belgilanmagan, 0=qo'llamadim, 1=amalga oshirdim
        outcome TEXT,                                -- NULL | 'good' | 'bad'
        note TEXT
    );
    CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        coin TEXT NOT NULL,
        entry_price REAL NOT NULL,
        exit_price REAL,
        amount_usd REAL NOT NULL,
        fee_rate REAL NOT NULL,                      -- % bir tomonlama (0.1 = 0.1%)
        opened_at TEXT NOT NULL,
        closed_at TEXT,
        analysis_id INTEGER,
        note TEXT,
        FOREIGN KEY (analysis_id) REFERENCES analyses(id) ON DELETE SET NULL
    );
    CREATE TABLE IF NOT EXISTS ai_trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mode TEXT NOT NULL,                          -- paper | real
        coin TEXT NOT NULL,
        entry_price REAL NOT NULL,
        exit_price REAL,
        amount_usd REAL NOT NULL,
        fee_rate REAL NOT NULL,
        opened_at TEXT NOT NULL,
        closed_at TEXT,
        analysis_id INTEGER,
        order_info TEXT,
        stop REAL,                                   -- himoya narxi (hakamdan)
        targets TEXT,                                -- JSON ro'yxat: maqsad narxlari
        close_reason TEXT,                           -- stop | target | ai | manual
        session TEXT DEFAULT 'swing'                 -- swing | fast
    );
    CREATE TABLE IF NOT EXISTS journal_entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        side TEXT NOT NULL,                          -- buy | sell
        coin TEXT NOT NULL,
        qty REAL NOT NULL,
        price REAL NOT NULL,
        usd_value REAL NOT NULL,                     -- xaridda sarflangan, sotishda tushgan $
        fee REAL NOT NULL DEFAULT 0,
        fee_asset TEXT,
        fee_usd REAL NOT NULL DEFAULT 0,              -- komissiya USD ga o'girilgan holda
        avg_cost_usd REAL,                            -- faqat sell: o'sha ondagi o'rtacha tannarx (birlik uchun)
        net_usd REAL,                                 -- faqat sell: sof foyda/zarar
        order_id TEXT UNIQUE,                         -- Binance buyurtma raqami — takrorni oldini oladi
        note TEXT,
        created_at TEXT NOT NULL,
        added_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS journal_positions (
        coin TEXT PRIMARY KEY,
        qty REAL NOT NULL DEFAULT 0,
        cost_usd REAL NOT NULL DEFAULT 0,
        updated_at TEXT
    );
    CREATE TABLE IF NOT EXISTS telegram_chats (
        chat_id TEXT PRIMARY KEY,          -- kim botga yozgan bo'lsa, shu yerda saqlanadi
        username TEXT,
        added_at TEXT NOT NULL
    );
    """)
    # Standart sozlamalar
    defaults = {
        "fee_rate": "0.1",
        "profile": ("Spot/Swing treyder. Haftada 1-3 marta savdo qilaman, pozitsiyani bir necha kundan "
                    "bir necha haftagacha ushlayman. Menga kunlik va 4 soatlik vaqt oralig'idagi tahlil, "
                    "aniq kirish/chiqish zonalari va xatar darajasi kerak."),
        "schedule_morning": "09:00",
        "schedule_evening": "18:30",
        "ai_enabled": '{"qwen": 1, "deepseek": 1, "gpt": 0, "gemini": 0}',
        "auto_enabled": "1",
        "trader_enabled": "0",
        "trader_mode": "paper",
        "trader_max_usd": "20",
        "trader_max_daily": "3",
        "trader_min_conf": "70",        # savdoga kirish uchun eng kam ishonch darajasi, %
        "trader_max_open": "2",         # bir vaqtda eng ko'p ochiq pozitsiya
        "trader_cooldown_h": "24",      # savdo yopilgach koin necha soat bloklanadi
        "trader_max_stops": "2",        # bir kunda necha stop ishlasa, to'xtaydi
        "trader_watch_min": "5",        # kuzatuvchi necha daqiqada bir tekshiradi
        "global_enabled": "1",          # umumiy kalit — o'chiq bo'lsa hech qanday jarayon ishlamaydi
        "telegram_enabled": "0",
        "telegram_notify_fast": "0",    # tezkor savdo "Sotib ol" signallarini ham yuborsinmi
        "telegram_update_offset": "0",  # Telegram'dan kimlar yozganini so'rashda ishlatiladi
        # Tezkor savdo (skalping/kun ichi) — bosh tahlildan mustaqil alohida tizim
        "trader_fast_enabled": "0",
        "trader_fast_interval_min": "20",
        "trader_fast_max_usd": "10",
        "trader_fast_max_daily": "6",
        "trader_fast_min_conf": "65",
        "trader_fast_max_open": "2",
        "trader_fast_cooldown_h": "2",
        "trader_fast_max_stops": "3",
    }
    for k, v in defaults.items():
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
    # Eski bazalar uchun migratsiyalar
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(analyses)").fetchall()]
    if "kind" not in cols:
        conn.execute("ALTER TABLE analyses ADD COLUMN kind TEXT NOT NULL DEFAULT 'swing'")
    if "price" not in cols:
        conn.execute("ALTER TABLE analyses ADD COLUMN price REAL")
    if "ai_feedback" not in cols:
        conn.execute("ALTER TABLE analyses ADD COLUMN ai_feedback TEXT")
    tcols = [r["name"] for r in conn.execute("PRAGMA table_info(ai_trades)").fetchall()]
    for col, typ in (("stop", "REAL"), ("targets", "TEXT"), ("close_reason", "TEXT"),
                    ("session", "TEXT DEFAULT 'swing'")):
        if col not in tcols:
            conn.execute(f"ALTER TABLE ai_trades ADD COLUMN {col} {typ}")
    ccols = [r["name"] for r in conn.execute("PRAGMA table_info(coins)").fetchall()]
    if "auto" not in ccols:
        conn.execute("ALTER TABLE coins ADD COLUMN auto INTEGER NOT NULL DEFAULT 1")
    if "starred" not in ccols:
        conn.execute("ALTER TABLE coins ADD COLUMN starred INTEGER NOT NULL DEFAULT 0")
    if "sort_order" not in ccols:
        conn.execute("ALTER TABLE coins ADD COLUMN sort_order INTEGER")
        # Mavjud koinlarga qo'shilgan tartibida raqam beramiz — shundan keyin
        # foydalanuvchi qo'lda tartiblay oladi.
        rows = conn.execute("SELECT symbol FROM coins ORDER BY added_at").fetchall()
        for i, r in enumerate(rows):
            conn.execute("UPDATE coins SET sort_order=? WHERE symbol=?", (i, r["symbol"]))
    conn.commit()
    conn.close()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


# ---------- Sozlamalar ----------

def get_setting(key, default=None):
    conn = get_db()
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    conn.close()
    return row["value"] if row else default


def set_setting(key, value):
    conn = get_db()
    conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
    conn.commit()
    conn.close()


def all_settings():
    conn = get_db()
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    conn.close()
    return {r["key"]: r["value"] for r in rows}


# ---------- Koinlar ----------

def list_coins(auto_only=False):
    conn = get_db()
    q = "SELECT symbol FROM coins"
    if auto_only:
        q += " WHERE auto=1"
    rows = conn.execute(q + " ORDER BY sort_order, added_at").fetchall()
    conn.close()
    return [r["symbol"] for r in rows]


def list_coins_full():
    conn = get_db()
    rows = conn.execute(
        "SELECT symbol, auto, starred FROM coins ORDER BY sort_order, added_at").fetchall()
    conn.close()
    return [{"symbol": r["symbol"], "auto": bool(r["auto"]), "starred": bool(r["starred"])}
            for r in rows]


def set_coin_auto(symbol, auto):
    conn = get_db()
    conn.execute("UPDATE coins SET auto=? WHERE symbol=?", (1 if auto else 0, symbol))
    conn.commit()
    conn.close()


def set_coin_starred(symbol, starred):
    conn = get_db()
    conn.execute("UPDATE coins SET starred=? WHERE symbol=?", (1 if starred else 0, symbol))
    conn.commit()
    conn.close()


def reorder_coins(symbols):
    """symbols — xohlagan tartibda to'liq ro'yxat. Har biriga shu tartibda
    sort_order beradi. Ro'yxatda bo'lmagan mavjud koinlar oxiriga qoladi."""
    conn = get_db()
    for i, sym in enumerate(symbols):
        conn.execute("UPDATE coins SET sort_order=? WHERE symbol=?", (i, sym.upper()))
    conn.commit()
    conn.close()


def add_coin(symbol):
    """Yangi koin avto-tahlil O'CHIQ holda qo'shiladi — foydalanuvchi o'zi yoqadi."""
    conn = get_db()
    max_order = conn.execute("SELECT MAX(sort_order) AS m FROM coins").fetchone()["m"]
    next_order = (max_order or 0) + 1
    conn.execute("INSERT OR IGNORE INTO coins (symbol, added_at, auto, sort_order) "
                "VALUES (?, ?, 0, ?)", (symbol, now_iso(), next_order))
    conn.commit()
    conn.close()


def remove_coin(symbol):
    conn = get_db()
    conn.execute("DELETE FROM coins WHERE symbol=?", (symbol,))
    conn.commit()
    conn.close()


# ---------- Tahlillar ----------

def save_analysis(coin, summary, discussion, mode="auto", kind="swing", price=None):
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO analyses (created_at, coin, mode, kind, summary, discussion, price) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (now_iso(), coin, mode, kind, summary, json.dumps(discussion, ensure_ascii=False), price))
    conn.commit()
    aid = cur.lastrowid
    conn.close()
    return aid


def list_analyses(limit=50, offset=0, kind=None):
    conn = get_db()
    if kind:
        rows = conn.execute(
            "SELECT * FROM analyses WHERE kind=? ORDER BY id DESC LIMIT ? OFFSET ?",
            (kind, limit, offset)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM analyses ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["discussion"] = json.loads(d["discussion"]) if d["discussion"] else None
        d["ai_feedback"] = json.loads(d["ai_feedback"]) if d.get("ai_feedback") else {}
        out.append(d)
    return out


def set_ai_feedback(analysis_id, ai, verdict):
    """Bitta AI fikriga baho: 'good' | 'bad' | 'clear'."""
    conn = get_db()
    row = conn.execute("SELECT ai_feedback FROM analyses WHERE id=?", (analysis_id,)).fetchone()
    fb = json.loads(row["ai_feedback"]) if row and row["ai_feedback"] else {}
    if verdict == "clear":
        fb.pop(ai, None)
    else:
        fb[ai] = verdict
    conn.execute("UPDATE analyses SET ai_feedback=? WHERE id=?",
                 (json.dumps(fb), analysis_id))
    conn.commit()
    conn.close()


def ai_accuracy_stats(since_iso=None, kind=None):
    """Har bir AI bo'yicha to'g'ri/noto'g'ri belgilar soni.
    kind berilmasa — faqat swing va long birga (news va fast kirmaydi —
    fast'ning o'z alohida statistikasi bor, aralashtirilmasin)."""
    conn = get_db()
    if kind:
        q = "SELECT ai_feedback FROM analyses WHERE ai_feedback IS NOT NULL AND kind=?"
        params = [kind]
    else:
        q = "SELECT ai_feedback FROM analyses WHERE ai_feedback IS NOT NULL AND kind NOT IN ('news', 'fast')"
        params = []
    if since_iso:
        q += " AND created_at >= ?"
        params.append(since_iso)
    rows = conn.execute(q, params).fetchall()
    conn.close()
    stats = {}
    for r in rows:
        for ai, v in json.loads(r["ai_feedback"]).items():
            d = stats.setdefault(ai, {"good": 0, "bad": 0})
            if v in d:
                d[v] += 1
    return [{"ai": k, **v} for k, v in sorted(stats.items())]


def set_feedback(analysis_id, applied=None, outcome=None, note=None):
    conn = get_db()
    if applied is not None:
        conn.execute("UPDATE analyses SET applied=? WHERE id=?", (1 if applied else 0, analysis_id))
        if not applied:  # qo'llanmagan tahlilda natija bo'lmaydi
            conn.execute("UPDATE analyses SET outcome=NULL WHERE id=?", (analysis_id,))
    if outcome is not None:
        conn.execute("UPDATE analyses SET outcome=? WHERE id=?",
                     (outcome if outcome in ("good", "bad") else None, analysis_id))
    if note is not None:
        conn.execute("UPDATE analyses SET note=? WHERE id=?", (note, analysis_id))
    conn.commit()
    conn.close()


def analyses_stats(since_iso=None):
    """Tahlillar statistikasi. 'news' (Nima bo'ldi?) va 'fast' (tezkor savdo) hisobga
    olinmaydi — ikkalasi ham savdo tavsiyasi tarzida sizga "Amalga oshirdim" deb
    belgilash uchun emas (fast avtomatik ishlaydi, news faqat tushuntirish beradi)."""
    conn = get_db()
    q = "SELECT created_at, applied, outcome FROM analyses WHERE kind NOT IN ('news', 'fast')"
    params = ()
    if since_iso:
        q += " AND created_at >= ?"
        params = (since_iso,)
    rows = conn.execute(q, params).fetchall()
    conn.close()
    total = len(rows)
    applied = sum(1 for r in rows if r["applied"] == 1)
    not_applied = sum(1 for r in rows if r["applied"] == 0)
    good = sum(1 for r in rows if r["applied"] == 1 and r["outcome"] == "good")
    bad = sum(1 for r in rows if r["applied"] == 1 and r["outcome"] == "bad")
    # oyma-oy dinamika
    monthly = {}
    for r in rows:
        m = r["created_at"][:7]
        d = monthly.setdefault(m, {"total": 0, "good": 0, "bad": 0})
        d["total"] += 1
        if r["applied"] == 1 and r["outcome"] == "good":
            d["good"] += 1
        if r["applied"] == 1 and r["outcome"] == "bad":
            d["bad"] += 1
    return {
        "total": total, "applied": applied, "not_applied": not_applied,
        "unmarked": total - applied - not_applied,
        "good": good, "bad": bad, "pending": applied - good - bad,
        "monthly": [{"month": k, **v} for k, v in sorted(monthly.items())],
    }


# ---------- Jurnal (savdolar) ----------

def trade_result(entry, exit_, amount, fee_rate):
    """Sof natijani hisoblaydi. fee_rate — % bir tomonlama (0.1 => 0.1%)."""
    f = fee_rate / 100.0
    gross = amount * (exit_ / entry - 1.0)
    fee_buy = amount * f
    fee_sell = amount * (exit_ / entry) * f
    net = gross - fee_buy - fee_sell
    return {
        "gross_usd": round(gross, 2),
        "fees_usd": round(fee_buy + fee_sell, 2),
        "net_usd": round(net, 2),
        "net_pct": round(net / amount * 100.0, 2),
    }


def open_trade(coin, entry_price, amount_usd, fee_rate, note=None, analysis_id=None, opened_at=None):
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO trades (coin, entry_price, amount_usd, fee_rate, opened_at, note, analysis_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (coin, entry_price, amount_usd, fee_rate, opened_at or now_iso(), note, analysis_id))
    conn.commit()
    tid = cur.lastrowid
    conn.close()
    return tid


def close_trade(trade_id, exit_price, closed_at=None):
    conn = get_db()
    conn.execute("UPDATE trades SET exit_price=?, closed_at=? WHERE id=?",
                 (exit_price, closed_at or now_iso(), trade_id))
    conn.commit()
    conn.close()


def delete_trade(trade_id):
    conn = get_db()
    conn.execute("DELETE FROM trades WHERE id=?", (trade_id,))
    conn.commit()
    conn.close()


def list_trades():
    conn = get_db()
    rows = conn.execute("SELECT * FROM trades ORDER BY opened_at DESC").fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        if d["exit_price"]:
            d.update(trade_result(d["entry_price"], d["exit_price"], d["amount_usd"], d["fee_rate"]))
        out.append(d)
    return out


def trades_stats(since_iso=None):
    conn = get_db()
    q = "SELECT * FROM trades WHERE exit_price IS NOT NULL"
    params = ()
    if since_iso:
        q += " AND closed_at >= ?"
        params = (since_iso,)
    rows = [dict(r) for r in conn.execute(q + " ORDER BY closed_at", params).fetchall()]
    conn.close()

    results = []
    for d in rows:
        res = trade_result(d["entry_price"], d["exit_price"], d["amount_usd"], d["fee_rate"])
        results.append({**d, **res})

    wins = [r for r in results if r["net_usd"] > 0]
    losses = [r for r in results if r["net_usd"] <= 0]
    total_net = round(sum(r["net_usd"] for r in results), 2)
    total_fees = round(sum(r["fees_usd"] for r in results), 2)
    invested = sum(r["amount_usd"] for r in results)

    monthly = {}
    for r in results:
        m = r["closed_at"][:7]
        monthly[m] = round(monthly.get(m, 0) + r["net_usd"], 2)

    # balans o'sish chizig'i (jamlangan)
    equity, cum = [], 0.0
    for r in results:
        cum += r["net_usd"]
        equity.append({"date": r["closed_at"][:10], "cum_usd": round(cum, 2)})

    by_coin = {}
    for r in results:
        c = by_coin.setdefault(r["coin"], {"net_usd": 0, "count": 0})
        c["net_usd"] = round(c["net_usd"] + r["net_usd"], 2)
        c["count"] += 1

    return {
        "closed_count": len(results),
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate": round(len(wins) / len(results) * 100, 1) if results else 0,
        "total_net_usd": total_net,
        "total_fees_usd": total_fees,
        "total_net_pct": round(total_net / invested * 100, 2) if invested else 0,
        "avg_win_pct": round(sum(r["net_pct"] for r in wins) / len(wins), 2) if wins else 0,
        "avg_loss_pct": round(sum(r["net_pct"] for r in losses) / len(losses), 2) if losses else 0,
        "monthly": [{"month": k, "net_usd": v} for k, v in sorted(monthly.items())],
        "equity": equity,
        "by_coin": [{"coin": k, **v} for k, v in sorted(by_coin.items(), key=lambda x: -x[1]["net_usd"])],
    }


# ---------- AI Trader savdolari ----------

def ai_open(coin, entry_price, amount_usd, fee_rate, mode, analysis_id=None, order_info=None,
            stop=None, targets=None, session="swing"):
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO ai_trades (mode, coin, entry_price, amount_usd, fee_rate, opened_at, "
        "analysis_id, order_info, stop, targets, session) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (mode, coin, entry_price, amount_usd, fee_rate, now_iso(), analysis_id, order_info,
         stop, json.dumps(targets) if targets else None, session))
    conn.commit()
    tid = cur.lastrowid
    conn.close()
    return tid


def ai_close(trade_id, exit_price, reason="ai"):
    conn = get_db()
    conn.execute("UPDATE ai_trades SET exit_price=?, closed_at=?, close_reason=? WHERE id=?",
                 (exit_price, now_iso(), reason, trade_id))
    conn.commit()
    conn.close()


def ai_open_trades(mode=None):
    """Ochiq AI pozitsiyalari. mode berilmasa — barcha rejimlar."""
    conn = get_db()
    if mode:
        rows = conn.execute("SELECT * FROM ai_trades WHERE exit_price IS NULL AND mode=?",
                            (mode,)).fetchall()
    else:
        rows = conn.execute("SELECT * FROM ai_trades WHERE exit_price IS NULL").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def ai_open_count(mode, session=None):
    conn = get_db()
    q = "SELECT COUNT(*) AS c FROM ai_trades WHERE mode=? AND exit_price IS NULL"
    p = [mode]
    if session:
        q += " AND session=?"; p.append(session)
    row = conn.execute(q, p).fetchone()
    conn.close()
    return row["c"]


def ai_last_closed_at(coin, mode, session=None):
    """Shu koin bo'yicha oxirgi yopilgan savdo vaqti (sovish muddati uchun)."""
    conn = get_db()
    q = ("SELECT closed_at FROM ai_trades WHERE coin=? AND mode=? AND closed_at IS NOT NULL")
    p = [coin, mode]
    if session:
        q += " AND session=?"; p.append(session)
    q += " ORDER BY closed_at DESC LIMIT 1"
    row = conn.execute(q, p).fetchone()
    conn.close()
    return row["closed_at"] if row else None


def ai_stops_today(mode, session=None):
    """Bugun necha marta stop ishlagan."""
    conn = get_db()
    q = "SELECT COUNT(*) AS c FROM ai_trades WHERE mode=? AND close_reason='stop' AND closed_at >= ?"
    p = [mode, now_iso()[:10]]
    if session:
        q += " AND session=?"; p.append(session)
    row = conn.execute(q, p).fetchone()
    conn.close()
    return row["c"]


def ai_trade_by_id(trade_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM ai_trades WHERE id=?", (trade_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def ai_open_for_coin(coin, mode, session=None):
    conn = get_db()
    q = "SELECT * FROM ai_trades WHERE coin=? AND mode=? AND exit_price IS NULL"
    p = [coin, mode]
    if session:
        q += " AND session=?"; p.append(session)
    q += " ORDER BY id DESC LIMIT 1"
    row = conn.execute(q, p).fetchone()
    conn.close()
    return dict(row) if row else None


def ai_trades_today(mode, session=None):
    conn = get_db()
    today = now_iso()[:10]
    q = "SELECT COUNT(*) AS c FROM ai_trades WHERE mode=? AND opened_at >= ?"
    p = [mode, today]
    if session:
        q += " AND session=?"; p.append(session)
    row = conn.execute(q, p).fetchone()
    conn.close()
    return row["c"]


def list_ai_trades():
    conn = get_db()
    rows = conn.execute("SELECT * FROM ai_trades ORDER BY opened_at DESC").fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        if d["exit_price"]:
            d.update(trade_result(d["entry_price"], d["exit_price"], d["amount_usd"], d["fee_rate"]))
        out.append(d)
    return out


def ai_trader_stats(since_iso=None):
    """Rejimlar bo'yicha alohida statistika: {'paper': {...}, 'real': {...}}."""
    conn = get_db()
    q = "SELECT * FROM ai_trades WHERE exit_price IS NOT NULL"
    params = ()
    if since_iso:
        q += " AND closed_at >= ?"
        params = (since_iso,)
    rows = [dict(r) for r in conn.execute(q + " ORDER BY closed_at", params).fetchall()]
    conn.close()

    out = {}
    for mode in ("paper", "real"):
        results = []
        for d in (r for r in rows if r["mode"] == mode):
            res = trade_result(d["entry_price"], d["exit_price"], d["amount_usd"], d["fee_rate"])
            results.append({**d, **res})
        wins = [r for r in results if r["net_usd"] > 0]
        losses = [r for r in results if r["net_usd"] <= 0]
        gross_win = round(sum(r["net_usd"] for r in wins), 2)
        gross_loss = round(-sum(r["net_usd"] for r in losses), 2)   # musbat son
        total_net = round(gross_win - gross_loss, 2)
        invested = round(sum(r["amount_usd"] for r in results), 2)
        equity, cum = [], 0.0
        for r in results:
            cum += r["net_usd"]
            equity.append({"date": r["closed_at"][:10], "cum_usd": round(cum, 2)})
        # yopilish sabablari
        reasons = {}
        for r in results:
            k = r.get("close_reason") or "ai"
            reasons[k] = reasons.get(k, 0) + 1
        out[mode] = {
            "closed_count": len(results),
            "win_count": len(wins),
            "loss_count": len(losses),
            "win_rate": round(len(wins) / len(results) * 100, 1) if results else 0,
            "invested_usd": invested,
            "gross_win_usd": gross_win,
            "gross_loss_usd": gross_loss,
            "win_pct": round(gross_win / invested * 100, 2) if invested else 0,
            "loss_pct": round(gross_loss / invested * 100, 2) if invested else 0,
            "total_net_usd": total_net,
            "total_net_pct": round(total_net / invested * 100, 2) if invested else 0,
            # foyda koeffitsienti: jami foyda / jami zarar. Zarar bo'lmasa None
            "profit_factor": (round(gross_win / gross_loss, 2) if gross_loss > 0 else None),
            "reasons": reasons,
            "equity": equity,
        }
    return out


# ---------- Kripto jurnal (rasm asosida, o'rtacha tannarx usuli) ----------

def journal_order_exists(order_id):
    """Bu buyurtma raqami allaqachon qo'shilganmi. order_id bo'lmasa — False."""
    if not order_id:
        return False
    conn = get_db()
    row = conn.execute("SELECT id, coin, side, qty, price FROM journal_entries WHERE order_id=?",
                       (order_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def journal_position(coin):
    conn = get_db()
    row = conn.execute("SELECT * FROM journal_positions WHERE coin=?", (coin,)).fetchone()
    conn.close()
    return dict(row) if row else {"coin": coin, "qty": 0, "cost_usd": 0, "updated_at": None}


def journal_add_buy(coin, qty, price, usd_value, fee, fee_asset, fee_usd,
                    order_id=None, note=None, created_at=None, side="buy"):
    """Xaridni (yoki 'earn' — bepul kelgan koinni) yozadi va pozitsiyani kengaytiradi.

    side='earn' bo'lsa ham xuddi xarid kabi umumiy pozitsiya hovuziga qo'shiladi —
    shundagina o'rtacha narx va foyda/zarar hisob-kitobi to'g'ri chiqadi (bepul kelgan
    miqdor umumiy tannarxni oshirmasdan umumiy miqdorni oshiradi, demak o'rtacha narx
    avtomatik pasayadi).
    """
    coin = coin.upper()
    created_at = created_at or now_iso()
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO journal_entries (side, coin, qty, price, usd_value, fee, fee_asset, "
        "fee_usd, order_id, note, created_at, added_at) VALUES "
        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (side, coin, qty, price, usd_value, fee, fee_asset, fee_usd, order_id, note, created_at, now_iso()))
    eid = cur.lastrowid
    pos = conn.execute("SELECT qty, cost_usd FROM journal_positions WHERE coin=?", (coin,)).fetchone()
    if pos:
        conn.execute("UPDATE journal_positions SET qty=qty+?, cost_usd=cost_usd+?, updated_at=? "
                    "WHERE coin=?", (qty, usd_value + fee_usd, now_iso(), coin))
    else:
        conn.execute("INSERT INTO journal_positions (coin, qty, cost_usd, updated_at) VALUES (?, ?, ?, ?)",
                    (coin, qty, usd_value + fee_usd, now_iso()))
    conn.commit()
    conn.close()
    return eid


def journal_add_sell(coin, qty, price, usd_value, fee, fee_asset, fee_usd,
                     order_id=None, note=None, created_at=None):
    """Sotishni yozadi. Pozitsiyadagi o'rtacha tannarxga solishtirib sof natijani hisoblaydi.

    Pozitsiyada yetarli miqdor bo'lmasa ham yozib qo'yadi (masalan eski, jurnalga
    kiritilmagan xaridlar bo'lgan holat) — bu holda avg_cost sifatida mavjud
    o'rtacha (yoki topilmasa sotish narxining o'zi, ya'ni 0 foyda/zarar) olinadi.
    """
    coin = coin.upper()
    created_at = created_at or now_iso()
    conn = get_db()
    pos = conn.execute("SELECT qty, cost_usd FROM journal_positions WHERE coin=?", (coin,)).fetchone()
    pos_qty = pos["qty"] if pos else 0
    pos_cost = pos["cost_usd"] if pos else 0
    avg_cost = (pos_cost / pos_qty) if pos_qty > 0 else price   # pozitsiya bo'lmasa 0 foyda/zarar
    over_sold = qty > pos_qty + 1e-9    # jurnalda yo'q xaridni ham sotayaptimi — ogohlantirish uchun
    cost_removed = avg_cost * qty
    net_usd = round(usd_value - fee_usd - cost_removed, 4)

    cur = conn.execute(
        "INSERT INTO journal_entries (side, coin, qty, price, usd_value, fee, fee_asset, "
        "fee_usd, avg_cost_usd, net_usd, order_id, note, created_at, added_at) VALUES "
        "('sell', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (coin, qty, price, usd_value, fee, fee_asset, fee_usd, round(avg_cost, 8), net_usd,
         order_id, note, created_at, now_iso()))
    eid = cur.lastrowid

    new_qty = max(0, pos_qty - qty)
    new_cost = max(0, pos_cost - cost_removed) if new_qty > 0 else 0
    if pos:
        conn.execute("UPDATE journal_positions SET qty=?, cost_usd=?, updated_at=? WHERE coin=?",
                    (new_qty, new_cost, now_iso(), coin))
    else:
        conn.execute("INSERT INTO journal_positions (coin, qty, cost_usd, updated_at) VALUES (?, 0, 0, ?)",
                    (coin, now_iso()))
    conn.commit()
    conn.close()
    return eid, net_usd, over_sold


def journal_positions_list():
    """Hozirgi ochiq pozitsiyalar (qty > 0), o'rtacha narx bilan."""
    conn = get_db()
    rows = conn.execute("SELECT * FROM journal_positions WHERE qty > 0.00000001 "
                        "ORDER BY coin").fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["avg_price"] = round(d["cost_usd"] / d["qty"], 8) if d["qty"] else 0
        out.append(d)
    return out


def journal_entries_for_coin(coin, limit=100):
    conn = get_db()
    rows = conn.execute("SELECT * FROM journal_entries WHERE coin=? ORDER BY created_at DESC LIMIT ?",
                        (coin.upper(), limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def journal_all_entries(limit=200):
    conn = get_db()
    rows = conn.execute("SELECT * FROM journal_entries ORDER BY created_at DESC LIMIT ?",
                        (limit,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _journal_reverse_entry(conn, d):
    """Bitta yozuvning pozitsiyaga ta'sirini bekor qiladi. Ichki yordamchi —
    delete va edit ikkalasi ham shu mantiqdan foydalanadi."""
    pos = conn.execute("SELECT qty, cost_usd FROM journal_positions WHERE coin=?",
                       (d["coin"],)).fetchone()
    qty = pos["qty"] if pos else 0
    cost = pos["cost_usd"] if pos else 0
    if d["side"] in ("buy", "earn"):
        qty = max(0, qty - d["qty"])
        cost = max(0, cost - (d["usd_value"] + d["fee_usd"]))
    else:
        qty = qty + d["qty"]
        cost = cost + (d["avg_cost_usd"] or 0) * d["qty"]
    if pos:
        conn.execute("UPDATE journal_positions SET qty=?, cost_usd=?, updated_at=? WHERE coin=?",
                    (qty, cost, now_iso(), d["coin"]))
    else:
        conn.execute("INSERT INTO journal_positions (coin, qty, cost_usd, updated_at) VALUES (?, ?, ?, ?)",
                    (d["coin"], qty, cost, now_iso()))


def journal_delete_entry(entry_id):
    """Yozuvni o'chiradi. DIQQAT: pozitsiya qayta hisoblanmaydi — shuning uchun
    faqat eng oxirgi (hali boshqa yozuvga ta'sir qilmagan) yozuvni o'chirish
    xavfsiz. Interfeys buni tushuntiradi."""
    conn = get_db()
    row = conn.execute("SELECT * FROM journal_entries WHERE id=?", (entry_id,)).fetchone()
    if not row:
        conn.close()
        return False
    d = dict(row)
    _journal_reverse_entry(conn, d)
    conn.execute("DELETE FROM journal_entries WHERE id=?", (entry_id,))
    conn.commit()
    conn.close()
    return True


def journal_edit_entry(entry_id, qty, price, usd_value, fee, fee_asset, order_id, note, created_at):
    """Mavjud yozuvni tahrirlaydi: avvalgi ta'sirini pozitsiyadan olib tashlab,
    yangi qiymatlar bilan qayta qo'shadi. side o'zgartirilmaydi (buy/sell/earn) —
    turini almashtirish kerak bo'lsa, o'chirib qaytadan qo'shish kerak.

    DIQQAT: delete kabi, faqat eng oxirgi yozuvni tahrirlash xavfsiz — eski
    sell yozuvi tahrirlansa, undan keyingi sell'larning avg_cost'i eskicha qolaveradi."""
    conn = get_db()
    row = conn.execute("SELECT * FROM journal_entries WHERE id=?", (entry_id,)).fetchone()
    if not row:
        conn.close()
        return None
    old = dict(row)
    created_at = created_at or old["created_at"]
    fee_usd = fee if (fee_asset or "").upper() in ("USDT", "USD", "USDC", "BUSD", "") else round(fee * price, 8)

    _journal_reverse_entry(conn, old)

    side = old["side"]
    avg_cost_usd = old["avg_cost_usd"]
    net_usd = old["net_usd"]
    if side == "sell":
        pos = conn.execute("SELECT qty, cost_usd FROM journal_positions WHERE coin=?",
                           (old["coin"],)).fetchone()
        pos_qty = pos["qty"] if pos else 0
        pos_cost = pos["cost_usd"] if pos else 0
        avg_cost_usd = round((pos_cost / pos_qty) if pos_qty > 0 else price, 8)
        net_usd = round(usd_value - fee_usd - avg_cost_usd * qty, 4)

    conn.execute(
        "UPDATE journal_entries SET qty=?, price=?, usd_value=?, fee=?, fee_asset=?, fee_usd=?, "
        "avg_cost_usd=?, net_usd=?, order_id=?, note=?, created_at=? WHERE id=?",
        (qty, price, usd_value, fee, fee_asset, fee_usd, avg_cost_usd, net_usd,
         order_id, note, created_at, entry_id))

    new_d = {**old, "qty": qty, "usd_value": usd_value, "fee_usd": fee_usd, "avg_cost_usd": avg_cost_usd}
    pos = conn.execute("SELECT qty, cost_usd FROM journal_positions WHERE coin=?",
                       (old["coin"],)).fetchone()
    p_qty = pos["qty"] if pos else 0
    p_cost = pos["cost_usd"] if pos else 0
    if side in ("buy", "earn"):
        p_qty += qty
        p_cost += usd_value + fee_usd
    else:
        p_qty = max(0, p_qty - qty)
        p_cost = max(0, p_cost - avg_cost_usd * qty)
    if pos:
        conn.execute("UPDATE journal_positions SET qty=?, cost_usd=?, updated_at=? WHERE coin=?",
                    (p_qty, p_cost, now_iso(), old["coin"]))
    else:
        conn.execute("INSERT INTO journal_positions (coin, qty, cost_usd, updated_at) VALUES (?, ?, ?, ?)",
                    (old["coin"], p_qty, p_cost, now_iso()))
    conn.commit()
    conn.close()
    return {"side": side, "net_usd": net_usd}


def journal_portfolio():
    """Har bir koin bo'yicha to'liq manzara: qancha olingan, qancha sotilgan,
    sotishdan qancha sof foyda/zarar chiqqan va hozir qancha qolgan.

    Bu yerda 'yopilgan/ochiq' deb ajratilmaydi — qoldiq miqdor qaytariladi,
    ajratishni main.py joriy narxga qarab qiladi (chang qoldiqni yopilgan deb
    hisoblash uchun narx kerak).
    """
    conn = get_db()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM journal_entries ORDER BY created_at").fetchall()]
    pos = {r["coin"]: dict(r) for r in conn.execute(
        "SELECT * FROM journal_positions").fetchall()}
    conn.close()

    agg = {}
    for d in rows:
        a = agg.setdefault(d["coin"], {
            "coin": d["coin"], "bought_qty": 0.0, "sold_qty": 0.0, "earn_qty": 0.0,
            "buy_usd": 0.0, "sell_usd": 0.0, "realized_usd": 0.0,
            "sold_cost_usd": 0.0, "buy_count": 0, "sell_count": 0, "earn_count": 0,
            "first_at": d["created_at"], "last_at": d["created_at"]})
        a["first_at"] = min(a["first_at"], d["created_at"])
        a["last_at"] = max(a["last_at"], d["created_at"])
        if d["side"] in ("buy", "earn"):
            # earn — bepul kelgan koin; umumiy pozitsiya hovuziga xarid kabi qo'shiladi
            # (odatda tannarxi $0), shuning uchun o'rtacha narxni to'g'ri pasaytiradi.
            a["bought_qty"] += d["qty"]
            a["buy_usd"] += d["usd_value"] + (d["fee_usd"] or 0)
            if d["side"] == "earn":
                a["earn_qty"] += d["qty"]
                a["earn_count"] += 1
            else:
                a["buy_count"] += 1
        else:
            a["sold_qty"] += d["qty"]
            a["sell_usd"] += d["usd_value"]
            a["realized_usd"] += d["net_usd"] or 0
            a["sold_cost_usd"] += (d["avg_cost_usd"] or 0) * d["qty"]
            a["sell_count"] += 1

    out = []
    for coin, a in agg.items():
        p = pos.get(coin) or {}
        a["qty"] = p.get("qty") or 0.0
        a["cost_usd"] = round(p.get("cost_usd") or 0.0, 4)
        a["avg_price"] = round(a["cost_usd"] / a["qty"], 8) if a["qty"] > 1e-12 else 0
        a["realized_usd"] = round(a["realized_usd"], 2)
        # sotilgan qismning tannarxiga nisbatan foiz — savdo hajmi har xil bo'lgani
        # uchun foizni faqat shu asosda hisoblash to'g'ri bo'ladi
        a["realized_pct"] = (round(a["realized_usd"] / a["sold_cost_usd"] * 100, 2)
                             if a["sold_cost_usd"] > 0 else None)
        a["buy_usd"] = round(a["buy_usd"], 2)
        a["sell_usd"] = round(a["sell_usd"], 2)
        out.append(a)
    out.sort(key=lambda x: x["last_at"], reverse=True)
    return out


def journal_entry_counts():
    """Jurnaldagi operatsiyalar soni: xarid, sotish, jami."""
    conn = get_db()
    row = conn.execute(
        "SELECT COUNT(*) AS total, "
        "SUM(CASE WHEN side='buy' THEN 1 ELSE 0 END) AS buys, "
        "SUM(CASE WHEN side='sell' THEN 1 ELSE 0 END) AS sells FROM journal_entries").fetchone()
    conn.close()
    return {"total": row["total"] or 0, "buys": row["buys"] or 0, "sells": row["sells"] or 0}


def journal_stats(since_iso=None):
    """Faqat 'sell' yozuvlari asosida — realizatsiya qilingan foyda/zarar."""
    conn = get_db()
    q = "SELECT * FROM journal_entries WHERE side='sell'"
    params = ()
    if since_iso:
        q += " AND created_at >= ?"
        params = (since_iso,)
    rows = [dict(r) for r in conn.execute(q + " ORDER BY created_at", params).fetchall()]
    conn.close()

    wins = [r for r in rows if (r["net_usd"] or 0) > 0]
    losses = [r for r in rows if (r["net_usd"] or 0) <= 0]
    gross_win = round(sum(r["net_usd"] for r in wins), 2)
    gross_loss = round(-sum(r["net_usd"] for r in losses), 2)
    total_net = round(gross_win - gross_loss, 2)
    sold_value = round(sum(r["usd_value"] for r in rows), 2)
    monthly = {}
    for r in rows:
        m = r["created_at"][:7]
        d = monthly.setdefault(m, {"net_usd": 0.0, "cost_usd": 0.0, "count": 0})
        d["net_usd"] += r["net_usd"] or 0
        d["cost_usd"] += (r["avg_cost_usd"] or 0) * r["qty"]
        d["count"] += 1
    # Bo'sh oylarni ham 0 bilan to'ldiramiz — shunda grafikda faqat bitta katta
    # ustun emas, oylar ketma-ketligi ko'rinadi. Birinchi savdo oyidan (yoki so'nggi
    # 6 oydan, qaysi biri qisqaroq bo'lsa) joriy oygacha.
    uz_months = ["Yanvar","Fevral","Mart","Aprel","May","Iyun",
                 "Iyul","Avgust","Sentabr","Oktabr","Noyabr","Dekabr"]
    now = datetime.now(timezone.utc)
    # Kamida oxirgi 6 oyni ko'rsatamiz (savdo bo'lmasa ham) — shunda grafikda
    # yolg'iz bitta ustun emas, oylar ketma-ketligi ko'rinadi.
    min_span = 6
    default_total = now.year * 12 + (now.month - 1) - (min_span - 1)
    default_y, default_m = default_total // 12, default_total % 12 + 1
    if monthly:
        first_key = min(monthly.keys())
        fy, fm = int(first_key[:4]), int(first_key[5:7])
        # eng erta sana: savdo qachon boshlangan yoki oxirgi 6 oy — qaysi eртароq bo'lsa
        if (fy, fm) < (default_y, default_m):
            first_y, first_m = fy, fm
        else:
            first_y, first_m = default_y, default_m
    else:
        first_y, first_m = default_y, default_m
    # Ko'pi bilan 12 oyni ko'rsatamiz (uzoq tarix bo'lsa grafik cho'zilib ketmasin)
    span = (now.year - first_y) * 12 + (now.month - first_m) + 1
    if span > 12:
        start_total = (now.year * 12 + now.month - 1) - 11
        first_y, first_m = start_total // 12, start_total % 12 + 1
        span = 12
    monthly_out = []
    y, m = first_y, first_m
    for _ in range(span):
        key = f"{y:04d}-{m:02d}"
        d = monthly.get(key, {"net_usd": 0.0, "cost_usd": 0.0, "count": 0})
        net = round(d["net_usd"], 2)
        pct = round(net / d["cost_usd"] * 100, 2) if d["cost_usd"] > 0 else 0
        monthly_out.append({"month": key, "label": f"{uz_months[m-1]} {y}",
                             "net_usd": net, "net_pct": pct, "count": d["count"]})
        m += 1
        if m > 12:
            m = 1; y += 1
    equity, cum = [], 0.0
    for r in rows:
        cum += r["net_usd"] or 0
        equity.append({"date": r["created_at"][:10], "cum_usd": round(cum, 2)})
    return {
        "sell_count": len(rows),
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate": round(len(wins) / len(rows) * 100, 1) if rows else 0,
        "sold_value_usd": sold_value,
        "gross_win_usd": gross_win,
        "gross_loss_usd": gross_loss,
        "win_pct": round(gross_win / sold_value * 100, 2) if sold_value else 0,
        "loss_pct": round(gross_loss / sold_value * 100, 2) if sold_value else 0,
        "total_net_usd": total_net,
        "total_net_pct": round(total_net / sold_value * 100, 2) if sold_value else 0,
        "profit_factor": (round(gross_win / gross_loss, 2) if gross_loss > 0 else None),
        "monthly": monthly_out,
        "equity": equity,
    }


def ai_session_stats(session, since_iso=None):
    """Bitta session (swing yoki fast) bo'yicha statistika — paper/real alohida.
    ai_trader_stats() bilan bir xil shaklda, faqat session bo'yicha filtrlangan."""
    conn = get_db()
    q = "SELECT * FROM ai_trades WHERE exit_price IS NOT NULL AND session=?"
    params = [session]
    if since_iso:
        q += " AND closed_at >= ?"; params.append(since_iso)
    rows = [dict(r) for r in conn.execute(q + " ORDER BY closed_at", params).fetchall()]
    conn.close()

    out = {}
    for mode in ("paper", "real"):
        results = []
        for d in (r for r in rows if r["mode"] == mode):
            res = trade_result(d["entry_price"], d["exit_price"], d["amount_usd"], d["fee_rate"])
            results.append({**d, **res})
        wins = [r for r in results if r["net_usd"] > 0]
        losses = [r for r in results if r["net_usd"] <= 0]
        gross_win = round(sum(r["net_usd"] for r in wins), 2)
        gross_loss = round(-sum(r["net_usd"] for r in losses), 2)
        total_net = round(gross_win - gross_loss, 2)
        invested = round(sum(r["amount_usd"] for r in results), 2)
        equity, cum = [], 0.0
        for r in results:
            cum += r["net_usd"]
            equity.append({"date": r["closed_at"][:10], "cum_usd": round(cum, 2)})
        out[mode] = {
            "closed_count": len(results),
            "win_count": len(wins),
            "loss_count": len(losses),
            "win_rate": round(len(wins) / len(results) * 100, 1) if results else 0,
            "invested_usd": invested,
            "total_net_usd": total_net,
            "total_net_pct": round(total_net / invested * 100, 2) if invested else 0,
            "profit_factor": (round(gross_win / gross_loss, 2) if gross_loss > 0 else None),
            "equity": equity,
        }
    return out


def fast_analysis_summary(since_iso=None):
    """Tezkor tahlillar soni va shulardan nechtasi savdoga aylangani."""
    conn = get_db()
    q1 = "SELECT COUNT(*) AS c FROM analyses WHERE kind='fast'"
    q2 = "SELECT COUNT(DISTINCT analysis_id) AS c FROM ai_trades WHERE session='fast' AND analysis_id IS NOT NULL"
    p1, p2 = [], []
    if since_iso:
        q1 += " AND created_at >= ?"; p1.append(since_iso)
        q2 += " AND opened_at >= ?"; p2.append(since_iso)
    total = conn.execute(q1, p1).fetchone()["c"]
    traded = conn.execute(q2, p2).fetchone()["c"]
    conn.close()
    return {"total": total, "traded": traded}


def telegram_chat_add(chat_id, username=None):
    """Botga yozgan foydalanuvchini eslab qoladi. Qaytadi: yangi yozuvmi (True/False)."""
    conn = get_db()
    row = conn.execute("SELECT 1 FROM telegram_chats WHERE chat_id=?", (str(chat_id),)).fetchone()
    if row:
        conn.close()
        return False
    conn.execute("INSERT INTO telegram_chats (chat_id, username, added_at) VALUES (?, ?, ?)",
                (str(chat_id), username, now_iso()))
    conn.commit()
    conn.close()
    return True


def telegram_chats_list():
    conn = get_db()
    rows = conn.execute("SELECT chat_id, username FROM telegram_chats ORDER BY added_at").fetchall()
    conn.close()
    return [dict(r) for r in rows]
