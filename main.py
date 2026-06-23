import os
import asyncio
import time
from datetime import datetime
from zoneinfo import ZoneInfo  # बिना कोई एक्स्ट्रा लाइब्रेरी इंस्टॉल किए भारतीय समय के लिए
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, CallbackQueryHandler, ContextTypes, filters
from telegram.error import BadRequest, Forbidden, TimedOut, NetworkError
from tinydb import TinyDB, Query  # लोकल डेटाबेस

# 🔑 आपकी सभी डिटेल्स यहाँ पूरी तरह सेट हैं
TELEGRAM_BOT_TOKEN = "8963914654:AAFWhHf9mOquOCyabBwuPBI7Vlb488J5r4g"
ADMIN_ID = 6598432032        # आपकी असली टेलीग्राम आईडी AK भाई

# 📢 यूज़र को जॉइन करने के लिए बोला जाने वाला पब्लिक बैकअप चैनल
FORCE_SUB_CHANNEL = "@AllstoryFM2"  
CHANNEL_INVITE_LINK = "https://t.me/AllstoryFM2" # आपका बैकअप चैनल लिंक

# 🔒 आपका बिल्कुल अलग प्राइवेट चैनल जहाँ फाइल्स छुपाई (Store) जाएँगी
PRIVATE_STORE_ID = -1004319812230  

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
active_sending_tasks = {} 

# ⏱️ 🔄 रीस्टार्ट-प्रूफ ऑटो-डिलीट बैकग्राउंड टास्क (ब्रैकेट एरर फिक्स)
async def auto_delete_monitor(app):
    while True:
        try:
            current_time = time.time()
            DeleteQ = Query()
            all_pending = delete_queue_table.search(DeleteQ.delete_at <= current_time)
            
            for task in all_pending:
                chat_id = task['chat_id']
                message_ids = task['message_ids']
                
                print(f"🗑️ पुराना छूटा हुआ बैच डिलीट किया जा रहा है: Chat ID {chat_id}")
                for msg_id in message_ids:
                    try:
                        await app.bot.delete_message(chat_id=chat_id, message_id=msg_id)
                    except (BadRequest, Forbidden):
                        pass 
                    except Exception as e:
                        print(f"डिलिटिंग एरर: {e}")
                    await asyncio.sleep(0.1) 
                
                # यहाँ ब्रैकेट फिक्स किया गया है
                delete_queue_table.remove((DeleteQ.chat_id == chat_id) & (DeleteQ.message_ids == message_ids))
                
        except Exception as e:
            print(f"ऑटो-डिलीट मॉनिटर एरर: {e}")
            
        await asyncio.sleep(15)

