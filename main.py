import os
import asyncio
import time
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, ContextTypes, filters
from tinydb import TinyDB, Query
from flask import Flask
from threading import Thread

# --- Flask Keep-Alive ---
app_flask = Flask('')
@app_flask.route('/')
def home(): return "Bot is running!"
def run_flask(): app_flask.run(host='0.0.0.0', port=int(os.environ.get("PORT", 8080)))
def keep_alive():
    t = Thread(target=run_flask)
    t.start()

# --- Config ---
TELEGRAM_BOT_TOKEN = "8728549558:AAHV8V_qGKUB51ynFUay5ylcS7D4K_z3eQw"
ADMIN_ID = 6598432032        
FORCE_SUB_CHANNEL = "@Kaala_1Saaya_Kuku_Fmm"             
CHANNEL_INVITE_LINK = "https://t.me/Kaala_1Saaya_Kuku_Fmm" 
PRIVATE_STORE_ID = -1003965548099  

db = TinyDB('bot_database.json')
batch_table = db.table('file_batches')
user_table = db.table('users')
delete_queue_table = db.table('delete_queue') 
history_table = db.table('user_history')  

user_queues = {}

# --- Helper Functions ---
async def auto_delete_monitor(app):
    while True:
        try:
            current_time = time.time()
            DeleteQ = Query()
            all_pending = delete_queue_table.search(DeleteQ.delete_at <= current_time)
            for task in all_pending:
                for msg_id in task['message_ids']:
                    try: await app.bot.delete_message(chat_id=task['chat_id'], message_id=msg_id)
                    except: pass
                delete_queue_table.remove((DeleteQ.chat_id == task['chat_id']) & (DeleteQ.message_ids == task['message_ids']))
        except: pass
        await asyncio.sleep(15)

async def check_user_joined(context, user_id):
    try:
        member = await context.bot.get_chat_member(chat_id=FORCE_SUB_CHANNEL, user_id=user_id)
        return member.status in ['member', 'administrator', 'creator']
    except: return False

# --- Core Features ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user_table.search(Query().user_id == user.id):
        user_table.insert({"user_id": user.id, "username": user.username, "first_name": user.first_name})
    
    args = context.args
    if args:
        if not await check_user_joined(context, user.id):
            await update.message.reply_text("⚠️ फाइल्स के लिए चैनल जॉइन करें:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📢 Join", url=CHANNEL_INVITE_LINK)]]))
            return
        
        results = batch_table.search(Query().batch_key == args[0])
        if results:
            history_table.insert({"user_id": user.id, "first_name": user.first_name, "batch_key": args[0], "time": datetime.now().strftime('%Y-%m-%d %H:%M:%S')})
            info_msg = await update.message.reply_text("⏳ Sending...")
            sent_ids = [info_msg.message_id]
            for file in results[0]["files"]:
                try:
                    m = await update.message.reply_document(file['file_id'], protect_content=True)
                    sent_ids.append(m.message_id)
                except: break
            delete_queue_table.insert({"chat_id": update.message.chat_id, "message_ids": sent_ids, "delete_at": time.time() + 28800})
            await update.message.reply_text("𝙷𝙸𝙽𝙳𝙸 𝚂𝚃𝙾𝚁𝚈\n❤️ 𝙷𝙴𝚈 𝙱𝚁𝙾 🇮🇳 \n\n📂 𝙵𝙸𝙻𝙴𝚂 𝚆𝙸𝙻𝙻 𝙱𝙴 𝙳𝙴𝙻𝙴𝚃𝙴𝙳 \n𝙰𝙵𝚃𝙴𝚁  𝟾 𝙷𝙾𝚄𝚁𝚂  𝙿𝙻𝙴𝙰𝚂𝙴 \n𝚂𝙰𝚅𝙴 𝚃𝙷𝙴𝙼 𝚂𝙾𝙼𝙴𝚆𝙷𝙴𝚁𝙴 𝚂𝙰𝙵𝙴.")
    else:
        await update.message.reply_text("👋 Hello! I am a permanent batch file store bot.")

async def stats(update, context):
    if update.effective_user.id == ADMIN_ID:
        await update.message.reply_text(f"👥 Total Users: {len(user_table.all())}\n📥 Total Requests: {len(history_table.all())}")

async def check_logs(update, context):
    if update.effective_user.id != ADMIN_ID: return
    logs = history_table.all()[-10:]
    text = "📊 Recent Logs:\n" + "\n".join([f"👤 {e.get('first_name')} - 📥 {e.get('batch_key')}" for e in logs])
    await update.message.reply_text(text)

async def broadcast(update, context):
    if update.effective_user.id != ADMIN_ID: return
    msg = " ".join(context.args)
    for user in user_table.all():
        try: await context.bot.send_message(user['user_id'], msg)
        except: pass
    await update.message.reply_text("✅ Broadcast complete.")

async def store_file(update, context):
    if update.message.from_user.id != ADMIN_ID: return
    if ADMIN_ID not in user_queues:
        user_queues[ADMIN_ID] = []
        asyncio.create_task(process_batch_queue(context, update.message))
    user_queues[ADMIN_ID].append(update.message)

async def process_batch_queue(context, message):
    await asyncio.sleep(60)
    raw_files = user_queues.pop(ADMIN_ID)
    saved_files = []
    for m in raw_files:
        if m.document:
            new_msg = await context.bot.copy_message(chat_id=PRIVATE_STORE_ID, from_chat_id=m.chat_id, message_id=m.message_id)
            saved_files.append({"file_id": new_msg.document.file_id})
    batch_key = f"batch_{int(time.time())}"
    batch_table.insert({"batch_key": batch_key, "files": saved_files})
    await message.reply_text(f"✅ Batch stored! Link: https://t.me/{(await context.bot.get_me()).username}?start={batch_key}")

# --- Main ---
if __name__ == "__main__":
    # 1. Flask को शुरू करें
    keep_alive()

    # 2. बॉट बिल्ड करें (post_init हटा दें)
    app_bot = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    # 3. हैंडलर्स जोड़ें
    app_bot.add_handlers([
        CommandHandler("start", start),
        CommandHandler("stats", stats),
        CommandHandler("logs", check_logs),
        CommandHandler("broadcast", broadcast),
        MessageHandler(filters.ChatType.PRIVATE & filters.Document.ALL & ~filters.COMMAND, store_file)
    ])

    # 4. बॉट को चलाने का सही तरीका (asyncio के साथ)
    async def main():
        # यहाँ auto_delete_monitor को एक टास्क के रूप में शुरू करें
        asyncio.create_task(auto_delete_monitor(app_bot))
        
        await app_bot.initialize()
        await app_bot.updater.start_polling()
        await app_bot.start()
        print("🤖 Bot is starting...")
        
        # बॉट को तब तक जीवित रखें जब तक स्टॉप न हो
        await asyncio.Event().wait()

    # लूप चलाएं
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
