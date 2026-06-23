import os
import asyncio
import time
import signal
from datetime import datetime, timedelta, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, CallbackQueryHandler, ContextTypes, filters
from telegram.error import BadRequest, Forbidden
from tinydb import TinyDB, Query

# 🔑 Configuration
TELEGRAM_BOT_TOKEN = "8963914654:AAFWhHf9mOquOCyabBwuPBI7Vlb488J5r4g"
ADMIN_ID = 6598432032        
FORCE_SUB_CHANNEL = "@AllstoryFM2"  
CHANNEL_INVITE_LINK = "https://t.me/AllstoryFM2"
PRIVATE_STORE_ID = -1004319812230  

# Database
db = TinyDB('bot_database.json')
batch_table = db.table('file_batches')
user_table = db.table('users')
delete_queue_table = db.table('delete_queue') 
history_table = db.table('user_history')  

active_sending_tasks = {} 
user_queues = {}

# --- Helper Functions ---
def check_cancel(user_id):
    return user_id in active_sending_tasks and active_sending_tasks[user_id] is False

async def auto_delete_monitor(app):
    while True:
        try:
            current_time = time.time()
            all_pending = delete_queue_table.search(Query().delete_at <= current_time)
            for task in all_pending:
                for msg_id in task['message_ids']:
                    try: await app.bot.delete_message(chat_id=task['chat_id'], message_id=msg_id)
                    except: pass
                delete_queue_table.remove(Query().chat_id == task['chat_id'])
        except: pass
        await asyncio.sleep(15)

# --- Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not user_table.search(Query().user_id == user_id):
        user_table.insert({"user_id": user_id, "username": update.effective_user.username, "first_name": update.effective_user.first_name})

    args = context.args
    if args:
        batch_key = args[0]
        # Force sub check
        try:
            member = await context.bot.get_chat_member(chat_id=FORCE_SUB_CHANNEL, user_id=user_id)
            if member.status not in ['member', 'administrator', 'creator']:
                await update.message.reply_text("⚠️ Join channel first!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📢 Join", url=CHANNEL_INVITE_LINK)]]))
                return
        except: pass

        results = batch_table.search(Query().batch_key == batch_key)
        if results:
            file_list = results[0]["files"]
            active_sending_tasks[user_id] = True
            cancel_markup = InlineKeyboardMarkup([[InlineKeyboardButton("🛑 STOP", callback_data=f"cancel_{user_id}")]])
            info_msg = await update.message.reply_text("⏳ Sending files...", reply_markup=cancel_markup)
            
            sent_message_ids = [info_msg.message_id]
            delete_at = time.time() + 28800
            
            for file in file_list:
                if check_cancel(user_id): break
                try:
                    sent_msg = None
                    if file['file_type'] == 'document': sent_msg = await update.message.reply_document(file['file_id'], protect_content=True)
                    elif file['file_type'] == 'video': sent_msg = await update.message.reply_video(file['file_id'], protect_content=True)
                    elif file['file_type'] == 'photo': sent_msg = await update.message.reply_photo(file['file_id'], protect_content=True)
                    elif file['file_type'] == 'audio': sent_msg = await update.message.reply_audio(file['file_id'], protect_content=True)
                    if sent_msg: sent_message_ids.append(sent_msg.message_id)
                    await asyncio.sleep(0.5)
                except: break
            
            delete_queue_table.insert({"chat_id": update.message.chat_id, "message_ids": sent_message_ids, "delete_at": delete_at})
            if user_id in active_sending_tasks: del active_sending_tasks[user_id]
            return
    await update.message.reply_text("👋 Hello Admin!")

async def cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = int(query.data.split("_")[1])
    if query.from_user.id == uid:
        active_sending_tasks[uid] = False
        await query.answer("🛑 Stopped!")
        await query.edit_message_text("🛑 Process stopped.")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id == ADMIN_ID:
        await update.message.reply_text(f"👥 Users: {len(user_table.all())}\n📥 Downloads: {len(history_table.all())}")

async def logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    recent = history_table.all()[-15:]
    report = "📊 Recent Logs:\n\n" + "\n".join([f"👤 {e.get('first_name')}: {e.get('total_files')} files" for e in recent])
    await update.message.reply_text(report or "No logs.")

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    for u in user_table.all():
        try: await context.bot.send_message(u['user_id'], " ".join(context.args))
        except: pass
    await update.message.reply_text("✅ Done!")

async def store_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.from_user.id != ADMIN_ID: return
    uid = update.message.from_user.id
    if uid not in user_queues:
        user_queues[uid] = []
        asyncio.create_task(process_batch_queue(uid, context, update.message))
    user_queues[uid].append(update.message)

async def process_batch_queue(user_id, context, message):
    await asyncio.sleep(60)
    raw = user_queues.pop(user_id)
    meta = [{"file_id": (m.document or m.video or m.photo[-1] or m.audio).file_id, "file_type": "document"} for m in raw]
    b_key = f"batch_{int(time.time())}"
    batch_table.insert({"batch_key": b_key, "files": meta, "timestamp": time.time()})
    await message.reply_text(f"✅ Link: https://t.me/{(await context.bot.get_me()).username}?start={b_key}")

async def main_async():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handlers([CommandHandler("start", start), CommandHandler("stats", stats), CommandHandler("logs", logs), CommandHandler("broadcast", broadcast), CallbackQueryHandler(cancel_callback), MessageHandler(filters.ALL & ~filters.COMMAND, store_file)])
    await app.initialize()
    await app.start()
    asyncio.create_task(auto_delete_monitor(app))
    await app.updater.start_polling()
    while True: await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main_async())
                          
