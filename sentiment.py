"""Bozor kayfiyati — Fear & Greed Index (alternative.me, bepul, kalitsiz).

0-100 shkala: 0 — kuchli qo'rquv (hamma sotyapti), 100 — kuchli ochko'zlik
(bozor qizigan). Tahlillarga kontekst sifatida qo'shiladi va Bozor sahifasida
alohida karta bo'lib ko'rinadi.
"""
import requests

LABELS_UZ = {
    "Extreme Fear": "Kuchli qo'rquv",
    "Fear": "Qo'rquv",
    "Neutral": "Neytral",
    "Greed": "Ochko'zlik",
    "Extreme Greed": "Kuchli ochko'zlik",
}


def fetch(limit=8):
    """Joriy qiymat + so'nggi kunlar tarixi."""
    try:
        r = requests.get("https://api.alternative.me/fng/",
                         params={"limit": limit, "format": "json"}, timeout=20)
        if r.status_code >= 400:
            return {"error": f"HTTP {r.status_code}"}
        rows = r.json().get("data", [])
        if not rows:
            return {"error": "ma'lumot bo'sh"}
        cur = rows[0]
        history = []
        for row in reversed(rows):
            try:
                from datetime import datetime, timezone
                d = datetime.fromtimestamp(int(row["timestamp"]), tz=timezone.utc)
                history.append({"date": d.strftime("%d.%m"), "value": int(row["value"])})
            except Exception:
                continue
        label_en = cur.get("value_classification", "")
        return {
            "value": int(cur["value"]),
            "label_en": label_en,
            "label_uz": LABELS_UZ.get(label_en, label_en),
            "history": history,
        }
    except Exception as e:
        return {"error": str(e)[:200]}


def snapshot_line():
    """AI'larga beriladigan bitta qator. Xatoda None — tahlil to'xtamaydi."""
    d = fetch(2)
    if d.get("error"):
        return None
    prev = f" (kecha: {d['history'][-2]['value']})" if len(d.get("history", [])) >= 2 else ""
    return (f"Bozor kayfiyati (Fear & Greed Index): {d['value']}/100 — "
            f"{d['label_en']}{prev}")
