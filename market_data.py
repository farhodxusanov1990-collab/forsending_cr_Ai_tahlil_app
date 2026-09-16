"""Binance'dan narx ma'lumotlarini olish va indikatorlarni hisoblash (ccxt orqali)."""
import ccxt

_exchange = None


def exchange():
    global _exchange
    if _exchange is None:
        _exchange = ccxt.binance({"enableRateLimit": True})
    return _exchange


def symbol_exists(coin: str) -> bool:
    """Koin Binance'da USDT juftligi sifatida mavjudligini tekshiradi."""
    try:
        markets = exchange().load_markets()
        return f"{coin.upper()}/USDT" in markets
    except Exception:
        return False


def rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 1)


def support_resistance(ohlcv, lookback=60):
    """Oddiy usul: so'nggi davrdagi lokal cho'qqi va tublar."""
    data = ohlcv[-lookback:]
    highs = [c[2] for c in data]
    lows = [c[3] for c in data]
    piv_high, piv_low = [], []
    for i in range(2, len(data) - 2):
        if highs[i] == max(highs[i - 2:i + 3]):
            piv_high.append(highs[i])
        if lows[i] == min(lows[i - 2:i + 3]):
            piv_low.append(lows[i])
    last = data[-1][4]
    res = sorted([p for p in piv_high if p > last])[:3]
    sup = sorted([p for p in piv_low if p < last], reverse=True)[:3]
    return sup, res


def fmt(x):
    if x is None:
        return "-"
    if x >= 1000:
        return f"{x:,.0f}"
    if x >= 1:
        return f"{x:,.2f}"
    return f"{x:.6f}"


def coin_snapshot(coin: str) -> str:
    """Bitta koin bo'yicha AI'ga beriladigan matnli hisobot tayyorlaydi."""
    ex = exchange()
    pair = f"{coin.upper()}/USDT"

    daily = ex.fetch_ohlcv(pair, "1d", limit=120)
    h4 = ex.fetch_ohlcv(pair, "4h", limit=90)
    ticker = ex.fetch_ticker(pair)

    d_closes = [c[4] for c in daily]
    h_closes = [c[4] for c in h4]
    price = ticker["last"]

    ch24 = ticker.get("percentage")
    ch7 = (d_closes[-1] / d_closes[-8] - 1) * 100 if len(d_closes) >= 8 else None
    ch30 = (d_closes[-1] / d_closes[-31] - 1) * 100 if len(d_closes) >= 31 else None

    vol_avg30 = sum(c[5] for c in daily[-30:]) / 30
    vol_today = daily[-1][5]

    sup, res = support_resistance(daily)

    last7 = daily[-7:]
    candles = "\n".join(
        f"  {ex.iso8601(c[0])[:10]}: ochilish {fmt(c[1])}, yuqori {fmt(c[2])}, "
        f"past {fmt(c[3])}, yopilish {fmt(c[4])}"
        for c in last7)

    return f"""KOIN: {coin.upper()}/USDT
Joriy narx: ${fmt(price)}
O'zgarish: 24s: {ch24:+.1f}% | 7 kun: {ch7:+.1f}% | 30 kun: {ch30:+.1f}%
RSI (kunlik, 14): {rsi(d_closes)}
RSI (4 soatlik, 14): {rsi(h_closes)}
Bugungi hajm / 30 kunlik o'rtacha hajm: {vol_today / vol_avg30:.2f}x
Qo'llab-quvvatlash zonalari: {', '.join('$' + fmt(s) for s in sup) or 'aniqlanmadi'}
Qarshilik zonalari: {', '.join('$' + fmt(r) for r in res) or 'aniqlanmadi'}
So'nggi 7 kunlik shamlar:
{candles}"""