# 🔄 जॉइन स्टेटस चेक करने का तरीका
async def check_user_joined(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id=FORCE_SUB_CHANNEL, user_id=user_id)
        if member.status in ['member', 'administrator', 'creator']:
            return True
        return False
    except (BadRequest, Forbidden):
        return False
    except Exception as e:
        print(f"चैनल चेक एरर: {e}")
        return True

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user:
        return
        
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
                [InlineKeyboardButton("🔄 Try Again (फाइल्स पायें)", url=f"https://t.me/{(await context.bot.get_me()).username}?start={batch_key}")]

]
            reply_markup = InlineKeyboardMarkup(keyboard)
            try:
                await update.message.reply_text(
                    "⚠️ एक्सेस अस्वीकृत (Access Denied)!\n\n"
                    "फाइल्स प्राप्त करने के लिए आपको हमारे बैकअप चैनल को जॉइन करना होगा।",
                    reply_markup=reply_markup,
                    parse_mode="Markdown"
                )
            except Forbidden: pass
            return

        # TinyDB से बैच डेटा निकालना
        BatchQ = Query()
        results = batch_table.search(BatchQ.batch_key == batch_key)
        if results:
            batch_data = results[0]
            file_list = batch_data["files"]  
            
            # ⏰ भारतीय समय (IST Zone) निकाला जा रहा है
            ist_time = datetime.now(ZoneInfo("Asia/Kolkata")).strftime('%Y-%m-%d %H:%M:%S')
            
            # डेटाबेस में भारतीय समय के साथ रिकॉर्ड सेव करना
            history_table.insert({
                "user_id": user_id,
                "first_name": user.first_name,
                "username": user.username,
                "action": "requested_files",
                "batch_key": batch_key,
                "total_files": len(file_list),
                "time": ist_time
            })
            
            # 🎯 क्लीन 'Please wait...' और '• cancel' बटन
            cancel_keyboard = [[InlineKeyboardButton("• cancel", callback_data=f"cancel_{user_id}")]]
            reply_markup = InlineKeyboardMarkup(cancel_keyboard)
            
            try:
                info_msg = await update.message.reply_text(
                    "Please wait...", 
                    reply_markup=reply_markup
                )
            except Forbidden: return
            
            active_sending_tasks[user_id] = True
            sent_message_ids = [info_msg.message_id]
            was_cancelled = False
            
            delete_at_time = time.time() + 28800 # 8 घंटे
            delete_queue_table.insert({
                "chat_id": update.message.chat_id,
                "message_ids": sent_message_ids,
                "delete_at": delete_at_time
            })
            
            for index, file in enumerate(file_list):
                if user_id not in active_sending_tasks or not active_sending_tasks[user_id]:
                    was_cancelled = True
                    break
                    
                try:
                    sent_msg = None
                    if file['file_type'] == 'document': 
                        sent_msg = await update.message.reply_document(document=file['file_id'], protect_content=True)
                    elif file['file_type'] == 'video': 
                        sent_msg = await update.message.reply_video(video=file['file_id'], protect_content=True)
                    elif file['file_type'] == 'photo': 
                        sent_msg = await update.message.reply_photo(photo=file['file_id'], protect_content=True)
                    elif file['file_type'] == 'audio': 
                        sent_msg = await update.message.reply_audio(audio=file['file_id'], protect_content=True)
                    
                    if sent_msg:
                        sent_message_ids.append(sent_msg.message_id)
                        DeleteQ = Query()
                        # 🔥 यहाँ ब्रैकेट को सही से फिक्स किया गया है ताकि एरर न आए
                        delete_queue_table.update(
                            {"message_ids": sent_message_ids},
                            (DeleteQ.chat_id == update.message.chat_id) & (DeleteQ.delete_at == delete_at_time)
                        )
                    
                    await asyncio.sleep(0.6) 
                except Forbidden: break
                except Exception as e: print(f"फाइल भेजने में एरर: {e}")

if user_id in active_sending_tasks:
                del active_sending_tasks[user_id]
                
            if not was_cancelled:
                try:
                    await context.bot.delete_message(chat_id=update.message.chat_id, message_id=info_msg.message_id)
                    sent_message_ids.remove(info_msg.message_id)
                except Exception: pass

                # 🎯 टाइपिंग स्टाइल अलर्ट मैसेज
                try:
                    alert_text = (
                        "𝙷𝙸𝙽𝙳𝙸 𝚂𝚃𝙾𝚁𝚈\n"
                        "❤️ 𝙷𝙴𝚈 𝙱𝚁𝙾 🇮🇳 \n\n"
                        "📂 𝙵𝙸𝙻𝙴𝚂 𝚆𝙸𝙻𝙻 𝙱𝙴 𝙳𝙴𝙻𝙴𝚃𝙴𝙳 \n"
                        "𝙰𝙵𝚃𝙴𝚁 [ 𝟾 𝙷𝙾𝚄𝚁𝚂 ] 𝙿𝙻𝙴𝙰𝚂𝙴 \n"
                        "𝚂𝙰𝚅𝙴 𝚃𝙷𝙴𝙼 𝚂𝙾𝙼𝙴𝚆𝙷𝙴𝚁𝙴 𝚂𝙰𝙵𝙴 . \n"
                        "𝚃𝙾 𝙶𝙴𝚃 𝙸𝚃 𝙰𝙶𝙰𝙸𝙽 , 𝚁𝙴𝙿𝙴𝙰𝚃 \n"
                        "𝚃𝙷𝙴 𝚂𝙰𝙼𝙴 𝙿𝚁𝙾𝙲𝙴𝚂𝚂 .\n\n"
                        "☝️☝️☝️☝️☝️☝️☝️☝️"
                    )
                    end_msg = await update.message.reply_text(alert_text, parse_mode="Markdown")
                    sent_message_ids.append(end_msg.message_id)
                    
                    DeleteQ = Query()
                    # 🔥 यहाँ भी ब्रैकेट फिक्स किया गया है
                    delete_queue_table.update(
                        {"message_ids": sent_message_ids},
                        (DeleteQ.chat_id == update.message.chat_id) & (DeleteQ.delete_at == delete_at_time)
                    )
                except Exception: pass
            return
        else:
            await update.message.reply_text("❌ यह लिंक अमान्य है या सिस्टम में मौजूद नहीं है।")
            return

    await update.message.reply_text("👋 नमस्ते! मैं एक परमानेंट बैच फाइल स्टोर बोट हूँ।")

