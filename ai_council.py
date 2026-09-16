"""AI kengashi — modulli tizim.

Tahlilchilar (sozlamada yoqiladi/o'chiriladi): Qwen, DeepSeek, GPT, Gemini.
Hakam: Claude (ishlamasa — mavjud birinchi AI).
Tahlilchilar ingliz tilida ishlaydi (sifat yuqoriroq), hakam yakuniy xulosani
o'zbek tilida, qat'iy JSON shaklida qaytaradi — sahifada vizual karta bo'lib chiqadi.
"""
import os
import json
import requests

TIMEOUT = 150


def _sanitize(text: str) -> str:
    """Xato matnlaridan API kalitlarini o'chiradi."""
    for env in ("OPENAI_API_KEY", "GEMINI_API_KEY", "ANTHROPIC_API_KEY",
                "DEEPSEEK_API_KEY", "QWEN_API_KEY", "BINANCE_API_KEY", "BINANCE_SECRET"):
        val = os.environ.get(env)
        if val:
            text = text.replace(val, "***KALIT***")
    return text


def _raise_with_body(r):
    """Xato bo'lsa, server javobidagi aniq izohni ham qo'shib ko'taradi."""
    if r.status_code >= 400:
        body = ""
        try:
            body = r.text[:400]
        except Exception:
            pass
        raise RuntimeError(f"HTTP {r.status_code}: {body}")


# ---------- Har bir provayderga so'rov ----------

def _openai_compat(url, key, model, system, user):
    r = requests.post(url,
        headers={"Authorization": f"Bearer {key}"},
        json={"model": model,
              "messages": [{"role": "system", "content": system},
                           {"role": "user", "content": user}]},
        timeout=TIMEOUT)
    _raise_with_body(r)
    return r.json()["choices"][0]["message"]["content"]


def _call_gpt(system, user):
    return _openai_compat("https://api.openai.com/v1/chat/completions",
                          os.environ["OPENAI_API_KEY"],
                          os.environ.get("OPENAI_MODEL", "gpt-5-mini"), system, user)


def _call_deepseek(system, user):
    return _openai_compat(
        os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/chat/completions"),
        os.environ["DEEPSEEK_API_KEY"],
        os.environ.get("DEEPSEEK_MODEL", "deepseek-chat"), system, user)


def _call_qwen(system, user):
    return _openai_compat(
        os.environ.get("QWEN_BASE_URL",
                       "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions"),
        os.environ["QWEN_API_KEY"],
        os.environ.get("QWEN_MODEL", "qwen-max"), system, user)


def _call_gemini(system, user):
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{os.environ.get('GEMINI_MODEL', 'gemini-2.5-flash')}:generateContent",
        params={"key": os.environ["GEMINI_API_KEY"]},
        json={"system_instruction": {"parts": [{"text": system}]},
              "contents": [{"role": "user", "parts": [{"text": user}]}]},
        timeout=TIMEOUT)
    _raise_with_body(r)
    return r.json()["candidates"][0]["content"]["parts"][0]["text"]


def _call_claude(system, user):
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"],
                 "anthropic-version": "2023-06-01"},
        json={"model": os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5"),
              "max_tokens": 3000, "system": system,
              "messages": [{"role": "user", "content": user}]},
        timeout=TIMEOUT)
    _raise_with_body(r)
    return "".join(b.get("text", "") for b in r.json()["content"])


