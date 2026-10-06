import os
import json
import random
import logging
from datetime import datetime, timedelta
from pathlib import Path

import requests
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

# ---------- CONFIG ----------
BOT_TOKEN = os.environ.get("BOT_TOKEN")
FOOTBALL_API_KEY = os.environ.get("FOOTBALL_API_KEY", "")
FOOTBALL_API_URL = "https://v3.football.api-sports.io/fixtures"
ADMIN_IDS = [int(x) for x in os.environ.get("ADMIN_IDS", "").split(",") if x.strip().isdigit()]
BOT_USERNAME = "SB24_Lucky234bot"
BOT_NAME = "SB24 Lucky234"

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---------- PERSISTENCE (simple JSON file) ----------
DATA_FILE = Path("data.json")

def load_data():
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text())
        except Exception:
            pass
    return {
        "checkin": {},        # user_id -> iso date string
        "points": {},         # user_id -> int
        "streak": {},         # user_id -> int (consecutive days)
        "referrals": {},      # user_id -> list of referred user_ids
        "referred_by": {},    # user_id -> referrer user_id
        "redeemed_promos": {},# user_id -> [promo codes redeemed]
        "giveaway_entries": [],
        "giveaway_active": False,
    }

def save_data():
    DATA_FILE.write_text(json.dumps(DB, indent=2))

DB = load_data()

# ---------- PROMO CODES ----------
# Each promo: code -> { "points": int, "uses_left": int, "expires": iso | None, "label": str }
PROMOS = {
    "LUCKY234":   {"points": 25, "uses_left": 999, "expires": None, "label": "Welcome Bonus"},
    "GOAL234":    {"points": 15, "uses_left": 999, "expires": None, "label": "Football Fan Bonus"},
    "STREAK7":    {"points": 50, "uses_left": 500, "expires": None, "label": "7-Day Streak Reward"},
    "WEEKEND234": {"points": 30, "uses_left": 300, "expires": None, "label": "Weekend Special"},
    "VIP234":     {"points": 100,"uses_left": 100, "expires": None, "label": "VIP Community Promo"},
}

def get_daily_promo():
    """Rotate one featured promo per day."""
    codes = list(PROMOS.keys())
    idx = datetime.utcnow().toordinal() % len(codes)
    code = codes[idx]
    return code, PROMOS[code]

# ---------- HELPERS ----------
def get_points(uid):
    return DB["points"].get(str(uid), 0)

def add_points(uid, amount):
    uid = str(uid)
    DB["points"][uid] = DB["points"].get(uid, 0) + amount
    save_data()

def is_admin(uid):
    return uid in ADMIN_IDS