def ma(closes, period):
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def coin_snapshot_longterm(coin: str) -> str:
    """Uzoq muddatli tahlil uchun ma'lumot: haftalik shamlar, MA'lar, ATH, oylik natijalar."""
    ex = exchange()
    pair = f"{coin.upper()}/USDT"

    weekly = ex.fetch_ohlcv(pair, "1w", limit=200)      # ~4 yilgacha
    daily = ex.fetch_ohlcv(pair, "1d", limit=450)       # MA200 + zaxira
    ticker = ex.fetch_ticker(pair)
    price = ticker["last"]

    w_closes = [c[4] for c in weekly]
    d_closes = [c[4] for c in daily]

    ma50 = ma(d_closes, 50)
    ma200 = ma(d_closes, 200)
    cross = "-"
    if ma50 and ma200:
        cross = ("oltin kesishuv holati (MA50 > MA200 — uzoq muddatli o'sish tendensiyasi)"
                 if ma50 > ma200 else
                 "o'lim kesishuvi holati (MA50 < MA200 — uzoq muddatli pasayish tendensiyasi)")

    ath = max(c[2] for c in weekly)
    from_ath = (price / ath - 1) * 100

    # so'nggi 12 oy natijalari (taxminan 4 haftalik bloklar)
    monthly_lines = []
    for i in range(12, 0, -1):
        a, b = -(i * 4 + 1), -((i - 1) * 4 + 1)
        if len(w_closes) >= abs(a):
            chg = (w_closes[b] / w_closes[a] - 1) * 100
            monthly_lines.append(f"  ~{i} oy oldin blok: {chg:+.1f}%")
    monthly = "\n".join(monthly_lines) or "  yetarli tarix yo'q"

    # hajm tendensiyasi: so'nggi 4 hafta vs oldingi 12 hafta
    vol_recent = sum(c[5] for c in weekly[-4:]) / 4
    vol_prev = sum(c[5] for c in weekly[-16:-4]) / 12 if len(weekly) >= 16 else vol_recent
    vol_trend = vol_recent / vol_prev if vol_prev else 1

    # yirik haftalik qo'llab-quvvatlash/qarshilik
    sup, res = support_resistance(weekly, lookback=min(len(weekly), 150))

    ch90 = (d_closes[-1] / d_closes[-91] - 1) * 100 if len(d_closes) >= 91 else None
    ch365 = (d_closes[-1] / d_closes[-366] - 1) * 100 if len(d_closes) >= 366 else None

    return f"""KOIN: {coin.upper()}/USDT (UZOQ MUDDATLI KO'RINISH)
Joriy narx: ${fmt(price)}
90 kunlik o'zgarish: {f'{ch90:+.1f}%' if ch90 is not None else 'tarix yetarli emas'}
365 kunlik o'zgarish: {f'{ch365:+.1f}%' if ch365 is not None else 'tarix yetarli emas'}
MA50 (kunlik): ${fmt(ma50)} | MA200 (kunlik): ${fmt(ma200)}
Kesishuv holati: {cross}
Narxning MA200 ga nisbati: {f'{(price / ma200 - 1) * 100:+.1f}%' if ma200 else '-'}
Tarixiy cho'qqi (ATH, mavjud tarixda): ${fmt(ath)} | ATH'dan masofa: {from_ath:+.1f}%
RSI (haftalik, 14): {rsi(w_closes)}
Hajm tendensiyasi (so'nggi 4 hafta / oldingi 12 hafta): {vol_trend:.2f}x
Yirik haftalik qo'llab-quvvatlash: {', '.join('$' + fmt(s) for s in sup) or 'aniqlanmadi'}
Yirik haftalik qarshilik: {', '.join('$' + fmt(r) for r in res) or 'aniqlanmadi'}
So'nggi 12 ta 4-haftalik blok natijalari:
{monthly}"""


def move_snapshot(coin: str) -> str:
    """'Nima bo'ldi?' uchun qisqa harakat ma'lumoti: 1s/4s/24s o'zgarish, hajm."""
    ex = exchange()
    pair = f"{coin.upper()}/USDT"
    h1 = ex.fetch_ohlcv(pair, "1h", limit=26)
    ticker = ex.fetch_ticker(pair)
    closes = [c[4] for c in h1]
    price = ticker["last"]
    ch1 = (closes[-1] / closes[-2] - 1) * 100 if len(closes) >= 2 else 0
    ch4 = (closes[-1] / closes[-5] - 1) * 100 if len(closes) >= 5 else 0
    ch24 = ticker.get("percentage") or 0
    vol_recent = sum(c[5] for c in h1[-4:])
    vol_prev = sum(c[5] for c in h1[-24:-4]) / 5 if len(h1) >= 24 else vol_recent
    candles = "\n".join(
        f"  {ex.iso8601(c[0])[11:16]}: {fmt(c[1])} -> {fmt(c[4])} (hajm {c[5]:,.0f})"
        for c in h1[-6:])
    return f"""KOIN: {coin.upper()}/USDT
Joriy narx: ${fmt(price)}
O'zgarish: 1 soat: {ch1:+.2f}% | 4 soat: {ch4:+.2f}% | 24 soat: {ch24:+.2f}%
So'nggi 4 soat hajmi / oldingi o'rtacha 4 soatlik hajm: {vol_recent / vol_prev:.2f}x
So'nggi 6 soatlik shamlar:
{candles}"""