PROVIDERS = {
    "qwen": {
        "name": "Qwen", "env": "QWEN_API_KEY", "fn": _call_qwen,
        "role": ("You are a disciplined crypto technical analyst. You rely strictly on the "
                 "given numbers, define precise entry/exit zones, and never hype. "
                 "SPOT trading only: you may recommend buying, selling a held position, "
                 "or staying out — never short selling.")},
    "deepseek": {
        "name": "DeepSeek", "env": "DEEPSEEK_API_KEY", "fn": _call_deepseek,
        "role": ("You are an opportunity-seeking crypto analyst. You look for upside "
                 "scenarios and momentum the market may be missing, but you always state "
                 "the exact invalidation level that kills your idea. SPOT only — never "
                 "suggest short selling.")},
    "gpt": {
        "name": "GPT", "env": "OPENAI_API_KEY", "fn": _call_gpt,
        "role": ("You are a crypto technical analyst. Structured, precise, no hype. "
                 "SPOT only — never suggest short selling.")},
    "gemini": {
        "name": "Gemini", "env": "GEMINI_API_KEY", "fn": _call_gemini,
        "role": ("You are a crypto risk analyst. You stress-test every scenario, point out "
                 "weak assumptions and false-signal risks. SPOT only — never suggest "
                 "short selling.")},
    "claude": {
        "name": "Claude", "env": "ANTHROPIC_API_KEY", "fn": _call_claude, "role": ""},
}

JUDGE_CHAIN = ["claude", "gpt", "deepseek", "qwen", "gemini"]

MARKUP = "\\n\\nMATNNI BELGILASH (majburiy):\\n- Ijobiy/kuchli signalni [+]shunday[/+] ichiga ol (o'sish tasdig'i, kuchli hajm, sotib olish imkoniyati)\\n- Salbiy signal yoki xavfni [-]shunday[/-] ichiga ol (tushish xavfi, soxta chiqish, tavsiya etilmaydi, zarar ehtimoli)\\n- Shartli/kutish holatini [~]shunday[/~] ichiga ol (tasdiq kerak, kuzatish lozim, noaniq)\\n- Treyder uchun amaliy qadamni (nima qilish kerakligi) [!]shunday[/!] ichiga ol — har xulosada kamida bitta shunday bo'lsin\\nFaqat eng muhim 4-8 ta joyni belgila, butun matnni emas. Belgilar matn ichida aynan shu ko'rinishda yozilsin."


def has_key(pid):
    return bool(os.environ.get(PROVIDERS[pid]["env"]))


def _safe(pid, system, user):
    p = PROVIDERS[pid]
    if not has_key(pid):
        return f"({p['name']} kaliti sozlanmagan)"
    try:
        return p["fn"](system, user).strip()
    except Exception as e:
        return f"({p['name']} javob bermadi: {_sanitize(str(e))})"


def _failed(text):
    return text.startswith("(") and ("javob bermadi" in text or "sozlanmagan" in text)


def test_provider(pid):
    """AI aktivligini tekshirish uchun mitti so'rov."""
    if not has_key(pid):
        return {"id": pid, "name": PROVIDERS[pid]["name"], "ok": False,
                "error": "kalit sozlanmagan"}
    try:
        out = PROVIDERS[pid]["fn"]("Reply with exactly: OK", "ping")
        return {"id": pid, "name": PROVIDERS[pid]["name"], "ok": True,
                "sample": (out or "").strip()[:40]}
    except Exception as e:
        return {"id": pid, "name": PROVIDERS[pid]["name"], "ok": False,
                "error": _sanitize(str(e))[:300]}


# ---------- Hakam JSON'ini o'qish ----------

def parse_verdict(text):
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.lower().startswith("json"):
            t = t[4:]
    a, b = t.find("{"), t.rfind("}")
    if a == -1 or b == -1:
        return None
    try:
        v = json.loads(t[a:b + 1])
    except Exception:
        return None
    if v.get("action") not in ("BUY", "SELL", "WAIT"):
        return None
    return v


# ---------- Asosiy jarayon ----------

