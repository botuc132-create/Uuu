import os
import json
import logging
import sqlite3
from datetime import datetime, timezone

import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("telegram-auto-orders")

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
API_KEY = os.getenv("MUALIKE_API_KEY", "").strip()
API_URL = os.getenv("MUALIKE_API_URL", "https://mualike1s.com/api/v3").strip()
SERVICE_ID = os.getenv("MUALIKE_SERVICE_ID", "3284").strip()
DB_PATH = os.getenv("DB_PATH", "bot_data.sqlite3")
ADMIN_IDS = {x.strip() for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()}

if not BOT_TOKEN:
    raise RuntimeError("Missing TELEGRAM_BOT_TOKEN environment variable")

def connect_db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    with connect_db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS settings (
            admin_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0,
            channel TEXT, quantity INTEGER NOT NULL DEFAULT 50, awaiting TEXT
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS processed (
            channel_id TEXT NOT NULL, message_id TEXT NOT NULL,
            order_id TEXT, status TEXT NOT NULL, link TEXT, quantity INTEGER,
            created_at TEXT NOT NULL, PRIMARY KEY(channel_id, message_id)
        )""")

def allowed(user):
    return bool(user and str(user.id) in ADMIN_IDS)

def settings_for(admin_id):
    with connect_db() as con:
        con.execute("INSERT OR IGNORE INTO settings(admin_id) VALUES(?)", (str(admin_id),))
        row = con.execute("SELECT * FROM settings WHERE admin_id=?", (str(admin_id),)).fetchone()
        return dict(row)

def save_settings(admin_id, **changes):
    current = settings_for(admin_id)
    current.update(changes)
    with connect_db() as con:
        con.execute("""INSERT INTO settings(admin_id, enabled, channel, quantity, awaiting)
        VALUES(?,?,?,?,?) ON CONFLICT(admin_id) DO UPDATE SET
        enabled=excluded.enabled, channel=excluded.channel,
        quantity=excluded.quantity, awaiting=excluded.awaiting""",
        (str(admin_id), int(current["enabled"]), current["channel"],
         int(current["quantity"]), current["awaiting"]))

def panel(admin_id):
    s = settings_for(admin_id)
    state = "🟢 ĐANG BẬT" if s["enabled"] else "🔴 ĐANG TẮT"
    channel = s["channel"] or "Chưa cài"
    text = (
        "🤖 BOT TẠO ĐƠN TỰ ĐỘNG\n\n"
        f"Trạng thái: {state}\n"
        f"Kênh: {channel}\n"
        f"Lượt xem mỗi bài: {s['quantity']}\n"
        f"Mã dịch vụ: {SERVICE_ID}\n\nChọn chức năng:"
    )
    keyboard = [
        [InlineKeyboardButton("🟢 Bật tự động" if not s["enabled"] else "🔴 Tắt tự động", callback_data="toggle")],
        [InlineKeyboardButton("📣 Cài kênh", callback_data="channel"),
         InlineKeyboardButton("👁 Cài lượt xem", callback_data="quantity")],
        [InlineKeyboardButton("📊 Lịch sử gần đây", callback_data="history"),
         InlineKeyboardButton("ℹ️ Hướng dẫn", callback_data="help")]
    ]
    return text, InlineKeyboardMarkup(keyboard)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not allowed(update.effective_user):
        await update.effective_message.reply_text("Bạn chưa được cấp quyền quản trị bot.")
        return
    text, markup = panel(update.effective_user.id)
    await update.effective_message.reply_text(text, reply_markup=markup)

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user = update.effective_user
    if not allowed(user):
        await q.message.reply_text("Bạn chưa được cấp quyền quản trị bot.")
        return
    uid = str(user.id)
    s = settings_for(uid)

    if q.data == "toggle":
        if s["enabled"]:
            save_settings(uid, enabled=0)
        else:
            if not s["channel"]:
                save_settings(uid, awaiting="channel")
                await q.message.reply_text("Nhập @username hoặc ID kênh (ví dụ @kenhcuaban hoặc -1001234567890).")
                return
            if not API_KEY:
                await q.message.reply_text("Thiếu MUALIKE_API_KEY trong Environment của Render.")
                return
            save_settings(uid, enabled=1)
    elif q.data == "channel":
        save_settings(uid, awaiting="channel")
        await q.message.reply_text("Nhập @username hoặc ID kênh (ví dụ @kenhcuaban hoặc -1001234567890).")
        return
    elif q.data == "quantity":
        save_settings(uid, awaiting="quantity")
        await q.message.reply_text("Nhập số lượt xem cho mỗi bài mới, ví dụ 50.")
        return
    elif q.data == "history":
        with connect_db() as con:
            rows = con.execute("SELECT order_id,status,link,quantity,created_at FROM processed ORDER BY created_at DESC LIMIT 10").fetchall()
        if not rows:
            await q.message.reply_text("Chưa có bài nào được xử lý.")
        else:
            out = []
            for r in rows:
                out.append(f"{r['created_at']} | {r['status']} | đơn {r['order_id'] or 'chưa có'} | {r['quantity']} lượt\n{r['link'] or ''}")
            await q.message.reply_text("\n\n".join(out))
    elif q.data == "help":
        await q.message.reply_text(
            "1) Cài kênh, 2) cài số lượt xem, 3) bật tự động.\n"
            "Thêm bot làm quản trị viên kênh để nhận bài đăng. Bot chỉ xử lý bài mới sau khi bật. "
            "API key phải được lưu trong Environment, không đưa vào mã nguồn."
        )
    text, markup = panel(uid)
    # Never send an empty message body to Telegram.
    if not isinstance(text, str) or not text.strip():
        text = (
            "🤖 BOT TẠO ĐƠN TỰ ĐỘNG\\n\\n"
            f"Mã dịch vụ: {SERVICE_ID}\\n"
            "Chọn chức năng bên dưới:"
        )
    try:
        await q.edit_message_text(text=text, reply_markup=markup)
    except BadRequest as exc:
        # Ignore only Telegram's harmless no-change response.
        if "Message is not modified" in str(exc):
            return
        # If editing the current message fails, send a fresh panel instead.
        log.warning("Không sửa được bảng nút: %s", exc)
        try:
            await q.message.reply_text(text=text, reply_markup=markup)
        except Exception:
            log.exception("Không gửi được bảng nút thay thế")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, msg = update.effective_user, update.effective_message
    if not allowed(user) or not msg or not msg.text:
        return
    uid = str(user.id)
    s = settings_for(uid)
    if not s["awaiting"]:
        return
    value = msg.text.strip()

    if s["awaiting"] == "channel":
        if not value or " " in value or len(value) > 200:
            await msg.reply_text("Kênh không hợp lệ. Hãy gửi @username hoặc ID kênh.")
            return
        save_settings(uid, channel=value, awaiting=None, enabled=0)
        await msg.reply_text("Đã lưu kênh. Tiếp theo cài lượt xem rồi bấm Bật tự động.")
    elif s["awaiting"] == "quantity":
        try:
            quantity = int(value)
            if quantity < 1 or quantity > 1000000:
                raise ValueError
        except ValueError:
            await msg.reply_text("Hãy nhập số nguyên từ 1 đến 1.000.000.")
            return
        save_settings(uid, quantity=quantity, awaiting=None, enabled=0)
        await msg.reply_text(f"Đã lưu {quantity} lượt xem cho mỗi bài.")
    text, markup = panel(uid)
    await msg.reply_text(text, reply_markup=markup)

def post_link(chat, message_id):
    if chat.username:
        return f"https://t.me/{chat.username}/{message_id}"
    chat_id = str(chat.id)
    if chat_id.startswith("-100"):
        return f"https://t.me/c/{chat_id[4:]}/{message_id}"
    return None

def update_processed(chat_id, message_id, status, order_id=None):
    with connect_db() as con:
        con.execute("UPDATE processed SET status=?, order_id=? WHERE channel_id=? AND message_id=?",
                    (status, order_id, str(chat_id), str(message_id)))

async def send_admin(context, admin_id, text):
    try:
        await context.bot.send_message(chat_id=int(admin_id), text=text)
    except Exception:
        log.exception("Unable to notify admin %s", admin_id)

async def channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.channel_post
    if not msg:
        return
    chat = msg.chat
    link = post_link(chat, msg.message_id)
    with connect_db() as con:
        admins = con.execute("SELECT * FROM settings WHERE enabled=1").fetchall()

    for s in admins:
        configured = (s["channel"] or "").strip()
        possible = {str(chat.id)}
        if chat.username:
            possible.add(chat.username)
            possible.add("@" + chat.username)
        if configured not in possible:
            continue
        if not link:
            await send_admin(context, s["admin_id"], "Không tạo được link bài đăng. Hãy kiểm tra kênh và quyền truy cập.")
            continue

        try:
            with connect_db() as con:
                con.execute("""INSERT INTO processed(channel_id,message_id,status,link,quantity,created_at)
                    VALUES(?,?,?,?,?,?)""",
                    (str(chat.id), str(msg.message_id), "processing", link,
                     int(s["quantity"]), datetime.now(timezone.utc).isoformat()))
        except sqlite3.IntegrityError:
            continue

        payload = {
            "key": API_KEY, "action": "add", "service": SERVICE_ID,
            "link": link, "quantity": int(s["quantity"])
        }
        try:
            response = requests.post(API_URL, data=payload, timeout=30)
            response.raise_for_status()
            try:
                data = response.json()
            except ValueError:
                data = {"raw_response": response.text[:1000]}
            if isinstance(data, dict) and data.get("order") is not None:
                order_id = str(data["order"])
                update_processed(chat.id, msg.message_id, "ordered", order_id)
                await send_admin(context, s["admin_id"],
                    f"✅ Đã gửi tạo đơn\nMã đơn: {order_id}\nDịch vụ: {SERVICE_ID}\nLượt xem: {s['quantity']}\nBài: {link}")
            else:
                update_processed(chat.id, msg.message_id, "api_error")
                await send_admin(context, s["admin_id"],
                    "⚠️ API chưa trả mã đơn. Kiểm tra URL/key/dịch vụ. Phản hồi API:\n" +
                    json.dumps(data, ensure_ascii=False)[:1200] + f"\nBài: {link}")
        except Exception as exc:
            log.exception("Order API request failed")
            update_processed(chat.id, msg.message_id, "request_error")
            await send_admin(context, s["admin_id"], f"❌ Lỗi API: {type(exc).__name__}: {exc}\nBài: {link}")

def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.ChatType.CHANNEL, channel_post))
    log.info("Bot started; service=%s; API=%s", SERVICE_ID, API_URL)
    app.run_polling(allowed_updates=["message", "channel_post", "callback_query"])

if __name__ == "__main__":
    main()