# ---------- /start ----------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    uid = user.id

    # Referral handling: /start ref_<id>
    if context.args:
        arg = context.args[0]
        if arg.startswith("ref_"):
            try:
                referrer_id = int(arg.replace("ref_", ""))
                if referrer_id != uid and str(uid) not in DB["referred_by"]:
                    DB["referred_by"][str(uid)] = referrer_id
                    DB["referrals"].setdefault(str(referrer_id), []).append(uid)
                    add_points(referrer_id, 20)   # referrer bonus
                    add_points(uid, 10)           # new user bonus
                    save_data()
                    try:
                        await context.bot.send_message(
                            referrer_id,
                            f"🎉 Someone joined using your link! +20 points",
                        )
                    except Exception:
                        pass
            except Exception:
                pass

    keyboard = [
        [InlineKeyboardButton("⚽ Football", callback_data="football"),
         InlineKeyboardButton("🔴 Live", callback_data="live")],
        [InlineKeyboardButton("🎁 Giveaway", callback_data="giveaway_join"),
         InlineKeyboardButton("✅ Check-in", callback_data="checkin")],
        [InlineKeyboardButton("🔥 Promo Code", callback_data="promo"),
         InlineKeyboardButton("👥 My Referrals", callback_data="referrals")],
        [InlineKeyboardButton("💎 My Points", callback_data="points"),
         InlineKeyboardButton("📖 Help", callback_data="help")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    text = (
        f"👋 Hello <b>{user.first_name}</b>!\n\n"
        f"Welcome to <b>{BOT_NAME}</b> 🍀⚽🎁🔥\n\n"
        "What I can do:\n"
        "• ⚽ <b>Football</b> — Live scores & fixtures\n"
        "• 🎁 <b>Giveaway</b> — Join community draws\n"
        "• ✅ <b>Check-in</b> — Earn points daily\n"
        "• 🔥 <b>Promo Codes</b> — Redeem for bonus points\n"
        "• 👥 <b>Referrals</b> — Invite friends, earn more\n\n"
        f"💎 Your points: <b>{get_points(uid)}</b>"
    )
    await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="HTML")

# ---------- /help ----------
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code, promo = get_daily_promo()
    text = (
        "📖 <b>Commands</b>\n\n"
        "/start – Main menu\n"
        "/football – Today's fixtures\n"
        "/live – Live matches\n"
        "/giveaway – Join the giveaway\n"
        "/checkin – Daily reward\n"
        "/points – Your balance\n"
        "/promo – See today's promo code\n"
        "/redeem CODE – Redeem a promo code\n"
        "/invite – Get your referral link\n"
        "/referrals – See your invite count\n"
        "/help – This menu\n\n"
        f"🔥 <b>Today's promo hint:</b> {promo['label']}\n"
        f"Use <code>/promo</code> to reveal it."
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(text, parse_mode="HTML")
    else:
        await update.message.reply_text(text, parse_mode="HTML")

# ---------- FOOTBALL ----------
def fetch_fixtures(live_only=False):
    if not FOOTBALL_API_KEY:
        return None
    headers = {"x-apisports-key": FOOTBALL_API_KEY}
    params = {"live": "all"} if live_only else {"date": datetime.utcnow().strftime("%Y-%m-%d")}
    try:
        r = requests.get(FOOTBALL_API_URL, headers=headers, params=params, timeout=10)
        return r.json().get("response", [])
    except Exception as e:
        logger.error(f"Football API error: {e}")
        return None


def format_fixtures(fixtures, live_only=False):
    if fixtures is None:
        return "⚠️ Football data temporarily unavailable."
    if not fixtures:
        return "📭 No matches right now."

    lines = ["⚽ <b>Live Matches</b>\n" if live_only else "⚽ <b>Today's Fixtures</b>\n"]
    for f in fixtures[:10]:
        home = f["teams"]["home"]["name"]
        away = f["teams"]["away"]["name"]
        gh, ga = f["goals"]["home"], f["goals"]["away"]
        status = f["fixture"]["status"]["short"]
        league = f["league"]["name"]
        score = f"{gh or 0} - {ga or 0}" if (live_only or status in ("1H","2H","HT","ET","P")) else "vs"
        lines.append(f"🏆 {league}\n   {home}  <b>{score}</b>  {away}\n")
    return "\n".join(lines)


async def football(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    await msg.reply_text("🔎 Fetching today's fixtures...")
    fixtures = fetch_fixtures(False)
    await msg.reply_text(format_fixtures(fixtures), parse_mode="HTML", disable_web_page_preview=True)


async def live(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    await msg.reply_text("🔎 Fetching live matches...")
    fixtures = fetch_fixtures(True)
    await msg.reply_text(format_fixtures(fixtures, True), parse_mode="HTML", disable_web_page_preview=True)

# ---------- GIVEAWAY ----------
async def giveaway(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await join_giveaway(update, context)


async def join_giveaway(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not DB["giveaway_active"]:
        msg = "❌ No giveaway is running right now."
    elif uid in DB["giveaway_entries"]:
        msg = "✅ You're already in the draw! Good luck 🍀"
    else:
        DB["giveaway_entries"].append(uid)
        save_data()
        msg = f"🎉 You joined! Total entries: {len(DB['giveaway_entries'])}"

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(msg)
    else:
        await update.message.reply_text(msg)


async def start_giveaway(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return await update.message.reply_text("⛔ Not authorized.")
    DB["giveaway_active"] = True
    DB["giveaway_entries"] = []
    save_data()
    await update.message.reply_text("🎉 New giveaway started! Users can /giveaway to join.")


async def end_giveaway(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return await update.message.reply_text("⛔ Not authorized.")
    if not DB["giveaway_entries"]:
        DB["giveaway_active"] = False
        save_data()
        return await update.message.reply_text("❌ No participants.")
    winner_id = random.choice(DB["giveaway_entries"])
    try:
        w = await context.bot.get_chat(winner_id)
        name = w.first_name
    except Exception:
        name = f"User {winner_id}"
    await update.message.reply_text(
        f"🏆 <b>Winner:</b> {name}! 🎉\nEntries: {len(DB['giveaway_entries'])}",
        parse_mode="HTML",
    )
    DB["giveaway_active"] = False
    DB["giveaway_entries"] = []
    save_data()

# ---------- CHECK-IN ----------
async def checkin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    now = datetime.utcnow()
    last = DB["checkin"].get(uid)

    if last:
        last_dt = datetime.fromisoformat(last)
        if now - last_dt < timedelta(hours=24):
            remain = timedelta(hours=24) - (now - last_dt)
            h, r = divmod(int(remain.total_seconds()), 3600)
            m = r // 60
            msg = f"⏳ Already checked in! Come back in <b>{h}h {m}m</b>."
            return await _reply(update, msg)

        # Streak logic
        if now - last_dt < timedelta(hours=48):
            DB["streak"][uid] = DB["streak"].get(uid, 0) + 1
        else:
            DB["streak"][uid] = 1
    else:
        DB["streak"][uid] = 1

    streak = DB["streak"][uid]
    base = random.randint(5, 15)
    streak_bonus = min(streak, 7) * 2   # up to +14
    total = base + streak_bonus

    DB["checkin"][uid] = now.isoformat()
    add_points(uid, total)

    msg = (
        f"✅ <b>Check-in successful!</b>\n\n"
        f"Base: <b>{base}</b> pts\n"
        f"🔥 Streak bonus (day {streak}): <b>+{streak_bonus}</b> pts\n"
        f"💰 Earned: <b>{total}</b> pts\n"
        f"💎 Total: <b>{get_points(uid)}</b>"
    )
    await _reply(update, msg)

# ---------- POINTS ----------
async def points(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    streak = DB["streak"].get(str(uid), 0)
    refs = len(DB["referrals"].get(str(uid), []))
    msg = (
        f"💎 <b>Your Stats</b>\n\n"
        f"Points: <b>{get_points(uid)}</b>\n"
        f"🔥 Streak: <b>{streak} day(s)</b>\n"
        f"👥 Referrals: <b>{refs}</b>"
    )
    await _reply(update, msg)

# ---------- PROMO ----------
async def promo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    code, info = get_daily_promo()
    text = (
        f"🔥 <b>Today's Featured Promo</b>\n\n"
        f"🏷️ Label: <b>{info['label']}</b>\n"
        f"🎁 Reward: <b>{info['points']} points</b>\n"
        f"⏳ Uses left: <b>{info['uses_left']}</b>\n\n"
        f"💡 Redeem with:\n<code>/redeem {code}</code>"
    )
    await _reply(update, text)


async def redeem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    if not context.args:
        return await _reply(update, "❌ Usage: <code>/redeem CODE</code>")
    code = context.args[0].upper().strip()

    if code not in PROMOS:
        return await _reply(update, "❌ Invalid promo code.")

    p = PROMOS[code]
    if p["uses_left"] <= 0:
        return await _reply(update, "❌ This promo has been fully used.")

    redeemed = DB["redeemed_promos"].setdefault(uid, [])
    if code in redeemed:
        return await _reply(update, "⚠️ You already redeemed this code.")

    if p.get("expires"):
        if datetime.utcnow() > datetime.fromisoformat(p["expires"]):
            return await _reply(update, "❌ This promo has expired.")

    # Apply
    p["uses_left"] -= 1
    redeemed.append(code)
    add_points(uid, p["points"])
    save_data()

    msg = (
        f"🎉 <b>Promo Redeemed!</b>\n\n"
        f"Code: <code>{code}</code>\n"
        f"Reward: <b>+{p['points']} points</b>\n"
        f"💎 Total: <b>{get_points(uid)}</b>"
    )
    await _reply(update, msg)

# ---------- REFERRALS ----------
async def invite(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    link = f"https://t.me/{BOT_USERNAME}?start=ref_{uid}"
    text = (
        f"👥 <b>Invite Friends — Earn 20 points each!</b>\n\n"
        f"Your personal link:\n"
        f"<code>{link}</code>\n\n"
        f"• You get <b>+20 pts</b> per new user\n"
        f"• They get <b>+10 pts</b> welcome bonus"
    )
    await _reply(update, text)


async def referrals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = str(update.effective_user.id)
    count = len(DB["referrals"].get(uid, []))
    msg = (
        f"👥 <b>Your Referrals</b>\n\n"
        f"Total invited: <b>{count}</b>\n"
        f"Points earned: <b>{count * 20}</b>"
    )
    await _reply(update, msg)

# ---------- BROADCAST ----------
async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        return await update.message.reply_text("⛔ Not authorized.")
    if not context.args:
        return await update.message.reply_text("Usage: /broadcast Your message here")
    msg = " ".join(context.args)
    users = set(list(DB["points"].keys()) + list(DB["checkin"].keys()))
    sent, failed = 0, 0
    for uid in users:
        try:
            await context.bot.send_message(int(uid), f"📢 {msg}")
            sent += 1
        except Exception:
            failed += 1
    await update.message.reply_text(f"✅ Sent: {sent} | ❌ Failed: {failed}")

# ---------- ROUTER ----------
async def _reply(update: Update, text):
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(text, parse_mode="HTML")
    else:
        await update.message.reply_text(text, parse_mode="HTML")


async def button_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = update.callback_query.data
    if data == "football":
        await update.callback_query.answer()
        fixtures = fetch_fixtures(False)
        await update.callback_query.message.reply_text(
            format_fixtures(fixtures), parse_mode="HTML", disable_web_page_preview=True)
    elif data == "live":
        await update.callback_query.answer()
        fixtures = fetch_fixtures(True)
        await update.callback_query.message.reply_text(
            format_fixtures(fixtures, True), parse_mode="HTML", disable_web_page_preview=True)
    elif data == "giveaway_join":
        await join_giveaway(update, context)
    elif data == "checkin":
        await checkin(update, context)
    elif data == "promo":
        await promo(update, context)
    elif data == "referrals":
        await referrals(update, context)
    elif data == "points":
        await points(update, context)
    elif data == "help":
        await help_command(update, context)

# ---------- MAIN ----------
def main():
    if not BOT_TOKEN:
        raise SystemExit("❌ BOT_TOKEN not set!")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("football", football))
    app.add_handler(CommandHandler("live", live))
    app.add_handler(CommandHandler("giveaway", giveaway))
    app.add_handler(CommandHandler("startgiveaway", start_giveaway))
    app.add_handler(CommandHandler("endgiveaway", end_giveaway))
    app.add_handler(CommandHandler("checkin", checkin))
    app.add_handler(CommandHandler("points", points))
    app.add_handler(CommandHandler("promo", promo))
    app.add_handler(CommandHandler("redeem", redeem))
    app.add_handler(CommandHandler("invite", invite))
    app.add_handler(CommandHandler("referrals", referrals))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CallbackQueryHandler(button_router))

    logger.info(f"🤖 {BOT_NAME} is running...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