def run_council(snapshot, profile, horizon="swing", analysts=None, on_step=None):
    def step(t):
        if on_step:
            try:
                on_step(t)
            except Exception:
                pass
    analysts = [a for a in (analysts or ["qwen", "deepseek"]) if a in PROVIDERS and a != "claude"]
    if not analysts:
        analysts = ["qwen", "deepseek"]

    if horizon == "long":
        task = ("Write a LONG-TERM analysis in English based only on this data. Focus on the "
                "major trend (MA cross, weekly RSI), market cycle stage, distance from ATH and "
                "volume trend. Give conditional scenarios for 1 month, 2-3 months, 6 months and "
                "1 year (e.g. 'if price holds above MA200...'). Never promise exact prices; "
                "speak in zones and conditions. SPOT only.")
    elif horizon == "fast":
        task = ("Write a FAST INTRADAY analysis in English based only on this short-term data "
                "(1h/4h moves, recent candles, volume). This is for a trade that will likely "
                "close within hours, not days. Give a TIGHT stop (close to current price) and "
                "a realistic near-term target (do not use wide swing-style ranges). If the move "
                "already happened and there is no fresh edge, say so clearly — do not force a "
                "trade. Never suggest short selling.")
    else:
        task = ("Write your analysis in English based only on this data: current state, likely "
                "scenarios, entry/exit zones for a SPOT swing trader, and your confidence level. "
                "Never suggest short selling.")

    base = f"Trader profile: {profile}\n\nMarket data:\n{snapshot}\n\n{task}"

    discussion = {}
    r1 = {}
    for a in analysts:
        step(f"{PROVIDERS[a]['name']} birinchi tahlilni yozmoqda…")
        r1[a] = _safe(a, PROVIDERS[a]["role"], base)
        discussion[f"{a}_1"] = r1[a]

    r2 = {}
    for a in analysts:
        others = "\n\n".join(
            f"=== Analysis by {PROVIDERS[o]['name']} ===\n{r1[o]}"
            for o in analysts if o != a and not _failed(r1[o]))
        if not others or _failed(r1[a]):
            r2[a] = r1[a]
            continue
        step(f"{PROVIDERS[a]['name']} boshqalarning fikriga javob bermoqda…")
        r2[a] = _safe(a, PROVIDERS[a]["role"],
                      f"{base}\n\nYour earlier analysis:\n{r1[a]}\n\n"
                      f"Other analysts' views:\n{others}\n\n"
                      "Considering their points, give your short final take in English: "
                      "what do you agree with, where do you stand firm?")
        discussion[f"{a}_2"] = r2[a]

    all_views = "\n\n".join(
        f"=== {PROVIDERS[a]['name']} (round 1) ===\n{r1[a]}\n\n"
        f"=== {PROVIDERS[a]['name']} (round 2) ===\n{r2[a]}"
        for a in analysts)

    if horizon == "fast":
        ids = ", ".join(f'"{a}"' for a in analysts)
        judge_system = ("Sen hakam-jamlovchisan, TEZKOR (kun ichi) savdo uchun xulosa berasan. "
                        "Javobing FAQAT bitta JSON obyekt bo'lsin — hech qanday qo'shimcha matn, "
                        "izoh yoki ``` belgilarisiz. Barcha matn maydonlari O'ZBEK TILIDA. "
                        "SPOT savdo: BUY = sotib olish tavsiyasi, SELL = koin qo'lda bo'lsa "
                        "sotish, WAIT = kutish. Short yo'q. Bu kun ichi savdo — stop va maqsad "
                        "swing tahlildagidan ANCHA YAQINROQ bo'lishi kerak (odatda 1-3%). Agar "
                        "yaqinda kuchli harakat allaqachon bo'lib o'tgan bo'lsa va yangi edge "
                        "yo'q bo'lsa, ochiq WAIT deb yoz — signal o'ylab topma.")
        judge_user = (f"Treyder profili: {profile}\n\nQisqa muddatli bozor ma'lumoti:\n{snapshot}\n\n"
                      f"{all_views}\n\n"
                      "Shu maydonlar bilan JSON qaytar:\n"
                      '{\n'
                      '  "action": "BUY" | "SELL" | "WAIT",\n'
                      '  "confidence": 0-100 (butun son),\n'
                      '  "risk": "past" | "o\'rta" | "yuqori",\n'
                      '  "entry_low": son yoki null,\n'
                      '  "entry_high": son yoki null,\n'
                      '  "stop": son yoki null (joriy narxga yaqin, 1-3%),\n'
                      '  "targets": [son] (bitta yaqin maqsad, bo\'sh bo\'lishi mumkin),\n'
                      '  "comment": "1-2 gap qisqa o\'zbekcha izoh, kun ichi harakatga xos. '
                      'Muhim joylarni belgila: [+]ijobiy[/+] [-]xavf[/-] [~]kutish[/~] '
                      '[!]amaliy qadam[/!] — kamida bitta [!] bo\'lsin",\n'
                      '  "disagreement": "",\n'
                      f'  "analyst_summaries": {{{ids}}} — har biri 1 gaplik qisqa bayon\n'
                      '}\n'
                      "action=WAIT bo'lsa entry/stop/targets null bo'lishi mumkin.")
    elif horizon == "long":
        judge_system = ("Sen hakam-jamlovchisan. Tahlilchilar inglizcha yozgan, sen yakuniy "
                        "xulosani O'ZBEK TILIDA yozasan. Inglizcha atama ishlatsang, qavsda "
                        "qisqa izoh ber. Spot treyder uchun yoz — short haqida gapirma."
                        + MARKUP)
        judge_user = (f"Treyder profili: {profile}\n\nBozor ma'lumotlari:\n{snapshot}\n\n"
                      f"{all_views}\n\n"
                      "Yakuniy xulosani o'zbekcha yoz. Tuzilishi:\n"
                      "1) Yirik trend holati (1-2 gap)\n"
                      "2) 1 oylik istiqbol: asosiy va muqobil stsenariy (shartlari bilan)\n"
                      "3) 2-3 oylik istiqbol\n4) 6 oylik istiqbol\n"
                      "5) 1 yillik yo'nalish va uni bekor qiladigan shart\n"
                      "6) Uzoq muddatli xaridor uchun qiziq zonalar va xatar darajasi\n"
                      "Muddat uzaygan sari noaniqlik oshishini ochiq ayt. Oxirida: bu "
                      "moliyaviy maslahat emas, yakuniy qaror treyderniki.")
    else:
        ids = ", ".join(f'"{a}"' for a in analysts)
        judge_system = ("Sen hakam-jamlovchisan. Javobing FAQAT bitta JSON obyekt bo'lsin — "
                        "hech qanday qo'shimcha matn, izoh yoki ``` belgilarisiz. Barcha matn "
                        "maydonlari O'ZBEK TILIDA. Inglizcha atama ishlatsang qavsda izohla. "
                        "SPOT savdo: BUY = sotib olish tavsiyasi, SELL = koin qo'lda bo'lsa "
                        "sotish, WAIT = kutish. Short yo'q.")
        judge_user = (f"Treyder profili: {profile}\n\nBozor ma'lumotlari:\n{snapshot}\n\n"
                      f"{all_views}\n\n"
                      "Shu maydonlar bilan JSON qaytar:\n"
                      '{\n'
                      '  "action": "BUY" | "SELL" | "WAIT",\n'
                      '  "confidence": 0-100 (butun son),\n'
                      '  "risk": "past" | "o\'rta" | "yuqori",\n'
                      '  "entry_low": son yoki null,\n'
                      '  "entry_high": son yoki null,\n'
                      '  "stop": son yoki null,\n'
                      '  "targets": [son, ...] (maksimum 3 ta, bo\'sh bo\'lishi mumkin),\n'
                      '  "comment": "2-3 gap qisqa o\'zbekcha izoh. Muhim joylarni belgila: '
                      'ijobiy signal [+]matn[/+], xavf [-]matn[/-], kutish yoki shart '
                      '[~]matn[/~], treyder uchun amaliy qadam [!]matn[/!] — kamida bitta '
                      '[!] bo\'lsin",\n'
                      '  "disagreement": "tahlilchilar kelishmagan joy va sening hukming, '
                      '1-2 gap (kelishgan bo\'lsa bo\'sh qator)",\n'
                      f'  "analyst_summaries": {{{ids}}} — har bir tahlilchi fikrining '
                      '2-3 gaplik o\'zbekcha qisqa bayoni\n'
                      '}\n'
                      "action=WAIT bo'lsa entry/stop/targets null yoki bo'sh bo'lishi mumkin. "
                      "Raqamlarni tahlilchilar bergan darajalardan ol, o'zing o'ylab topma.")

    verdict = None
    summary = None
    for pid in JUDGE_CHAIN:
        if not has_key(pid):
            continue
        step(f"{PROVIDERS[pid]['name']} hakam sifatida yakuniy xulosa chiqarmoqda…")
        out = _safe(pid, judge_system, judge_user)
        if _failed(out):
            discussion[f"hakam_xato_{pid}"] = out
            continue
        if horizon == "long":
            summary = out
            if pid != "claude":
                summary += f"\n\n(Eslatma: hakam vazifasini {PROVIDERS[pid]['name']} bajardi)"
            break
        verdict = parse_verdict(out)
        if verdict:
            if pid != "claude":
                verdict["judge_note"] = f"Hakam: {PROVIDERS[pid]['name']} (Claude javob bermadi)"
            summary = json.dumps(verdict, ensure_ascii=False)
            break
        discussion[f"hakam_json_xato_{pid}"] = out[:2000]

    if summary is None:
        summary = "(Hech bir AI hakamlik qila olmadi — sozlamalardagi 'AI'larni tekshirish' tugmasi bilan sabablarni ko'ring)"

    return {"summary": summary, "verdict": verdict, "discussion": discussion}


