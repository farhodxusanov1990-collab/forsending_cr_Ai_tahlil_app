"""Kripto yangiliklar — yirik nashrlarning RSS lentalaridan (bepul, kalitsiz).

Manbalar: CoinDesk, Cointelegraph, Decrypt. Sarlavhalar olinadi, koin bo'yicha
filtrlash so'z chegarasi bilan qilinadi (masalan "sui" so'zi "lawsuit" ichidan
topilib qolmasligi uchun).
"""
import re
import requests
import xml.etree.ElementTree as ET

FEEDS = [
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Cointelegraph", "https://cointelegraph.com/rss"),
    ("Decrypt", "https://decrypt.co/feed"),
]

# Koin belgisiga mos qidiruv so'zlari
COIN_WORDS = {
    "BTC": ["bitcoin", "btc"],
    "ETH": ["ethereum", "eth"],
    "SOL": ["solana", "sol"],
    "XRP": ["xrp", "ripple"],
    "BNB": ["bnb", "binance coin"],
    "DOGE": ["dogecoin", "doge"],
    "ADA": ["cardano", "ada"],
    "AVAX": ["avalanche", "avax"],
    "LINK": ["chainlink", "link"],
    "SUI": ["sui"],
    "TON": ["toncoin", "ton"],
    "TRX": ["tron", "trx"],
    "DOT": ["polkadot", "dot"],
    "LTC": ["litecoin", "ltc"],
    "PEPE": ["pepe"],
    "SHIB": ["shiba", "shib"],
}


def _fetch_feed(name, url):
    items = []
    try:
        r = requests.get(url, timeout=20,
                         headers={"User-Agent": "Mozilla/5.0 (kripto-stol)"})
        if r.status_code >= 400:
            return items
        root = ET.fromstring(r.content)
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            pub = (item.findtext("pubDate") or "")[:22]
            if title:
                items.append((name, pub, title))
    except Exception:
        pass
    return items


def get_headlines(coin=None, limit=15):
    """(matn, izoh) qaytaradi — main.py bilan mos. Kalit kerak emas."""
    all_items = []
    for name, url in FEEDS:
        all_items.extend(_fetch_feed(name, url))

    if not all_items:
        return None, "Yangiliklar manbalariga ulanib bo'lmadi"

    matched, general = [], []
    if coin:
        words = COIN_WORDS.get(coin.upper(), [coin.lower()])
        pattern = re.compile(r"\b(" + "|".join(re.escape(w) for w in words) + r")\b",
                             re.IGNORECASE)
        for src, pub, title in all_items:
            (matched if pattern.search(title) else general).append((src, pub, title))
    else:
        general = all_items

    chosen = matched[:limit]
    note = None
    if len(chosen) < 3:
        # Koin bo'yicha yangilik kam — umumiy bozor sarlavhalari bilan to'ldiramiz
        need = min(limit, 10) - len(chosen)
        chosen += [(s, p, f"(umumiy bozor) {t}") for s, p, t in general[:need]]
        if coin and not matched:
            note = f"{coin.upper()} bo'yicha alohida yangilik topilmadi — umumiy bozor sarlavhalari berildi"

    lines = [f"- [{pub}] ({src}) {title}" for src, pub, title in chosen]
    return "\n".join(lines), note
