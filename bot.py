import asyncio
import logging
import sqlite3
import os
import json
from datetime import datetime
from typing import Optional

from aiogram import Bot, Dispatcher, Router, types, F
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton, WebAppInfo, FSInputFile
)
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramAPIError
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

# ==================== LOAD ENVIRONMENT VARIABLES ====================
load_dotenv()

# ==================== CONFIGURATION ====================
BOT_TOKEN = os.getenv("BOT_TOKEN", "8777964457:AAH8LbGPU-3EdekLUJbCy44j15c7MXbGr6k")
REQUIRED_CHANNEL = os.getenv("REQUIRED_CHANNEL", "@masutech")
ADMIN_IDS = [int(id.strip()) for id in os.getenv("ADMIN_IDS", "7602822493").split(",")]
MINI_APP_URL = os.getenv("MINI_APP_URL", "https://digital-buy.taskupjob.top/")

# ==================== DATABASE ====================
DB_PATH = "bot.db"

def init_db():
    """Initialize database tables"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Users table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER UNIQUE NOT NULL,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_blocked BOOLEAN DEFAULT 0
        )
    ''')
    
    # Broadcast history table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS broadcasts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id INTEGER,
            message TEXT,
            total_sent INTEGER,
            failed INTEGER,
            sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()
    print("✅ Database initialized!")

def add_user(telegram_id: int, username: str = None, first_name: str = None, last_name: str = None):
    """Add or update user in database"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    try:
        cursor.execute('''
            INSERT OR REPLACE INTO users (telegram_id, username, first_name, last_name, joined_at)
            VALUES (?, ?, ?, ?, COALESCE((SELECT joined_at FROM users WHERE telegram_id = ?), CURRENT_TIMESTAMP))
        ''', (telegram_id, username, first_name, last_name, telegram_id))
        conn.commit()
        return True
    except Exception as e:
        logging.error(f"Error adding user: {e}")
        return False
    finally:
        conn.close()

def get_all_users():
    """Get all non-blocked users"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT telegram_id, username, first_name, joined_at FROM users WHERE is_blocked = 0')
    users = cursor.fetchall()
    conn.close()
    return users

def get_total_users():
    """Get total user count"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM users WHERE is_blocked = 0')
    count = cursor.fetchone()[0]
    conn.close()
    return count

