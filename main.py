import os
import asyncio
import time
import signal
from datetime import datetime, timedelta, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, CallbackQueryHandler, ContextTypes, filters
from telegram.error import BadRequest, Forbidden, TimedOut, NetworkError
from tinydb import TinyDB, Query

# 🔑 आपकी सभी डिटेल्स यहाँ पूरी तरह सेट हैं
TELEGRAM_BOT_TOKEN = "8963914654:AAFWhHf9mOquOCyabBwuPBI7Vlb488J5r4g"
ADMIN_ID = 6598432032        

FORCE_SUB_CHANNEL = "@AllstoryFM2"  
CHANNEL_INVITE_LINK = "https://t.me/AllstoryFM2"
PRIVATE_STORE_ID = -1004319812230  

# Local Database Setup
db = TinyDB('bot_database.json')
batch_table = db.table('file_batches')
user_table = db.table('users')
delete_queue_table = db.table('delete_queue') 
history_table = db.table('user_history')  
print("✅ Local TinyDB Active!")

user_queues = {}
backup_queues = {}
active_sending_tasks = {} 

async def auto_delete_monitor(app):
    while True:
        try:
            current_time = time.time()
            DeleteQ = Query()
            all_pending = delete_queue_table.search(DeleteQ.delete_at <= current_time)
            
            for task in all_pending:
                chat_id = task['chat_id']
                message_ids = task['message_ids']
                
                print(f"🗑️ Deleting expired batch: Chat ID {chat_id}")
                for msg_id in message_ids:
                    try:
                        await app.bot.delete_message(chat_id=chat_id, message_id=msg_id)
                    except (BadRequest, Forbidden): pass
                    except Exception as e: print(f"Delete Error: {e}")
                    await asyncio.sleep(0.1) 
                
                delete_queue_table.remove((DeleteQ.chat_id == chat_id) & (DeleteQ.message_ids == message_ids))
        except Exception as e:
            print(f"Monitor Error: {e}")
        await asyncio.sleep(15)

async def check_user_joined(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id=FORCE_SUB_CHANNEL, user_id=user_id)
        return member.status in ['member', 'administrator', 'creator']
    except Exception:
        return True

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user: return
    user = update.effective_user
    user_id = user.id
    
    UserQ = Query()
    if not user_table.search(UserQ.user_id == user_id):
        user_table.insert({"user_id": user_id, "username": user.username, "first_name": user.first_name})

    args = context.args
    if args:
        batch_key = args[0]
        is_joined = await check_user_joined(context, user_id)
        
        if not is_joined:
            keyboard = [
                [InlineKeyboardButton("📢 Join Backup Channel", url=CHANNEL_INVITE_LINK)],
                [InlineKeyboardButton("🔄 Try Again", url=f"https://t.me/{(await context.bot.get_me()).username}?start={batch_key}")]
            ]
            try:
                await update.message.reply_text("⚠️ Access Denied! Join backup channel.", reply_markup=InlineKeyboardMarkup(keyboard))
            except Forbidden: pass
            return

        BatchQ = Query()
        results = batch_table.search(BatchQ.batch_key == batch_key)
        if results:
            batch_data = results[0]
            file_list = batch_data["files"]  
            
            ist_now = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
            ist_time = ist_now.strftime('%Y-%m-%d %H:%M:%S')
            
            history_table.insert({
                "user_id": user_id, "first_name": user.first_name, "username": user.username,
                "action": "requested_files", "batch_key": batch_key, "total_files": len(file_list), "time": ist_time
            })
            
            cancel_markup = InlineKeyboardMarkup([[InlineKeyboardButton("• cancel", callback_data=f"cancel_{user_id}")]])
            try:
                info_msg = await update.message.reply_text("Please wait...", reply_markup=cancel_markup)
            except Forbidden: return
            
            active_sending_tasks[user_id] = True
            sent_message_ids = [info_msg.message_id]
            was_cancelled = False
            delete_at_time = time.time() + 28800
            
            delete_queue_table.insert({"chat_id": update.message.chat_id, "message_ids": sent_message_ids, "delete_at": delete_at_time})
            
            for file in file_list:
                if user_id not in active_sending_tasks or not active_sending_tasks[user_id]:
                    was_cancelled = True
                    break
                try:
                    sent_msg = None
                    if file['file_type'] == 'document': sent_msg = await update.message.reply_document(document=file['file_id'], protect_content=True)
                    elif file['file_type'] == 'video': sent_msg = await update.message.reply_video(video=file['file_id'], protect_content=True)
                    elif file['file_type'] == 'photo': sent_msg = await update.message.reply_photo(photo=file['file_id'], protect_content=True)
                    elif file['file_type'] == 'audio': sent_msg = await update.message.reply_audio(audio=file['file_id'], protect_content=True)
                    
                    if sent_msg:
                        sent_message_ids.append(sent_msg.message_id)
                        delete_queue_table.update({"message_ids": sent_message_ids}, (Query().chat_id == update.message.chat_id) & (Query().delete_at == delete_at_time))
                    await asyncio.sleep(0.6)
                except Forbidden: break
                except Exception as e: print(f"Send Error: {e}")

            if user_id in active_sending_tasks: del active_sending_tasks[user_id]
            if not was_cancelled:
                try: await context.bot.delete_message(chat_id=update.message.chat_id, message_id=info_msg.message_id)
                except Exception: pass
                try:
                    alert_text = "𝙷𝙸𝙽𝙳𝙸 𝚂𝚃𝙾𝚁𝚈\n❤️ 𝙷𝙴𝚈 𝙱𝚁𝙾 🇮🇳 \n\n📂 𝙵𝙸𝙻𝙴𝚂 𝚆𝙸𝙻𝙻 𝙱𝙴 𝙳𝙴𝙻𝙴𝚃𝙴𝙳 \n𝙰𝙵𝚃𝙴𝚁 [ 𝟾 𝙷𝙾𝚄𝚁𝚂 ] 𝙿𝙻𝙴𝙰𝚂𝙴 \n𝚂𝙰𝚅𝙴 𝚃𝙷𝙴𝙼 𝚂𝙾𝙼𝙴𝚆𝙷𝙴𝚁𝙴 𝚂𝙰𝙵𝙴 .\n\n☝️☝️☝️☝️☝️"
                    end_msg = await update.message.reply_text(alert_text, parse_mode="Markdown")
                    sent_message_ids.append(end_msg.message_id)
                    delete_queue_table.update({"message_ids": sent_message_ids}, (Query().chat_id == update.message.chat_id) & (Query().delete_at == delete_at_time))
                except Exception: pass
            return
    await update.message.reply_text("👋 Hello Admin!")

