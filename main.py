# main.py — Full Final Ready-to-Use Version + Anti-Spam
import logging
import math
import requests
import random
import time
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes, filters
import db
from config import BOT_TOKEN

# ---------------- logging ----------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("anonchat-final-ready")

# ---------------- startup ----------------
db.init_db()

# ---------------- constants ----------------
NOMINATIM_URL = "https://nominatim.openstreetmap.org/reverse"
NOMINATIM_HEADERS = {"User-Agent": "AnonChatBot/1.0 (contact: ujangkasbon97@gmail.com)"}
CHAT_RATE_LIMIT = 2  # detik per pesan

# ---------------- anti-spam ----------------
last_msg_time = {}  # user_id -> timestamp

def can_send(user_id):
    now = time.time()
    last = last_msg_time.get(user_id, 0)
    if now - last < CHAT_RATE_LIMIT:
        return False
    last_msg_time[user_id] = now
    return True

# ---------------- helpers ----------------
def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dlambda/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c

def reverse_geocode_city(lat, lon):
    try:
        params = {"lat": str(lat), "lon": str(lon), "format": "jsonv2", "addressdetails": 1, "zoom": 10}
        resp = requests.get(NOMINATIM_URL, params=params, headers=NOMINATIM_HEADERS, timeout=8)
        if resp.status_code != 200:
            return None
        data = resp.json()
        addr = data.get("address", {})
        city = addr.get("city") or addr.get("town") or addr.get("village") or addr.get("county") or addr.get("state")
        if city:
            return str(city).strip().title()
    except Exception as e:
        logger.warning("reverse_geocode_city error: %s", e)
    return None