def save_broadcast(admin_id: int, message: str, total_sent: int, failed: int):
    """Save broadcast history"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO broadcasts (admin_id, message, total_sent, failed)
        VALUES (?, ?, ?, ?)
    ''', (admin_id, message, total_sent, failed))
    conn.commit()
    conn.close()

# ==================== MIDDLEWARE ====================
class ForceJoinMiddleware:
    """Middleware to check if user is member of required channel"""
    
    def __init__(self, bot: Bot):
        self.bot = bot
    
    async def __call__(self, handler, event, data):
        user_id = None
        
        if isinstance(event, Message):
            user_id = event.from_user.id
        elif isinstance(event, CallbackQuery):
            user_id = event.from_user.id
        
        if user_id:
            # Skip check for admin commands
            if user_id in ADMIN_IDS and isinstance(event, Message) and event.text and event.text.startswith('/'):
                return await handler(event, data)
            
            try:
                member = await self.bot.get_chat_member(
                    chat_id=REQUIRED_CHANNEL,
                    user_id=user_id
                )
                
                if member.status in ["left", "kicked"]:
                    keyboard = InlineKeyboardMarkup(
                        inline_keyboard=[
                            [InlineKeyboardButton(
                                text="🔵 চ্যানেল জয়েন করুন",
                                url=f"https://t.me/{REQUIRED_CHANNEL.lstrip('@')}"
                            )],
                            [InlineKeyboardButton(
                                text="✅ জয়েন করে চেক করুন",
                                callback_data="check_join"
                            )]
                        ]
                    )
                    
                    if isinstance(event, Message):
                        await event.answer(
                            "⚠️ বট ব্যবহার করতে প্রথমে আমাদের চ্যানেল জয়েন করুন!\n\n"
                            "নিচের বাটনে ক্লিক করে চ্যানেল জয়েন করুন এবং তারপর 'চেক করুন' বাটনে ক্লিক করুন।",
                            reply_markup=keyboard
                        )
                    elif isinstance(event, CallbackQuery):
                        await event.message.edit_text(
                            "⚠️ বট ব্যবহার করতে প্রথমে আমাদের চ্যানেল জয়েন করুন!\n\n"
                            "নিচের বাটনে ক্লিক করে চ্যানেল জয়েন করুন এবং তারপর 'চেক করুন' বাটনে ক্লিক করুন।",
                            reply_markup=keyboard
                        )
                    return  # Stop processing
                    
            except TelegramAPIError as e:
                logging.error(f"Force join check error: {e}")
                # If bot is not admin or channel not found, allow access
                pass
        
        return await handler(event, data)

# ==================== STATES ====================
class BroadcastState(StatesGroup):
    waiting_for_message = State()

# ==================== ROUTERS ====================
router = Router()

# ==================== HANDLERS ====================

# --- START COMMAND ---
@router.message(CommandStart())
async def start_command(message: Message):
    """Handle /start command"""
    # Add user to database
    add_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name
    )
    
    # Main menu keyboard with Mini App button
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(
                text="🚀 অ্যাপ খুলুন",
                web_app=WebAppInfo(url=MINI_APP_URL)
            )],
            [KeyboardButton(text="ℹ️ সাহায্য")]
        ],
        resize_keyboard=True,
        one_time_keyboard=False
    )
    
    welcome_text = (
        f"👋 স্বাগতম {message.from_user.first_name}!\n\n"
        f"আমি একটি Telegram Mini App বট।\n"
        f"নিচের 'অ্যাপ খুলুন' বাটনে ক্লিক করে অ্যাপটি ব্যবহার করুন।\n\n"
        f"📌 চ্যানেল জয়েন করতে ভুলবেন না!"
    )
    
    await message.answer(welcome_text, reply_markup=keyboard)

# --- CHECK JOIN CALLBACK ---
@router.callback_query(lambda c: c.data == "check_join")
async def check_join_callback(callback: CallbackQuery):
    """Handle check join callback"""
    try:
        member = await callback.bot.get_chat_member(
            chat_id=REQUIRED_CHANNEL,
            user_id=callback.from_user.id
        )
        
        if member.status not in ["left", "kicked"]:
            # User is member - show main menu
            keyboard = ReplyKeyboardMarkup(
                keyboard=[
                    [KeyboardButton(
                        text="🚀 অ্যাপ খুলুন",
                        web_app=WebAppInfo(url=MINI_APP_URL)
                    )],
                    [KeyboardButton(text="ℹ️ সাহায্য")]
                ],
                resize_keyboard=True
            )
            
            await callback.message.delete()
            await callback.message.answer(
                "✅ ধন্যবাদ! এখন আপনি বট ব্যবহার করতে পারেন।\n"
                "নিচের বাটনে ক্লিক করে অ্যাপ খুলুন।",
                reply_markup=keyboard
            )
        else:
            await callback.answer(
                "❌ আপনি এখনও চ্যানেল জয়েন করেননি!\n"
                "দয়া করে চ্যানেল জয়েন করে আবার চেষ্টা করুন।",
                show_alert=True
            )
    except TelegramAPIError as e:
        logging.error(f"Check join error: {e}")
        await callback.answer("ত্রুটি হয়েছে! দয়া করে আবার চেষ্টা করুন।", show_alert=True)

# --- HELP COMMAND ---
@router.message(F.text == "ℹ️ সাহায্য")
@router.message(Command("help"))
async def help_command(message: Message):
    """Handle help command"""
    help_text = (
        "🤖 *বট সাহায্য*\n\n"
        "📌 *মুল ফিচার:*\n"
        "• চ্যানেল জয়েন বাধ্যতামূলক\n"
        "• Telegram Mini App সাপোর্ট\n"
        "• অ্যাডমিন প্যানেল\n\n"
        
        "🔹 *অ্যাডমিন কমান্ড:*\n"
        "`/users` - ইউজার লিস্ট দেখুন\n"
        "`/stats` - বট পরিসংখ্যান দেখুন\n"
        "`/broadcast` - সবাইকে মেসেজ পাঠান\n"
        "`/broadcast_file` - ফাইল ব্রডকাস্ট করুন\n"
        "`/admin` - অ্যাডমিন প্যানেল খুলুন\n\n"
        
        "💡 *টিপস:*\n"
        "• চ্যানেল জয়েন না করে বট ব্যবহার করা যাবে না\n"
        "• অ্যাপ খুলতে নিচের বাটন ব্যবহার করুন"
    )
    await message.answer(help_text, parse_mode=ParseMode.MARKDOWN)

# --- ADMIN PANEL ---
@router.message(Command("admin"))
async def admin_panel(message: Message):
    """Show admin panel"""
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ এই কমান্ড ব্যবহারের অনুমতি আপনার নাই!")
        return
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👥 ইউজার লিস্ট", callback_data="admin_users")],
            [InlineKeyboardButton(text="📊 পরিসংখ্যান", callback_data="admin_stats")],
            [InlineKeyboardButton(text="📢 ব্রডকাস্ট", callback_data="admin_broadcast")],
            [InlineKeyboardButton(text="📁 ফাইল ব্রডকাস্ট", callback_data="admin_broadcast_file")],
            [InlineKeyboardButton(text="🔄 রিফ্রেশ", callback_data="admin_refresh")]
        ]
    )
    
    await message.answer("🔐 *অ্যাডমিন প্যানেল*\n\nনিচের অপশনগুলো থেকে নির্বাচন করুন:", 
                        parse_mode=ParseMode.MARKDOWN, 
                        reply_markup=keyboard)

# --- ADMIN CALLBACKS ---
@router.callback_query(lambda c: c.data.startswith("admin_"))
async def admin_callback(callback: CallbackQuery):
    """Handle admin callback queries"""
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ অনুমতি নাই!", show_alert=True)
        return
    
    action = callback.data.split("_")[1]
    
    if action == "users":
        users = get_all_users()
        if not users:
            await callback.message.edit_text("📭 এখনো কোনো ইউজার নেই।")
            return
        
        total = len(users)
        text = f"👥 *মোট ইউজার: {total}*\n\n"
        for i, user in enumerate(users[:20], 1):
            telegram_id, username, first_name, joined_at = user
            username_display = f"@{username}" if username else "N/A"
            text += f"{i}. {first_name} {username_display} (ID: {telegram_id})\n   📅 {joined_at[:10]}\n"
        
        if total > 20:
            text += f"\n... এবং আরও {total - 20} জন ইউজার আছে।"
        
        await callback.message.edit_text(text, parse_mode=ParseMode.MARKDOWN)
        await callback.answer()
        
    elif action == "stats":
        total_users = get_total_users()
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users WHERE DATE(joined_at) = DATE('now') AND is_blocked = 0")
        today_users = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM broadcasts")
        broadcast_count = cursor.fetchone()[0]
        conn.close()
        
        stats_text = (
            f"📊 *বট পরিসংখ্যান*\n\n"
            f"👥 মোট ইউজার: {total_users}\n"
            f"📈 আজকের ইউজার: {today_users}\n"
            f"📢 মোট ব্রডকাস্ট: {broadcast_count}\n"
            f"🟢 বট স্ট্যাটাস: চালু ✅\n"
            f"📅 তারিখ: {datetime.now().strftime('%d-%m-%Y %H:%M')}"
        )
        
        await callback.message.edit_text(stats_text, parse_mode=ParseMode.MARKDOWN)
        await callback.answer()
        
    elif action == "broadcast":
        await callback.message.edit_text(
            "📢 *ব্রডকাস্ট*\n\n"
            "ব্রডকাস্ট করার জন্য নিচের কমান্ড ব্যবহার করুন:\n"
            "`/broadcast আপনার মেসেজ`\n\n"
            "উদাহরণ: `/broadcast সবাইকে সালাম!`",
            parse_mode=ParseMode.MARKDOWN
        )
        await callback.answer()
        
    elif action == "broadcast_file":
        await callback.message.edit_text(
            "📁 *ফাইল ব্রডকাস্ট*\n\n"
            "ফাইল ব্রডকাস্ট করার জন্য:\n"
            "1. `/broadcast_file` কমান্ড দিন\n"
            "2. তারপর ফাইল আপলোড করুন\n\n"
            "সাপোর্টেড ফাইল: ছবি, ভিডিও, ডকুমেন্ট, অডিও"
        )
        await callback.answer()
        
    elif action == "refresh":
        await callback.message.edit_text("🔄 রিফ্রেশ করা হচ্ছে...")
        await asyncio.sleep(1)
        await admin_panel(callback.message)
        await callback.answer()

# --- USERS COMMAND ---
@router.message(Command("users"))
async def list_users(message: Message):
    """List all users (admin only)"""
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ অনুমতি নাই!")
        return
    
    users = get_all_users()
    if not users:
        await message.answer("📭 কোনো ইউজার নেই।")
        return
    
    total = len(users)
    text = f"👥 *মোট ইউজার: {total}*\n\n"
    for i, user in enumerate(users[:30], 1):
        telegram_id, username, first_name, joined_at = user
        username_display = f"@{username}" if username else "N/A"
        text += f"{i}. {first_name} {username_display} (ID: {telegram_id})\n"
    
    if total > 30:
        text += f"\n... এবং আরও {total - 30} জন ইউজার আছে।"
    
    if len(text) > 4000:
        with open("users.txt", "w", encoding="utf-8") as f:
            f.write(text)
        await message.answer_document(FSInputFile("users.txt"), caption="📋 সম্পূর্ণ ইউজার লিস্ট")
        os.remove("users.txt")
    else:
        await message.answer(text, parse_mode=ParseMode.MARKDOWN)

# --- STATS COMMAND ---
@router.message(Command("stats"))
async def show_stats(message: Message):
    """Show bot statistics (admin only)"""
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ অনুমতি নাই!")
        return
    
    total_users = get_total_users()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM users WHERE DATE(joined_at) = DATE('now') AND is_blocked = 0")
    today_users = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM broadcasts")
    broadcast_count = cursor.fetchone()[0]
    conn.close()
    
    stats_text = (
        f"📊 *বট পরিসংখ্যান*\n\n"
        f"👥 মোট ইউজার: {total_users}\n"
        f"📈 আজকের ইউজার: {today_users}\n"
        f"📢 মোট ব্রডকাস্ট: {broadcast_count}\n"
        f"🟢 বট স্ট্যাটাস: চালু ✅\n"
        f"📅 {datetime.now().strftime('%d-%m-%Y %H:%M')}"
    )
    
    await message.answer(stats_text, parse_mode=ParseMode.MARKDOWN)

# --- BROADCAST COMMAND ---
@router.message(Command("broadcast"))
async def broadcast_start(message: Message, state: FSMContext):
    """Start broadcast process (admin only)"""
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ অনুমতি নাই!")
        return
    
    broadcast_text = message.text.replace("/broadcast", "").strip()
    if not broadcast_text:
        await message.answer(
            "❌ মেসেজ দিন!\n"
            "উদাহরণ: `/broadcast আপনার মেসেজ`"
        )
        return
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ হ্যাঁ, পাঠান", callback_data="confirm_broadcast"),
                InlineKeyboardButton(text="❌ বাতিল", callback_data="cancel_broadcast")
            ]
        ]
    )
    
    await state.update_data(broadcast_text=broadcast_text)
    await message.answer(
        f"📢 *ব্রডকাস্ট কনফার্মেশন*\n\n"
        f"মেসেজ:\n{broadcast_text[:200]}{'...' if len(broadcast_text) > 200 else ''}\n\n"
        f"👥 মোট প্রাপক: {get_total_users()} জন\n\n"
        f"পাঠাতে চান?",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=keyboard
    )

# --- BROADCAST FILE COMMAND ---
@router.message(Command("broadcast_file"))
async def broadcast_file_start(message: Message, state: FSMContext):
    """Start file broadcast (admin only)"""
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ অনুমতি নাই!")
        return
    
    await state.set_state(BroadcastState.waiting_for_message)
    await message.answer(
        "📁 *ফাইল ব্রডকাস্ট*\n\n"
        "দয়া করে যে ফাইলটি ব্রডকাস্ট করতে চান সেটি আপলোড করুন।\n"
        "সাপোর্টেড: ছবি, ভিডিও, ডকুমেন্ট, অডিও\n\n"
        "❌ বাতিল করতে /cancel দিন",
        parse_mode=ParseMode.MARKDOWN
    )

@router.message(BroadcastState.waiting_for_message)
async def process_broadcast_file(message: Message, state: FSMContext):
    """Process file broadcast"""
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ অনুমতি নাই!")
        await state.clear()
        return
    
    if not (message.photo or message.video or message.document or message.audio):
        await message.answer("❌ দয়া করে একটি ফাইল আপলোড করুন!")
        return
    
    file_type = "ফাইল"
    file_id = None
    
    if message.photo:
        file_id = message.photo[-1].file_id
        file_type = "ছবি"
    elif message.video:
        file_id = message.video.file_id
        file_type = "ভিডিও"
    elif message.document:
        file_id = message.document.file_id
        file_type = "ডকুমেন্ট"
    elif message.audio:
        file_id = message.audio.file_id
        file_type = "অডিও"
    
    if not file_id:
        await message.answer("❌ ফাইল আইডি পাওয়া যায়নি!")
        await state.clear()
        return
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ হ্যাঁ, পাঠান", callback_data="confirm_file_broadcast"),
                InlineKeyboardButton(text="❌ বাতিল", callback_data="cancel_broadcast")
            ]
        ]
    )
    
    await state.update_data(file_id=file_id, file_type=file_type)
    await message.answer(
        f"📢 *ফাইল ব্রডকাস্ট কনফার্মেশন*\n\n"
        f"ফাইল টাইপ: {file_type}\n"
        f"👥 মোট প্রাপক: {get_total_users()} জন\n\n"
        f"পাঠাতে চান?",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=keyboard
    )

# --- BROADCAST CALLBACKS ---
@router.callback_query(lambda c: c.data in ["confirm_broadcast", "confirm_file_broadcast"])
async def confirm_broadcast(callback: CallbackQuery, state: FSMContext):
    """Confirm and send broadcast"""
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ অনুমতি নাই!")
        return
    
    data = await state.get_data()
    await callback.message.edit_text("⏳ ব্রডকাস্ট শুরু হচ্ছে...")
    
    users = get_all_users()
    total = len(users)
    success = 0
    failed = 0
    
    for user in users:
        telegram_id, username, first_name, joined_at = user
        try:
            if "file_id" in data:
                if data.get("file_type") == "ছবি":
                    await callback.bot.send_photo(telegram_id, data["file_id"])
                elif data.get("file_type") == "ভিডিও":
                    await callback.bot.send_video(telegram_id, data["file_id"])
                elif data.get("file_type") == "ডকুমেন্ট":
                    await callback.bot.send_document(telegram_id, data["file_id"])
                elif data.get("file_type") == "অডিও":
                    await callback.bot.send_audio(telegram_id, data["file_id"])
            else:
                await callback.bot.send_message(telegram_id, data["broadcast_text"])
            success += 1
        except Exception as e:
            logging.error(f"Broadcast failed to {telegram_id}: {e}")
            failed += 1
        
        await asyncio.sleep(0.05)
    
    if "broadcast_text" in data:
        save_broadcast(callback.from_user.id, data["broadcast_text"], success, failed)
    else:
        save_broadcast(callback.from_user.id, f"[FILE] {data.get('file_type')}", success, failed)
    
    await callback.message.edit_text(
        f"✅ *ব্রডকাস্ট সম্পন্ন!*\n\n"
        f"📤 সফল: {success}\n"
        f"📤 ব্যর্থ: {failed}\n"
        f"👥 মোট: {total}\n"
        f"📅 {datetime.now().strftime('%d-%m-%Y %H:%M')}",
        parse_mode=ParseMode.MARKDOWN
    )
    
    await state.clear()
    await callback.answer()

@router.callback_query(lambda c: c.data == "cancel_broadcast")
async def cancel_broadcast(callback: CallbackQuery, state: FSMContext):
    """Cancel broadcast"""
    await state.clear()
    await callback.message.edit_text("❌ ব্রডকাস্ট বাতিল করা হয়েছে।")
    await callback.answer()

# --- CANCEL COMMAND ---
@router.message(Command("cancel"))
async def cancel_command(message: Message, state: FSMContext):
    """Cancel current operation"""
    current_state = await state.get_state()
    if current_state:
        await state.clear()
        await message.answer("❌ অপারেশন বাতিল করা হয়েছে।")
    else:
        await message.answer("কোনো অপারেশন চলছে না।")

# ==================== MAIN ====================
async def main():
    """Main function to start the bot"""
    init_db()
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    logger = logging.getLogger(__name__)
    
    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML)
    )
    
    storage = MemoryStorage()
    dp = Dispatcher(storage=storage)
    dp.include_router(router)
    
    # Add force join middleware
    dp.message.middleware(ForceJoinMiddleware(bot))
    dp.callback_query.middleware(ForceJoinMiddleware(bot))
    
    logger.info("🚀 Bot is starting...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Bot stopped by user")
    except Exception as e:
        logging.error(f"Fatal error: {e}")
