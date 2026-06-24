import os
import asyncio
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, ContextTypes, filters
from telegram.error import BadRequest, Forbidden, TimedOut
from tinydb import TinyDB, Query

# 🔑 अपनी डिटेल्स यहाँ भरें
TELEGRAM_BOT_TOKEN = "8728549558:AAHV8V_qGKUB51ynFUay5ylcS7D4K_z3eQw"
ADMIN_ID = 6598432032        
FORCE_SUB_CHANNEL = "@Kaala_1Saaya_Kuku_Fmm"             
CHANNEL_INVITE_LINK = "https://t.me/Kaala_1Saaya_Kuku_Fmm" 
PRIVATE_STORE_ID = -1003965548099  


# Local Database Setup
db = TinyDB('bot_database.json')
batch_table = db.table('file_batches')
user_table = db.table('users')
delete_queue_table = db.table('delete_queue') 
history_table = db.table('user_history')  
print("✅ लोकल TinyDB डेटाबेस सफलतापूर्वक एक्टिव हो चुका है!")

# ग्लोबल डिक्शनरी
user_queues = {}
backup_queues = {}

async def auto_delete_monitor(app):
    while True:
        try:
            current_time = time.time()
            DeleteQ = Query()
            all_pending = delete_queue_table.search(DeleteQ.delete_at <= current_time)
            for task in all_pending:
                chat_id = task['chat_id']
                message_ids = task['message_ids']
                for msg_id in message_ids:
                    try: await app.bot.delete_message(chat_id=chat_id, message_id=msg_id)
                    except: pass
                    await asyncio.sleep(0.1) 
                delete_queue_table.remove((DeleteQ.chat_id == chat_id) & (DeleteQ.message_ids == message_ids))
        except Exception as e: print(f"ऑटो-डिलीट मॉनिटर एरर: {e}")
        await asyncio.sleep(15)

async def run_post_init(application):
    asyncio.create_task(auto_delete_monitor(application))

async def check_user_joined(context, user_id):
    try:
        member = await context.bot.get_chat_member(chat_id=FORCE_SUB_CHANNEL, user_id=user_id)
        return member.status in ['member', 'administrator', 'creator']
    except: return False