async def cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data.startswith("cancel_"):
        uid = int(query.data.split("_")[1])
        if query.from_user.id == uid and uid in active_sending_tasks:
            active_sending_tasks[uid] = False
            await query.edit_message_text("🛑 Sending Cancelled!")

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id == ADMIN_ID:
        await update.message.reply_text(f"👥 Users: {len(user_table.all())}\n📥 Downloads: {len(history_table.all())}")

async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or update.effective_user.id != ADMIN_ID: return
    all_users = user_table.all()
    for u in all_users:
        try:
            if update.message.reply_to_message:
                await context.bot.copy_message(chat_id=u['user_id'], from_chat_id=update.message.chat_id, message_id=update.message.reply_to_message.message_id)
            else:
                await context.bot.send_message(chat_id=u['user_id'], text=" ".join(context.args))
            await asyncio.sleep(0.05)
        except Exception: pass
    await update.message.reply_text("✅ Broadcast Completed!")

async def process_batch_queue(user_id, context: ContextTypes.DEFAULT_TYPE, message):
    await asyncio.sleep(60)
    if user_id not in user_queues: return
    raw_files = user_queues[user_id]
    del user_queues[user_id]
    
    saved_meta = []
    first_id = None
    for msg in raw_files:
        fid, ftype = None, None
        if msg.document: fid, ftype = msg.document.file_id, 'document'
        elif msg.video: fid, ftype = msg.video.file_id, 'video'
        elif msg.photo: fid, ftype = msg.photo[-1].file_id, 'photo'
        elif msg.audio: fid, ftype = msg.audio.file_id, 'audio'
        
        if fid:
            try:
                f_msg = await context.bot.forward_message(chat_id=PRIVATE_STORE_ID, from_chat_id=msg.chat_id, message_id=msg.message_id)
                if not first_id: first_id = f_msg.message_id
                saved_meta.append({"file_id": fid, "file_type": ftype})
                await asyncio.sleep(0.5)
            except Exception: pass
            
    if saved_meta:
        b_key = f"batch_{first_id if first_id else int(time.time())}"
        batch_table.insert({"batch_key": b_key, "files": saved_meta, "timestamp": time.time()})
        await message.reply_text(f"✅ Link Created:\nhttps://t.me/{(await context.bot.get_me()).username}?start={b_key}")

async def store_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or update.message.from_user.id != ADMIN_ID: return
    uid = update.message.from_user.id
    if uid not in user_queues:
        user_queues[uid] = []
        asyncio.create_task(process_batch_queue(uid, context, update.message))
        await update.message.reply_text("⏳ Processing batch upload...")
    user_queues[uid].append(update.message)

# Main Native Async Engine for Python 3.14 (No polling CB issues)
async def main_async():
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CallbackQueryHandler(cancel_callback))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, store_file))
    
    await app.initialize()
    await app.updater.initialize()
    await app.start()
    
    # Run auto delete task
    asyncio.create_task(auto_delete_monitor(app))
    
    print("🤖 Native Bot Engine Started Successfully on Python 3.14!")
    
    # Custom compatible async loop instead of app.run_polling()
    offset = 0
    stop_event = asyncio.Event()
    
    # Handle termination signals safely
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try: loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError: pass

    try:
        await app.updater.start_polling(poll_interval=1.0, timeout=10, drop_pending_updates=True)
        while not stop_event.is_set():
            await asyncio.sleep(1)
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()

def main():
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        print("Bot Stopped Safely.")

if __name__ == "__main__":
    main()
          