def biggest_mover(coins):
    """Ro'yxatdan eng katta harakat qilgan koinni topadi."""
    ex = exchange()
    best, best_score, best_info = None, -1, None
    for c in coins:
        try:
            pair = f"{c.upper()}/USDT"
            h1 = ex.fetch_ohlcv(pair, "1h", limit=6)
            t = ex.fetch_ticker(pair)
            closes = [x[4] for x in h1]
            ch1 = abs((closes[-1] / closes[-2] - 1) * 100) if len(closes) >= 2 else 0
            ch4 = abs((closes[-1] / closes[-5] - 1) * 100) if len(closes) >= 5 else 0
            ch24 = abs(t.get("percentage") or 0)
            score = max(ch1 * 3, ch4 * 1.5, ch24)
            if score > best_score:
                best, best_score = c, score
                best_info = f"1s: {(closes[-1]/closes[-2]-1)*100:+.2f}%, 24s: {(t.get('percentage') or 0):+.2f}%"
        except Exception:
            continue
    return best, best_info


MAJOR_COINS = ["BTC", "ETH", "SOL", "XRP", "BNB", "DOGE", "ADA", "AVAX", "LINK", "TON"]


def market_overview(watch_coins=None):
    """Butun bozor holati — kuzatuv ro'yxatiga bog'liq emas.

    Doim eng yirik koinlar (MAJOR_COINS) olinadi; treyderning kuzatuv ro'yxati
    faqat qo'shimcha ma'lumot sifatida alohida beriladi.
    """
    ex = exchange()
    rows = []
    for c in MAJOR_COINS:
        try:
            t = ex.fetch_ticker(f"{c}/USDT")
            h1 = ex.fetch_ohlcv(f"{c}/USDT", "1h", limit=26)
            cl = [x[4] for x in h1]
            ch1 = (cl[-1] / cl[-2] - 1) * 100 if len(cl) >= 2 else 0
            ch4 = (cl[-1] / cl[-5] - 1) * 100 if len(cl) >= 5 else 0
            ch24 = t.get("percentage") or 0
            vol_recent = sum(x[5] for x in h1[-4:])
            vol_prev = sum(x[5] for x in h1[-24:-4]) / 5 if len(h1) >= 24 else vol_recent
            rows.append({"coin": c, "price": t["last"], "ch1": ch1, "ch4": ch4,
                         "ch24": ch24, "vol": vol_recent / vol_prev if vol_prev else 1})
        except Exception:
            continue

    if not rows:
        return "BUTUN BOZOR: ma'lumot olinmadi"

    lines = "\n".join(
        f"  {r['coin']}: ${fmt(r['price'])} | 1s {r['ch1']:+.2f}% | 4s {r['ch4']:+.2f}% | "
        f"24s {r['ch24']:+.2f}% | hajm {r['vol']:.2f}x" for r in rows)

    btc = next((r for r in rows if r["coin"] == "BTC"), None)
    alts = [r for r in rows if r["coin"] != "BTC"]
    avg_alt = sum(r["ch24"] for r in alts) / len(alts) if alts else 0
    up = sum(1 for r in rows if r["ch24"] > 0)

    breadth = (f"Kengligi: {up}/{len(rows)} yirik koin 24 soatda o'sishda.\n"
               f"Altkoinlarning o'rtacha 24s o'zgarishi: {avg_alt:+.2f}%")
    if btc:
        diff = avg_alt - btc["ch24"]
        breadth += (f"\nAltkoinlar BTC'dan {diff:+.2f} foiz punktga farq qilyapti "
                    f"({'altkoinlar kuchliroq' if diff > 0 else 'BTC kuchliroq'})")

    strongest = max(rows, key=lambda r: r["ch24"])
    weakest = min(rows, key=lambda r: r["ch24"])
    extremes = (f"Eng kuchli: {strongest['coin']} {strongest['ch24']:+.2f}% | "
                f"Eng zaif: {weakest['coin']} {weakest['ch24']:+.2f}%")

    out = ("BUTUN KRIPTO BOZORI (eng yirik koinlar)\n" + lines + "\n\n" +
           breadth + "\n" + extremes)

    # Treyderning ro'yxati — faqat qo'shimcha kontekst
    extra = [c.upper() for c in (watch_coins or []) if c.upper() not in MAJOR_COINS]
    if extra:
        wl = []
        for c in extra:
            try:
                t = ex.fetch_ticker(f"{c}/USDT")
                wl.append(f"  {c}: ${fmt(t['last'])} | 24s {(t.get('percentage') or 0):+.2f}%")
            except Exception:
                continue
        if wl:
            out += ("\n\nQo'shimcha ma'lumot — treyderning kuzatuv ro'yxati "
                    "(bozor xulosasi bunga qarab emas, yuqoridagi yirik koinlarga "
                    "qarab chiqarilsin):\n" + "\n".join(wl))
    return out
