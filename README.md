# Kripto stol — shaxsiy AI tahlil va savdo jurnali

Kuniga 2 marta (09:00 va 18:30, Toshkent) Binance'dan ma'lumot olib, uchta AI
(GPT — texnik tahlilchi, Gemini — xatarlar tahlilchisi, Claude — hakam) muhokamasidan
o'tkazadi va yakuniy xulosani o'zbek tilida sahifaga chiqaradi. Savdo jurnali
Binance komissiyasini (standart 0.1% har tomonga) avtomatik hisoblaydi.

## O'rnatish (lokal sinov)

```bash
pip install -r requirements.txt
cp .env.example .env        # kalitlarni yozing
python main.py              # http://localhost:8000
```

Linux/Mac'da `.env` ni yuklash: `export $(cat .env | xargs)` yoki `python-dotenv` qo'shing.

## Railway'ga joylash

1. Kodni GitHub'ga yuklang, Railway'da "New Project → Deploy from GitHub"
2. Variables bo'limiga `.env.example` dagi kalitlarni kiriting
3. **Muhim:** Volume qo'shing (masalan `/data`) va `DB_PATH=/data/data.db` qiling —
   aks holda har qayta joylashda tarix o'chib ketadi
4. Ochilgan manzilga kirsangiz sahifa ishlaydi

## Model nomlari haqida

AI modellari tez yangilanadi. Agar biror API "model topilmadi" desa, `.env` dagi
model nomini o'sha kompaniyaning hujjatidagi joriy nom bilan almashtiring —
kod o'zgarmaydi.

## Eslatma

Bu tizim bashorat bermaydi — mavjud ma'lumot tahlilini beradi. Yakuniy qaror
har doim o'zingizda. Statistika kamida 20-30 yopilgan savdodan keyin ma'noli bo'ladi.