def run_flash(coin, move_data, headlines, news_note, positions_note, profile,
              analysts=None, scope="coin", on_step=None):
    """'Nima bo'ldi?' — tezkor tahlil.

    scope='coin'   — bitta koin bo'yicha
    scope='market' — butun kripto bozori (BTC + altkoinlar) nega o'sdi/tushdi
    """
    def step(t):
        if on_step:
            try:
                on_step(t)
            except Exception:
                pass
    analysts = [a for a in (analysts or ["qwen", "deepseek"]) if a in PROVIDERS and a != "claude"]
    if not analysts:
        analysts = ["qwen", "deepseek"]

    news_block = f"Recent crypto news headlines:\n{headlines}" if headlines else \
                 f"(No news available: {news_note})"

    if scope == "market":
        subject = "the overall crypto market"
        task = ("IMPORTANT: this is a MARKET-WIDE analysis. Judge the market by the major "
                "coins listed (BTC, ETH, SOL, XRP, BNB and the rest) and the breadth numbers. "
                "Do NOT build your conclusion around any single small altcoin, even if it "
                "shows the largest percentage move — mention such a coin only in passing, if "
                "at all.\n"
                "In English, briefly (max 10 sentences): 1) what happened across the market as "
                "a whole — is this a broad move led by BTC, a rotation into altcoins, or mixed/"
                "flat, 2) which of these news items, if any, plausibly explains it — if none "
                "clearly does, say so explicitly instead of inventing a cause, 3) does this look "
                "like a short-lived reaction or the start of a bigger shift, and why.")
    else:
        subject = f"{coin}"
        task = ("In English, briefly (max 10 sentences): 1) what exactly happened in the price, "
                "2) which of these news items, if any, plausibly explains it — if none clearly "
                "does, say so explicitly instead of inventing a cause, 3) does this look like a "
                "short-lived reaction or the start of something bigger, and why. SPOT only.")

    base = (f"Trader profile: {profile}\n\nA notable move just happened in {subject}.\n\n"
            f"{move_data}\n\n{news_block}\n\n{task}")

    discussion = {}
    views = []
    for a in analysts:
        step(f"{PROVIDERS[a]['name']} tahlil qilmoqda…")
        out = _safe(a, PROVIDERS[a]["role"], base)
        discussion[f"{a}_1"] = out
        if not _failed(out):
            views.append(f"=== {PROVIDERS[a]['name']} ===\n{out}")

    judge_system = ("Sen hakam-jamlovchisan. Tahlilchilar inglizcha yozgan, sen O'ZBEK TILIDA "
                    "qisqa (maksimum 10 gap) xulosa yozasan. Inglizcha atamani qavsda izohla. "
                    "MUHIM: sababni faqat berilgan yangiliklar tasdiqlasa ayt; ishonchli "
                    "bog'liqlik bo'lmasa 'aniq sabab ko'rinmayapti, bozor harakati bo'lishi "
                    "mumkin' deb ochiq yoz — uydirma sabab yozma." + MARKUP)

    if scope == "market":
        structure = ("MUHIM: bu BUTUN BOZOR tahlili. Xulosani yirik koinlar (BTC, ETH, SOL, "
                     "XRP, BNB va boshqalar) va kenglik raqamlariga qarab chiqar. Bironta "
                     "kichik altkoinni markazga qo'yma — u eng katta foiz harakat qilgan "
                     "bo'lsa ham, faqat o'tkinchi eslatma sifatida tilga ol.\n\n"
                     "Xulosa tuzilishi:\n"
                     "1) Bozorda nima bo'ldi (BTC va yirik koinlar raqamlari bilan, 1-2 gap)\n"
                     "2) Harakat umumiymi (BTC boshchiligida hamma qimirlaganmi), altkoinlarga "
                     "oqim bormi, yoki bozor tarqoq/tinchmi\n"
                     "3) Ehtimoliy sabab — yangiliklarga tayanib; ishonchli sabab topilmasa "
                     "ochiq ayt\n"
                     "4) Bu qisqa muddatli reaksiyami yoki jiddiyroq o'zgarish belgisimi\n"
                     "5) Kuzatuvchi uchun 1-2 gaplik amaliy fikr\n"
                     "Bu moliyaviy maslahat emasligini bir gap bilan eslat.")
    else:
        structure = ("Xulosa tuzilishi:\n"
                     "1) Nima bo'ldi (raqamlar bilan, 1-2 gap)\n"
                     "2) Ehtimoliy sabab (yangiliklarga tayanib; topilmasa — ochiq ayt)\n"
                     "3) Bu qisqa muddatli reaksiyami yoki jiddiyroq o'zgarish belgisimi\n"
                     "4) Kuzatuvchi/pozitsiya egasi uchun 1-2 gaplik amaliy fikr\n"
                     "Bu moliyaviy maslahat emasligini bir gap bilan eslat.")

    judge_user = (f"{move_data}\n\n{news_block}\n\n{positions_note}\n\n"
                  + "\n\n".join(views) + "\n\n" + structure)

    summary = None
    for pid in JUDGE_CHAIN:
        if not has_key(pid):
            continue
        step(f"{PROVIDERS[pid]['name']} hakam xulosa yozmoqda…")
        out = _safe(pid, judge_system, judge_user)
        if not _failed(out):
            summary = out
            if pid != "claude":
                summary += f"\n\n(Hakam: {PROVIDERS[pid]['name']})"
            break
        discussion[f"hakam_xato_{pid}"] = out

    if summary is None:
        summary = "(Hech bir AI javob bermadi — 'AI'larni tekshirish' bilan sabablarni ko'ring)"
    if news_note:
        summary = f"\u26a0\ufe0f {news_note}\n\n{summary}"
    return {"summary": summary, "discussion": discussion}