# कैंसिल बटन क्लिक हैंडलर (ब्रैकेट फिक्स)
async def cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer() 
    
    data = query.data
    if data.startswith("cancel_"):
        target_user_id = int(data.split("_")[1])
        
        if query.from_user.id == target_user_id:
            if target_user_id in active_sending_tasks:
                active_sending_tasks[target_user_id] = False 
                
                alert_text = (
                    "🛑 Sending Cancelled!\n\n"
                    "𝙷𝙸𝙽𝙳𝙸 𝚂𝚃𝙾𝚁𝚈\n"
                    "❤️ 𝙷𝙴𝚈 𝙱𝚁𝙾 🇮🇳 \n\n"
                    "📂 𝙵𝙸𝙻𝙴𝚂 𝚆𝙸𝙻𝙻 𝙱𝙴 𝙳𝙴𝙻𝙴𝚃𝙴𝙳 \n"
                    "𝙰𝙵𝚃𝙴𝚁 [ 𝟾 𝙷𝙾𝚄𝚁𝚂 ] 𝙿𝙻𝙴𝙰𝚂𝙴 \n"
                    "𝚂𝙰𝚅𝙴 𝚃𝙷𝙴𝙼 𝚂𝙾𝙼𝙴𝚆𝙷𝙴𝚁𝙴 𝚂𝙰𝙵𝙴 . \n"
                    "𝚃𝙾 𝙶𝙴𝚃 𝙸𝚃 𝙰𝙶𝙰𝙸𝙽 , 𝚁𝙴𝙿𝙴𝙰𝚃 \n"
                    "𝚃𝙷𝙴 𝚂𝙰𝙼𝙴 𝙿𝚁𝙾𝙲𝙴𝚂𝚂 .\n\n"
                    "☝️☝️☝️☝️☝️☝️☝️☝️"
                )
                await query.edit_message_text(alert_text, parse_mode="Markdown")
                
                # ⏰ भारतीय समय (IST Zone) यहाँ भी सेट है
                ist_time = datetime.now(ZoneInfo("Asia/Kolkata")).strftime('%Y-%m-%d %H:%M:%S')
                
                history_table.insert({
                    "user_id": target_user_id,
                    "first_name": query.from_user.first_name,
                    "username": query.from_user.username,
                    "action": "cancelled_sending",
                    "time": ist_time
                })
            else:
                try:
                    await query.delete_message()
                except Exception: pass

# एडमिन कमांड: लॉग्स चेक करने के लिए
async def check_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user: return
    user_id = update.effective_user.id