# ---------------- DB helpers ----------------
def add_to_queue(user_id):
    conn = db.get_db()
    cur = conn.cursor()
    try:
        user = db.get_user(user_id)
        gender = user["gender"] if user else None
        age = user["age"] if user else None
        cur.execute("INSERT OR REPLACE INTO queue (user_id, gender, age) VALUES (?, ?, ?)", (user_id, gender, age))
        cur.execute("UPDATE users SET status='searching' WHERE user_id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()

def remove_from_queue(user_id):
    conn = db.get_db()
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM queue WHERE user_id = ?", (user_id,))
        cur.execute("UPDATE users SET status='idle' WHERE user_id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()

def queue_get_candidates_by_city(city, exclude_user_id=None):
    conn = db.get_db()
    cur = conn.cursor()
    try:
        if exclude_user_id:
            cur.execute("""
                SELECT q.user_id, u.latitude, u.longitude, u.location, u.gender, u.age
                FROM queue q JOIN users u ON q.user_id = u.user_id
                WHERE u.location = ? AND q.user_id != ?
            """, (city, exclude_user_id))
        else:
            cur.execute("""
                SELECT q.user_id, u.latitude, u.longitude, u.location, u.gender, u.age
                FROM queue q JOIN users u ON q.user_id = u.user_id
                WHERE u.location = ?
            """, (city,))
        rows = cur.fetchall()
        return [{"user_id": r[0], "latitude": r[1], "longitude": r[2], "location": r[3], "gender": r[4], "age": r[5]} for r in rows]
    finally:
        conn.close()

def create_pairing(a_id, b_id):
    conn = db.get_db()
    cur = conn.cursor()
    try:
        cur.execute("INSERT OR REPLACE INTO pairing (user_id, partner_id) VALUES (?, ?)", (a_id, b_id))
        cur.execute("INSERT OR REPLACE INTO pairing (user_id, partner_id) VALUES (?, ?)", (b_id, a_id))
        cur.execute("UPDATE users SET status='chatting', partner_id=? WHERE user_id=?", (b_id, a_id))
        cur.execute("UPDATE users SET status='chatting', partner_id=? WHERE user_id=?", (a_id, b_id))
        cur.execute("DELETE FROM queue WHERE user_id = ?", (a_id,))
        cur.execute("DELETE FROM queue WHERE user_id = ?", (b_id,))
        conn.commit()
    finally:
        conn.close()

def end_pairing(user_id):
    conn = db.get_db()
    cur = conn.cursor()
    partner = None
    try:
        cur.execute("SELECT partner_id FROM pairing WHERE user_id = ?", (user_id,))
        r = cur.fetchone()
        if r:
            partner = r[0]
        cur.execute("DELETE FROM pairing WHERE user_id = ?", (user_id,))
        if partner:
            cur.execute("DELETE FROM pairing WHERE user_id = ?", (partner,))
            cur.execute("UPDATE users SET status='idle', partner_id=NULL WHERE user_id = ?", (partner,))
        cur.execute("UPDATE users SET status='idle', partner_id=NULL WHERE user_id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()
    return partner

def get_partner(user_id):
    conn = db.get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT partner_id FROM pairing WHERE user_id = ?", (user_id,))
        r = cur.fetchone()
        return r[0] if r else None
    finally:
        conn.close()

def user_in_queue(user_id):
    conn = db.get_db()
    cur = conn.cursor()
    try:
        cur.execute("SELECT 1 FROM queue WHERE user_id = ?", (user_id,))
        return cur.fetchone() is not None
    finally:
        conn.close()

# ---------------- GAME ----------------
active_games = {}  # game_id -> {"type":"quiz","score":{},"current_q":0,"questions":[]}

quiz_questions = [
    {"q": "Apa warna bendera Indonesia?", "options": ["A.merah putih","B.merah hitam","C.abu abu biru"], "answer":"merah putih"},
    {"q": "Ibukota Indonesia?", "options":["A.Jakarta","B.Bandung","C.Surabaya"], "answer":"jakarta"},
    {"q": "Gunung tertinggi di Indonesia?", "options":["A.Merapi","B.Jaya Wijaya","C.Semeru"], "answer":"jaya wijaya"},
]

truth_dare_questions = [
    "Truth: Ceritakan rahasia lucu kamu!",
    "Dare: Kirim emoji 🐱 sebanyak 5 kali di chat!",
    "Truth: Siapa orang yang paling kamu kagumi?",
    "Dare: Tuliskan 3 kata terakhir pesanmu dalam chat ini."
]

async def start_quiz(user_id, partner_id, context: ContextTypes.DEFAULT_TYPE):
    game_id = f"{user_id}_{partner_id}"
    active_games[game_id] = {"type":"quiz","score":{user_id:0,partner_id:0},"current_q":0,"questions":random.sample(quiz_questions,len(quiz_questions))}
    await send_next_question(game_id, context)

async def send_next_question(game_id, context: ContextTypes.DEFAULT_TYPE):
    game = active_games.get(game_id)
    if not game:
        return
    q_idx = game["current_q"]
    if q_idx >= len(game["questions"]):
        scores = game["score"]
        winner = max(scores, key=lambda k: scores[k])
        loser = min(scores, key=lambda k: scores[k])
        await context.bot.send_message(winner, f"🏆 Kamu menang quiz! Skor: {scores[winner]} 🎉\nSekarang giliran partnermu pilih Truth or Dare!")
        await context.bot.send_message(loser, f"😅 Kamu kalah quiz. Skor: {scores[loser]}\nPilih Truth or Dare sekarang!")
        kb = ReplyKeyboardMarkup([["Truth","Dare"]], one_time_keyboard=True, resize_keyboard=True)
        await context.bot.send_message(loser, "Pilih Truth atau Dare:", reply_markup=kb)
        del active_games[game_id]
        return
    q = game["questions"][q_idx]
    text = f"❓ Quiz! Pertanyaan {q_idx+1}:\n{q['q']}\nPilihan: {', '.join(q['options'])}\nKetik jawabanmu!"
    for uid in game["score"]:
        try:
            await context.bot.send_message(uid, text)
        except:
            pass

async def handle_game_answer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip().lower()
    user_id = update.message.from_user.id
    partner_id = get_partner(user_id)
    if not partner_id:
        return
    game_id = f"{user_id}_{partner_id}" if f"{user_id}_{partner_id}" in active_games else f"{partner_id}_{user_id}"
    game = active_games.get(game_id)
    if not game or game["type"] != "quiz":
        return
    if not can_send(user_id):
        await update.message.reply_text("⚠️ Jangan spam, tunggu beberapa detik sebelum kirim jawaban lagi.")
        return
    q_idx = game["current_q"]
    question = game["questions"][q_idx]
    correct_answer = question["answer"].lower()
    if correct_answer in text:
        game["score"][user_id] += 1
        await context.bot.send_message(user_id, f"✅ Jawaban benar! Skor kamu: {game['score'][user_id]}")
        await context.bot.send_message(partner_id, f"😎 Partnermu baru aja jawab benar!")
    else:
        await context.bot.send_message(user_id, f"❌ Jawaban salah!")
    game["current_q"] +=1
    await send_next_question(game_id, context)

# ---------------- registration & chat ----------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db.create_user_if_not_exists(user_id)
    await update.message.reply_text("Pilih gender kamu:", reply_markup=ReplyKeyboardMarkup([["👨 Cowok","👩 Cewek"]], one_time_keyboard=True, resize_keyboard=True))
    context.user_data["reg_state"] = "GENDER"

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    user_id = update.message.from_user.id
    if not can_send(user_id):
        await update.message.reply_text("⚠️ Jangan spam, tunggu beberapa detik sebelum kirim pesan lagi.")
        return

    partner = get_partner(user_id)
    if partner:
        await handle_game_answer(update, context)
        try: await context.bot.send_message(partner, text)
        except: pass
        return

    state = context.user_data.get("reg_state")
    # ---------- REGISTRATION FLOW ----------
    if state == "GENDER":
        if text not in ("👨 Cowok","👩 Cewek"): await update.message.reply_text("Pilih gender dengan tombol."); return
        db.save_user(user_id,"gender","male" if "Cowok" in text else "female")
        context.user_data["reg_state"]="AGE"
        await update.message.reply_text("Masukkan umur kamu (contoh: 23):", reply_markup=ReplyKeyboardRemove())
        return
    if state=="AGE":
        if not text.isdigit() or not (10<=int(text)<=120): await update.message.reply_text("Umur tidak valid (10-120)."); return
        db.save_user(user_id,"age",int(text))
        kb=ReplyKeyboardMarkup([[KeyboardButton("📍 Kirim Lokasi Terkini",request_location=True)]],one_time_keyboard=True,resize_keyboard=True)
        context.user_data["reg_state"]="LOCATION"
        await update.message.reply_text("Kirim lokasi sekarang (atau ketik nama kota manual):",reply_markup=kb)
        return
    if state=="LOCATION":
        db.save_user(user_id,"location",text.title())
        db.save_user(user_id,"latitude",None)
        db.save_user(user_id,"longitude",None)
        context.user_data["reg_state"]="PREF_GENDER"
        kb=ReplyKeyboardMarkup([["👨 Cowok","👩 Cewek","🌐 Semua"]],one_time_keyboard=True,resize_keyboard=True)
        await update.message.reply_text("Pilih preferensi gender partner:",reply_markup=kb)
        return
    if state=="PREF_GENDER":
        if text not in ("👨 Cowok","👩 Cewek","🌐 Semua"): await update.message.reply_text("Pilih preferensi dengan tombol."); return
        pref="male" if "Cowok" in text else "female" if "Cewek" in text else "all"
        db.save_user(user_id,"pref_gender",pref)
        context.user_data["reg_state"]="PREF_AGE_MIN"
        await update.message.reply_text("Masukkan umur minimum partner:",reply_markup=ReplyKeyboardRemove())
        return
    if state=="PREF_AGE_MIN":
        if not text.isdigit() or not (10<=int(text)<=120): await update.message.reply_text("Umur tidak valid (10-120)."); return
        db.save_user(user_id,"pref_age_min",int(text))
        context.user_data["reg_state"]="PREF_AGE_MAX"
        await update.message.reply_text("Masukkan umur maksimum partner:")
        return
    if state=="PREF_AGE_MAX":
        if not text.isdigit() or not (10<=int(text)<=120): await update.message.reply_text("Umur tidak valid (10-120)."); return
        min_age=db.get_user(user_id)["pref_age_min"]
        max_age=int(text)
        if max_age<min_age: await update.message.reply_text(f"Umur maksimum harus >= {min_age}"); return
        db.save_user(user_id,"pref_age_max",max_age)
        context.user_data.pop("reg_state",None)
        await update.message.reply_text("Profil lengkap ✅\nGunakan /find untuk mencari partner.",reply_markup=ReplyKeyboardMarkup([["🔍 Cari Teman","❌ Stop Chat"],["🎮 Main Game"]],resize_keyboard=True))
        return

    # ---------- MAIN MENU ----------
    if text in ("🔍 Cari Partner","/find"): await cmd_find(update,context); return
    if text in ("❌ Stop Chat","/stop"): await cmd_stop(update,context); return
    if text in ("🎮 Main Game","/game"):
        partner=get_partner(user_id)
        if not partner: await update.message.reply_text("Mulai game bisa jika kamu sedang terhubung dengan partner."); return
        await start_quiz(user_id,partner,context)
        return

    await update.message.reply_text("Perintah tidak dikenal. Gunakan tombol keyboard.")

async def location_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    state = context.user_data.get("reg_state")
    if state!="LOCATION": await update.message.reply_text("Kirim lokasi hanya saat diminta (setelah umur)."); return
    loc=update.message.location
    if not loc: await update.message.reply_text("Lokasi tidak terdeteksi."); return
    lat,lon=loc.latitude,loc.longitude
    city=reverse_geocode_city(lat,lon) or "Unknown"
    db.save_user(user_id,"latitude",lat)
    db.save_user(user_id,"longitude",lon)
    db.save_user(user_id,"location",city)
    context.user_data["reg_state"]="PREF_GENDER"
    kb=ReplyKeyboardMarkup([["👨 Cowok","👩 Cewek","🌐 Semua"]],one_time_keyboard=True,resize_keyboard=True)
    await update.message.reply_text(f"Lokasi tersimpan: {city}\nPilih preferensi gender partner:",reply_markup=kb)

# ---------------- FIND ----------------
async def cmd_find(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id=update.message.from_user.id
    user=db.get_user(user_id)
    if not user or not user["gender"] or not user["age"] or not user["location"]: await update.message.reply_text("Lengkapi profil dulu dengan /start."); return
    if get_partner(user_id): await update.message.reply_text("Kamu sudah terhubung. Ketik /stop untuk akhiri."); return
    if user_in_queue(user_id): await update.message.reply_text("Kamu sudah dalam antrean, tunggu sebentar."); return
    add_to_queue(user_id)
    await update.message.reply_text("🔎 Mencari partner...")

    city=user["location"]
    pref_gender=user["pref_gender"]
    min_age=user["pref_age_min"]
    max_age=user["pref_age_max"]

    candidates=queue_get_candidates_by_city(city,exclude_user_id=user_id)
    filtered=[]
    for c in candidates:
        if pref_gender!="all" and c["gender"]!=pref_gender: continue
        if not(min_age<=c["age"]<=max_age): continue
        filtered.append(c)

    if not filtered: await update.message.reply_text("Belum ada partner sesuai preferensi di kotamu. Coba lagi nanti."); return

    if user["latitude"] is not None and user["longitude"] is not None:
        with_coords=[c for c in filtered if c["latitude"] is not None and c["longitude"] is not None]
        if with_coords:
            distances=[(haversine_km(user["latitude"],user["longitude"],c["latitude"],c["longitude"]),c["user_id"]) for c in with_coords]
            distances.sort(key=lambda x:x[0])
            partner_id=distances[0][1]
            create_pairing(user_id,partner_id)
            await context.bot.send_message(user_id,"🎉 Partner terdekat ditemukan! Mulai chat anon. Ketik /stop.")
            await context.bot.send_message(partner_id,"🎉 Partner terdekat ditemukan! Mulai chat anon. Ketik /stop.")
            return
    partner_id=filtered[0]["user_id"]
    create_pairing(user_id,partner_id)
    await context.bot.send_message(user_id,"🎉 Partner ditemukan (sekota)! Mulai chat anon. Ketik /stop.")
    await context.bot.send_message(partner_id,"🎉 Partner ditemukan (sekota)! Mulai chat anon. Ketik /stop.")

# ---------------- STOP ----------------
async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id=update.message.from_user.id
    partner=get_partner(user_id)
    if not partner: await update.message.reply_text("Kamu tidak sedang chat dengan siapa pun."); return
    other=end_pairing(user_id)
    try: await context.bot.send_message(other,"❌ Partner meninggalkan obrolan.")
    except: pass
    await update.message.reply_text("Kamu keluar dari obrolan.")
    remove_from_queue(user_id)
    if other: remove_from_queue(other)

# ---------------- main ----------------
def main():
    app=ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",cmd_start))
    app.add_handler(CommandHandler("find",cmd_find))
    app.add_handler(CommandHandler("stop",cmd_stop))
    app.add_handler(MessageHandler(filters.LOCATION,location_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,text_handler))
    logger.info("Bot started — final ready-to-use version with anti-spam.")
    app.run_polling()

if __name__=="__main__":
    main()