# 🚀 बैकग्राउंड फाइल सेंडर (मल्टी-यूज़र सपोर्ट के लिए)
async def send_files_logic(update, context, batch_key):
    user = update.effective_user
    results = batch_table.search(Query().batch_key == batch_key)
    
    if not results:
        await update.message.reply_text("❌ यह लिंक अमान्य है।")
        return

    file_list = results[0]["files"]
    history_table.insert({"user_id": user.id, "first_name": user.first_name, "username": user.username, "action": "requested_files", "batch_key": batch_key, "time": datetime.now(ZoneInfo("Asia/Kolkata")).strftime('%Y-%m-%d %H:%M:%S')})
    
    info_msg = await update.message.reply_text("⏳ Sending files...")
    sent_message_ids = [info_msg.message_id]
    delete_at_time = time.time() + 28800 
    
    for file in file_list:
        try:
            sent_msg = None
            if file['file_type'] == 'document': sent_msg = await update.message.reply_document(file['file_id'], protect_content=True)
            elif file['file_type'] == 'video': sent_msg = await update.message.reply_video(file['file_id'], protect_content=True)
            elif file['file_type'] == 'photo': sent_msg = await update.message.reply_photo(file['file_id'], protect_content=True)
            elif file['file_type'] == 'audio': sent_msg = await update.message.reply_audio(file['file_id'], protect_content=True)
            
            if sent_msg: sent_message_ids.append(sent_msg.message_id)
            await asyncio.sleep(0.4) 
        except: break

    delete_queue_table.insert({"chat_id": update.message.chat_id, "message_ids": sent_message_ids, "delete_at": delete_at_time})
    try: await context.bot.delete_message(chat_id=update.message.chat_id, message_id=info_msg.message_id)
    except: pass
    
    alert_text = "𝙷𝙸𝙽𝙳𝙸 𝚂𝚃𝙾𝚁𝚈\n❤️ 𝙷𝙴𝚈 𝙱𝚁𝙾 🇮🇳 \n\n📂 𝙵𝙸𝙻𝙴𝚂 𝚆𝙸𝙻𝙻 𝙱𝙴 𝙳𝙴𝙻𝙴𝚃𝙴𝙳 \n𝙰𝙵𝚃𝙴𝚁 [ 𝟾 𝙷𝙾𝚄𝚁𝚂 ] 𝙿𝙻𝙴𝙰𝚂𝙴 \n𝚂𝙰𝚅𝙴 𝚃𝙷𝙴𝙼 𝚂𝙾𝙼𝙴𝚆𝙷𝙴𝚁𝙴 𝚂𝙰𝙵𝙴."
    await update.message.reply_text(alert_text, parse_mode="Markdown")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user_table.search(Query().user_id == user.id):
        user_table.insert({"user_id": user.id, "username": user.username, "first_name": user.first_name})

    args = context.args
    if args:
        batch_key = args[0]
        if not await check_user_joined(context, user.id):
            keyboard = [[InlineKeyboardButton("📢 Join Channel", url=CHANNEL_INVITE_LINK)]]
            await update.message.reply_text("⚠️ फाइल्स के लिए चैनल जॉइन करें:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
            return
        
        # यहाँ से मल्टी-टास्किंग शुरू होती है
        asyncio.create_task(send_files_logic(update, context, batch_key))
        return

    await update.message.reply_text("👋 Hello! I am a permanent batch file store bot.")

# बाकी के फंक्शन्स (stats, logs, broadcast, store_file आदि) वही रहेंगे...
async def check_logs(update, context):
    if update.effective_user.id != ADMIN_ID: return
    logs = history_table.all()[-15:]
    log_text = "📊 Recent Logs:\n\n" + "".join([f"👤 {e.get('first_name')}\n📥 {e.get('batch_key')}\n⏰ {e.get('time')}\n\n" for e in logs])
    await update.message.reply_text(log_text)

async def stats(update, context):
    if update.effective_user.id == ADMIN_ID:
        await update.message.reply_text(f"👥 Total Users: {len(user_table.all())}\n📥 Total Requests: {len(history_table.all())}")

async def broadcast(update, context):
    if update.effective_user.id != ADMIN_ID: return
    all_users = user_table.all()
    for user in all_users:
        try:
            if update.message.reply_to_message:
                await context.bot.copy_message(user['user_id'], update.message.chat_id, update.message.reply_to_message.message_id)
            else:
                await context.bot.send_message(user['user_id'], " ".join(context.args))
            await asyncio.sleep(0.05)
        except: pass
    await update.message.reply_text("✅ Broadcast complete.")

async def get_link_manually(update, context):
    if update.effective_user.id != ADMIN_ID: return
    if ADMIN_ID not in backup_queues: return
    batch_key = f"batch_{int(time.time())}"
    batch_table.insert({"batch_key": batch_key, "files": backup_queues[ADMIN_ID], "timestamp": time.time()})
    await update.message.reply_text(f"🔗 Link: https://t.me/{(await context.bot.get_me()).username}?start={batch_key}")

async def process_batch_queue(user_id, context, message):
    await asyncio.sleep(60)
    if user_id not in user_queues: return
    raw_files = user_queues.pop(user_id)
    saved_files = []
    for msg in raw_files:
        file_id = msg.document.file_id if msg.document else (msg.video.file_id if msg.video else (msg.photo[-1].file_id if msg.photo else (msg.audio.file_id if msg.audio else None)))
        if file_id:
            try:
                await context.bot.forward_message(PRIVATE_STORE_ID, msg.chat_id, msg.message_id)
                saved_files.append({"file_id": file_id, "file_type": 'document' if msg.document else ('video' if msg.video else ('photo' if msg.photo else 'audio'))})
                await asyncio.sleep(0.5)
            except: pass
    backup_queues[user_id] = saved_files
    await message.reply_text("✅ Batch stored!")

async def store_file(update, context):
    # 🛠️ यह लाइन एरर को फिक्स करेगी
    if not update.message or not update.message.from_user:
        return 

    if update.message.from_user.id != ADMIN_ID: return
    if update.message.from_user.id not in user_queues:
        user_queues[update.message.from_user.id] = []
        asyncio.create_task(process_batch_queue(update.message.from_user.id, context, update.message))
    user_queues[update.message.from_user.id].append(update.message)


async def error_handler(update, context):
    print(f"Update {update} caused error {context.error}")

if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).post_init(run_post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("logs", check_logs))
    app.add_handler(CommandHandler("getlink", get_link_manually))
    app.add_handler(CommandHandler("broadcast", broadcast))
    # 🛠️ यहाँ PRIVATE चैट्स ही प्रोसेस होंगी
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.ALL & ~filters.COMMAND, store_file))
    app.add_error_handler(error_handler)
    print("🤖 Bot is starting...")
    app.run_polling(drop_pending_updates=True)