if user_id != ADMIN_ID:
        await update.message.reply_text("🔒 माफ़ कीजिये, यह कमांड केवल एडमिन के लिए है।")
        return
    
    try:
        logs = history_table.all()
        if not logs:
            await update.message.reply_text("📝 डेटाबेस में अभी तक किसी भी यूज़र की डाउनलोड हिस्ट्री रिकॉर्ड नहीं हुई है।")
            return
            
        recent_logs = logs[-15:]
        log_text = "📊 यूज़र डाउनलोड हिस्ट्री लॉग्स (Recent Logs):\n\n"
        
        for entry in recent_logs:
            name = entry.get('first_name', 'Unknown')
            uid = entry.get('user_id', 'N/A')
            action = entry.get('action', 'N/A')
            b_key = entry.get('batch_key', 'N/A')
            l_time = entry.get('time', 'N/A')
            
            if action == "requested_files":
                log_text += f"👤 {name} ({uid})\n📥 फाइलें लीं: {b_key}\n⏰ समय: {l_time}\n\n"
            else:
                log_text += f"👤 {name} ({uid})\n🛑 भेजना कैंसिल किया\n⏰ समय: {l_time}\n\n"
                
        await update.message.reply_text(log_text, parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ लॉग्स फैच करने में एरर आया: {e}", parse_mode="Markdown")

# 👥 एडमिन कमांड: स्टैट्स देखने के लिए
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user: return
    user_id = update.effective_user.id
    if user_id != ADMIN_ID: return
        
    try:
        total_users = len(user_table.all())
        total_requests = len(history_table.all())
        await update.message.reply_text(
            f"👥 कुल यूज़र्स (Total Users): {total_users}\n"
            f"📥 कुल डाउनलोड रिक्वेस्ट: {total_requests}", 
            parse_mode="Markdown"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ स्टैट्स निकालने में एरर: {e}", parse_mode="Markdown")

# ब्रॉडकास्ट फीचर
async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user or update.effective_user.id != ADMIN_ID: 
        return

    if not context.args and not update.message.reply_to_message:
        await update.message.reply_text("❌ उपयोग कैसे करें:\n1. /broadcast आपका संदेश लिखें।\n2. या किसी photo/file पर रिप्लाई करके /broadcast लिखें।", parse_mode="Markdown")
        return

    all_users = user_table.all()
    if not all_users:
        await update.message.reply_text("❌ डेटाबेस में कोई भी यूजर मौजूद नहीं है।")
        return

    status_msg = await update.message.reply_text(f"📢 कुल {len(all_users)} यूज़र्स को ब्रॉडकास्ट भेजा जा रहा है...", parse_mode="Markdown")
    success_count = 0
    failed_count = 0

    for user in all_users:
        target_id = user['user_id']
        try:
            if update.message.reply_to_message:
                await context.bot.copy_message(chat_id=target_id, from_chat_id=update.message.chat_id, message_id=update.message.reply_to_message.message_id)
            else:
                text_to_send = " ".join(context.args)
                await context.bot.send_message(chat_id=target_id, text=text_to_send, parse_mode="Markdown")
            success_count += 1
            await asyncio.sleep(0.05)
        except (BadRequest, Forbidden):
            failed_count += 1
            user_table.remove(Query().user_id == target_id)
        except Exception:
            failed_count += 1

    await status_msg.edit_text(f"✅ ब्रॉडकास्ट प्रक्रिया पूरी हुई!\n\n👥 कुल यूज़र्स: {len(all_users)}\n📥 सफलतापूर्वक पहुँचा: {success_count}\n❌ असफल: {failed_count}", parse_mode="Markdown")

async def get_link_manually(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.effective_user or update.effective_user.id != ADMIN_ID: return

if ADMIN_ID not in backup_queues or not backup_queues[ADMIN_ID]:
        await update.message.reply_text("❌ अभी बैकअप में कोई प्रोसेस की हुई化 फाइल नहीं है।")
        return
        
    saved_files_meta = backup_queues[ADMIN_ID]
    batch_key = f"batch_{int(time.time())}"
    batch_table.insert({"batch_key": batch_key, "files": saved_files_meta, "timestamp": time.time()})
    bot_username = (await context.bot.get_me()).username
    share_link = f"https://t.me/{bot_username}?start={batch_key}"
    await update.message.reply_text(f"🎯 एमर्जेंसी लिंक तैयार है:\n\n{share_link}", parse_mode="Markdown")

async def process_batch_queue(user_id, context: ContextTypes.DEFAULT_TYPE, message):
    await asyncio.sleep(60) 
    if user_id not in user_queues or not user_queues[user_id]: return

    raw_files = user_queues[user_id]
    del user_queues[user_id]
    
    status_msg = await message.reply_text(f"⏳ आपकी कुल {len(raw_files)} फाइल्स को प्रोसेस किया जा रहा है...")
    saved_files_meta = []
    first_forwarded_id = None

    for msg in raw_files:
        if not msg: continue
        file_id, file_type = None, None
        if msg.document: file_id, file_type = msg.document.file_id, 'document'
        elif msg.video: file_id, file_type = msg.video.file_id, 'video'
        elif msg.photo: file_id, file_type = msg.photo[-1].file_id, 'photo'
        elif msg.audio: file_id, file_type = msg.audio.file_id, 'audio'

        if file_id:
            try:
                forwarded_msg = await context.bot.forward_message(chat_id=PRIVATE_STORE_ID, from_chat_id=msg.chat_id, message_id=msg.message_id)
                if not first_forwarded_id: first_forwarded_id = forwarded_msg.message_id
                saved_files_meta.append({"file_id": file_id, "file_type": file_type})
                await asyncio.sleep(0.5) 
            except TimedOut:
                saved_files_meta.append({"file_id": file_id, "file_type": file_type})
                await asyncio.sleep(1)
            except Exception as e: print(f"फॉरवर्ड एरर: {e}")

    if saved_files_meta:
        backup_queues[user_id] = saved_files_meta
        batch_key = f"batch_{first_forwarded_id}" if first_forwarded_id else f"batch_{int(time.time())}"
        batch_table.insert({"batch_key": batch_key, "files": saved_files_meta, "timestamp": time.time()})
        bot_username = (await context.bot.get_me()).username
        share_link = f"https://t.me/{bot_username}?start={batch_key}"
        await status_msg.edit_text(f"✅ सभी फाइल्स सुरक्षित स्टोर हो गई हैं!\n\n🔗 आपका बैच लिंक:\n{share_link}", parse_mode="Markdown")
    else:
        await status_msg.edit_text("❌ कोई वैलिड फाइल प्रोसेस नहीं हो सकी।")

async def store_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not update.message.from_user: return
    user_id = update.message.from_user.id
    if user_id != ADMIN_ID:
        await update.message.reply_text("🔒 माफ़ कीजिये, यह बोट केवल एडमिन के लिए है।")
        return

    if user_id not in user_queues:
        user_queues[user_id] = []
        asyncio.create_task(process_batch_queue(user_id, context, update.message))
        await update.message.reply_text("⏱️ बैच अपलोड शुरू हो चुका है! आपके पास 1 मिनट का समय है...")
    user_queues[user_id].append(update.message)

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    print(f"⚠️ टेलीग्राम नेटवर्क एरर (इग्नोर किया गया): {context.error}")

async def post_init(application) -> None:
    asyncio.create_task(auto_delete_monitor(application))

if name == "main":
    app = (
        ApplicationBuilder()
        .token(TELEGRAM_BOT_TOKEN)
        .read_timeout(30)
        .connect_timeout(30)
        .post_init(post_init)  
        .build()
    )
    
    app.add_handler(CommandHandler("start", start))

app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("logs", check_logs)) 
    app.add_handler(CommandHandler("getlink", get_link_manually))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(CallbackQueryHandler(cancel_callback))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, store_file))
    app.add_error_handler(error_handler)
    
    print("🤖 Bot is successfully configured with Fixed TinyDB Queries!")
    app.run_polling()
