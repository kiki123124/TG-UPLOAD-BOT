#!/usr/bin/env python3
import os
import glob
import logging
import hashlib
import time
import asyncio
import re
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Chat
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, ConversationHandler, MessageHandler, filters
from telegram.constants import ParseMode, ChatType
from telegram.error import TelegramError, TimedOut, NetworkError, RetryAfter
import sys
import signal
import atexit
import fcntl
import json
import subprocess
import requests

# 配置日志
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# 状态定义
SELECTING_CATEGORY, SELECTING_COUNT, SELECTING_BOOK, SELECTING_CHANNEL = range(4)
# 新的对话状态
SELECTING_CATEGORY_FROM, INPUT_SEARCH_KEYWORD, SELECTING_START_BOOK = 100, 101, 102
# 新增对话状态
CHECK_CHANNEL, CHECK_CATEGORY, CHECK_CONFIRM = 200, 201, 202

# 配置
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
DATA_DIR = "processed_books_rich_副本"
EPUB_DIR = "new_categorized_books_副本"  # 新的epub文件目录

# 重试配置
MAX_RETRIES = 5
INITIAL_RETRY_DELAY = 1  # 初始重试延迟（秒）
MAX_RETRY_DELAY = 60  # 最大重试延迟（秒）
RETRY_FIRST_FILE_DELAY = 5  # 重试后第一个文件的延迟（秒）

# 书籍映射字典，用于存储短ID到文件名的映射
book_id_map = {}

# 重试状态跟踪
retry_status = {
    "is_after_retry": False,
    "retry_count": 0,
    "just_retried": False  # 新增：标记刚刚流控过
}

# 记录管理员ID（首次交互用户）
ADMIN_USER_ID = None

STOP_FLAG = False

# 单实例锁文件机制
LOCK_FILE = '/tmp/epub_uploader_bot.lock'
lock_fp = None

def acquire_lock():
    global lock_fp
    lock_fp = open(LOCK_FILE, 'w')
    try:
        fcntl.flock(lock_fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_fp.write(str(os.getpid()))
        lock_fp.flush()
    except IOError:
        print('已有一个bot实例在运行，请勿重复启动！')
        sys.exit(1)

def release_lock():
    global lock_fp
    try:
        if lock_fp:
            lock_fp.close()
        if os.path.exists(LOCK_FILE):
            os.remove(LOCK_FILE)
    except Exception:
        pass

acquire_lock()
atexit.register(release_lock)

# 辅助函数
def get_categories():
    """获取所有分类"""
    return sorted([d for d in os.listdir(DATA_DIR) if os.path.isdir(os.path.join(DATA_DIR, d))])

def get_books_in_category(category):
    """获取分类下的所有书籍"""
    books_path = os.path.join(DATA_DIR, category, "*.txt")
    books = glob.glob(books_path)
    return [os.path.basename(book) for book in books]

def get_short_id(book_name):
    """生成书籍的短ID"""
    # 使用哈希函数生成短ID
    hash_obj = hashlib.md5(book_name.encode())
    short_id = hash_obj.hexdigest()[:8]  # 取前8位作为短ID
    
    # 存储映射关系
    book_id_map[short_id] = book_name
    
    return short_id

def find_epub_file(book_name, category):
    """查找对应的epub文件"""
    # 移除.txt后缀
    book_name = book_name[:-4] if book_name.endswith('.txt') else book_name
    
    # 在原始epub目录中查找
    epub_category_path = os.path.join(EPUB_DIR, category)
    if os.path.exists(epub_category_path):
        epub_path = os.path.join(epub_category_path, f"{book_name}.epub")
        if os.path.exists(epub_path):
            return epub_path
    
    # 如果在原始目录中找不到，尝试在所有分类中查找
    for cat in os.listdir(EPUB_DIR):
        if os.path.isdir(os.path.join(EPUB_DIR, cat)):
            epub_path = os.path.join(EPUB_DIR, cat, f"{book_name}.epub")
            if os.path.exists(epub_path):
                return epub_path
    
    return None

def read_book_info(category, book_name):
    """读取书籍信息"""
    file_path = os.path.join(DATA_DIR, category, book_name)
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 解析内容
        title = ""
        author = ""
        intro = ""
        
        lines = content.split('\n')
        for i, line in enumerate(lines):
            if line.startswith("标题："):
                title = line.replace("标题：", "").strip()
            elif line.startswith("作者："):
                author = line.replace("作者：", "").strip()
            elif line.startswith("简介："):
                # 简介可能有多行
                intro_lines = []
                j = i + 1
                while j < len(lines) and not lines[j].startswith("标题：") and not lines[j].startswith("作者："):
                    if lines[j].strip():  # 只添加非空行
                        intro_lines.append(lines[j])
                    j += 1
                intro = "\n".join(intro_lines)
        
        return {
            "title": title or book_name.replace(".txt", ""),
            "author": author or "未知作者",
            "intro": intro or "暂无简介"
        }
    except Exception as e:
        logger.error(f"读取书籍信息出错: {e}")
        return {
            "title": book_name.replace(".txt", ""),
            "author": "未知作者",
            "intro": "暂无简介"
        }

def process_text_for_telegram(text):
    """处理文本，确保省略号正确显示，同时转义其他特殊字符"""
    if not text:
        return ""
    
    # 完全禁用Markdown解析，直接返回原始文本
    # 这样省略号和其他特殊字符都会原样显示
    return text

# 添加重试装饰器函数
async def with_retry(func, *args, **kwargs):
    """带有重试机制的函数装饰器，增加超时保护和流控后延迟标记"""
    global retry_status, STOP_FLAG
    retry_count = 0
    delay = INITIAL_RETRY_DELAY
    chat_id = kwargs.get('chat_id')
    context = kwargs.pop('context', None)
    while True:
        if STOP_FLAG:
            return
        try:
            result = await asyncio.wait_for(func(*args, **kwargs), timeout=60)
            if retry_count > 0:
                retry_status["is_after_retry"] = True
                retry_status["retry_count"] = retry_count
            return result
        except asyncio.TimeoutError:
            retry_count += 1
            logger.error(f"操作超时，{delay}秒后重试 (尝试 {retry_count}/{MAX_RETRIES})")
            if retry_count > MAX_RETRIES:
                logger.error(f"达到最大重试次数 ({MAX_RETRIES})，操作失败: 超时")
                raise
            await asyncio.sleep(delay)
        except RetryAfter as e:
            retry_count += 1
            wait_time = int(e.retry_after) + 1
            if retry_count > MAX_RETRIES:
                logger.error(f"达到最大重试次数 ({MAX_RETRIES})，操作失败: {e}")
                raise
            wait_message = f"Telegram流量限制，需等待 {wait_time} 秒后才能发送，正在等待... (尝试 {retry_count}/{MAX_RETRIES})"
            logger.info(wait_message)
            sent_msg = None
            if chat_id and context and hasattr(context, 'bot'):
                try:
                    sent_msg = await context.bot.send_message(
                        chat_id=chat_id,
                        text=wait_message
                    )
                except Exception as notify_error:
                    logger.error(f"通知用户等待时间失败: {notify_error}")
            # 实时倒计时通知，edit失败则静默等待
            for left in range(wait_time, 0, -5):
                await asyncio.sleep(5 if left > 5 else left)
                left_new = left - 5 if left > 5 else 0
                if sent_msg and chat_id and context and hasattr(context, 'bot') and left_new > 0:
                    try:
                        await context.bot.edit_message_text(
                            chat_id=chat_id,
                            message_id=sent_msg.message_id,
                            text=f"Telegram流量限制，剩余 {left_new} 秒... (尝试 {retry_count}/{MAX_RETRIES})"
                        )
                    except Exception as edit_error:
                        logger.error(f"更新等待时间失败: {edit_error}，本次edit跳过")
                        break  # edit失败直接跳出倒计时，避免二次流控
            retry_status["just_retried"] = True
        except (NetworkError, TimedOut) as e:
            retry_count += 1
            if retry_count > MAX_RETRIES:
                logger.error(f"达到最大重试次数 ({MAX_RETRIES})，操作失败: {e}")
                raise
            wait_time = min(delay * (2 ** (retry_count - 1)), MAX_RETRY_DELAY)
            logger.info(f"网络错误，{wait_time} 秒后重试 (尝试 {retry_count}/{MAX_RETRIES}): {e}")
            await asyncio.sleep(wait_time)
        except TelegramError as e:
            retry_count += 1
            if retry_count > MAX_RETRIES:
                logger.error(f"达到最大重试次数 ({MAX_RETRIES})，操作失败: {e}")
                raise
            logger.info(f"Telegram错误，{delay} 秒后重试 (尝试 {retry_count}/{MAX_RETRIES}): {e}")
            await asyncio.sleep(delay)
        except Exception as e:
            logger.error(f"未预期的错误，不重试: {e}")
            raise

# 新增：主菜单按钮回调
async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📤 上传书籍", callback_data='menu_upload')],
        [InlineKeyboardButton("📤 指定起点上传", callback_data='menu_uploadfrom')],
        [InlineKeyboardButton("🔄 同步频道", callback_data='menu_fetch')],
        [InlineKeyboardButton("🛠️ 检查并补发", callback_data='menu_checkfill')],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.message:
        await update.message.reply_text('请选择操作：', reply_markup=reply_markup)
    elif update.callback_query:
        await update.callback_query.edit_message_text('请选择操作：', reply_markup=reply_markup)

# 修改 /start 命令
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await with_retry(update.message.reply_text,
        f"你好 {user.first_name}！\n\n"
        f"这是一个电子书上传机器人，可以帮助你将电子书上传到Telegram频道。\n\n"
        f"📤 上传书籍：将本地电子书上传到频道\n"
        f"🔄 同步频道：同步频道最新书籍列表（fetch）\n"
        f"🛠️ 检查并补发：自动检测并补发频道缺失书籍\n\n"
        f"请点击下方按钮操作。",
        context=context
    )
    await main_menu(update, context)
    return ConversationHandler.END

# 新增：按钮回调处理
async def menu_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == 'menu_upload':
        await upload(update, context)
    elif query.data == 'menu_uploadfrom':
        await upload_from(update, context)
    elif query.data == 'menu_fetch':
        await query.edit_message_text("正在同步频道，请稍候...（大约1-2分钟）")
        try:
            resp = requests.post('http://127.0.0.1:5000/sync', timeout=3)
            if resp.status_code == 200:
                await query.message.reply_text("频道同步已启动，稍后请刷新页面或重新点击补发/上传。")
            else:
                await query.message.reply_text(f"同步请求失败：{resp.text}")
        except Exception as e:
            await query.message.reply_text(f"同步出错：{e}")
        await main_menu(update, context)
    elif query.data == 'menu_checkfill':
        # 触发 /check_and_fill 流程
        await check_and_fill_entry(update, context)
    else:
        await query.message.reply_text("未知操作，请重试。")
        await main_menu(update, context)

async def upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理/upload命令"""
    # 首先请求输入频道ID
    msg = update.message or (update.callback_query.message if hasattr(update, 'callback_query') and update.callback_query else None)
    if not msg:
        return
    logging.info(f"[upload] reply_text chat_id={msg.chat_id}, text=请输入要上传到的频道ID或用户名")
    await with_retry(msg.reply_text,
        "请输入要上传到的频道ID或用户名（例如：@your_channel 或 -1001234567890）：",
        context=context
    )
    return SELECTING_CHANNEL

def normalize_channel_id(channel_id):
    channel_id = channel_id.strip()
    if channel_id.startswith("https://t.me/") or channel_id.startswith("http://t.me/"):
        channel_id = "@" + channel_id.split("/")[-1]
    return channel_id

async def channel_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    channel_id = normalize_channel_id(update.message.text)
    context.user_data["channel_id"] = channel_id
    
    # 继续选择分类
    categories = get_categories()
    
    # 创建分类键盘
    keyboard = []
    for i in range(0, len(categories), 2):
        row = []
        row.append(InlineKeyboardButton(categories[i], callback_data=f"cat_{i}"))
        if i + 1 < len(categories):
            row.append(InlineKeyboardButton(categories[i+1], callback_data=f"cat_{i+1}"))
        keyboard.append(row)
    
    # 存储分类映射
    context.user_data["categories"] = categories
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await with_retry(update.message.reply_text, 
        f"将上传到频道: {channel_id}\n请选择要上传的电子书分类：", 
        reply_markup=reply_markup,
        context=context
    )
    
    return SELECTING_CATEGORY

async def category_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理分类选择"""
    query = update.callback_query
    await query.answer()
    
    cat_index = int(query.data.replace("cat_", ""))
    category = context.user_data["categories"][cat_index]
    context.user_data["category"] = category
    
    # 创建数量选择键盘
    keyboard = [
        [
            InlineKeyboardButton("1本", callback_data="count_1"),
            InlineKeyboardButton("3本", callback_data="count_3"),
            InlineKeyboardButton("5本", callback_data="count_5")
        ],
        [
            InlineKeyboardButton("10本", callback_data="count_10"),
            InlineKeyboardButton("全部", callback_data="count_all")
        ],
        [InlineKeyboardButton("返回分类选择", callback_data="back_to_categories")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    await with_retry(query.edit_message_text,
        f"已选择分类: {category}\n请选择要上传的电子书数量：", 
        reply_markup=reply_markup,
        context=context
    )
    
    return SELECTING_COUNT

async def count_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理数量选择"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "back_to_categories":
        categories = context.user_data["categories"]
        keyboard = []
        for i in range(0, len(categories), 2):
            row = []
            row.append(InlineKeyboardButton(categories[i], callback_data=f"cat_{i}"))
            if i + 1 < len(categories):
                row.append(InlineKeyboardButton(categories[i+1], callback_data=f"cat_{i+1}"))
            keyboard.append(row)
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await with_retry(query.edit_message_text,
            f"将上传到频道: {context.user_data['channel_id']}\n请选择要上传的电子书分类：", 
            reply_markup=reply_markup,
            context=context
        )
        return SELECTING_CATEGORY
    
    count = query.data.replace("count_", "")
    category = context.user_data["category"]
    context.user_data["count"] = count
    
    books = get_books_in_category(category)
    
    if count == "all":
        # 直接上传所有书籍
        await with_retry(query.edit_message_text,
            f"正在上传 {category} 分类下的所有电子书...",
            context=context
        )
        
        success_count = 0
        skip_count = 0
        
        # 重置重试状态
        global retry_status
        retry_status = {
            "is_after_retry": False,
            "retry_count": 0
        }
        
        for book in books:
            result = await upload_book(update, context, category, book)
            if result:
                success_count += 1
            else:
                skip_count += 1
        
        await with_retry(query.message.reply_text,
            f"已完成 {category} 分类下电子书的上传！\n成功: {success_count} 本\n跳过: {skip_count} 本",
            context=context
        )
        return ConversationHandler.END
    else:
        # 显示书籍选择
        count = int(count)
        context.user_data["remaining_count"] = count
        context.user_data["selected_books"] = []
        
        # 创建书籍选择键盘
        keyboard = []
        book_id_map.clear()  # 清空之前的映射
        
        for i in range(0, min(10, len(books)), 1):
            book_name = books[i].replace(".txt", "")
            short_id = get_short_id(books[i])
            
            if len(book_name) > 30:
                book_name = book_name[:27] + "..."
            keyboard.append([InlineKeyboardButton(book_name, callback_data=f"book_{short_id}")])
        
        keyboard.append([InlineKeyboardButton("返回数量选择", callback_data="back_to_count")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await with_retry(query.edit_message_text,
            f"已选择分类: {category}, 数量: {count}本\n"
            f"请选择要上传的电子书（还需选择{count}本）：", 
            reply_markup=reply_markup,
            context=context
        )
        
        return SELECTING_BOOK

async def book_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理书籍选择"""
    query = update.callback_query
    await query.answer()
    
    if query.data == "back_to_count":
        category = context.user_data["category"]
        keyboard = [
            [
                InlineKeyboardButton("1本", callback_data="count_1"),
                InlineKeyboardButton("3本", callback_data="count_3"),
                InlineKeyboardButton("5本", callback_data="count_5")
            ],
            [
                InlineKeyboardButton("10本", callback_data="count_10"),
                InlineKeyboardButton("全部", callback_data="count_all")
            ],
            [InlineKeyboardButton("返回分类选择", callback_data="back_to_categories")]
        ]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await with_retry(query.edit_message_text,
            f"已选择分类: {category}\n请选择要上传的电子书数量：", 
            reply_markup=reply_markup,
            context=context
        )
        return SELECTING_COUNT
    
    short_id = query.data.replace("book_", "")
    book = book_id_map.get(short_id)
    
    if not book:
        await with_retry(query.message.reply_text, 
            "无法识别选中的书籍，请重试。",
            context=context
        )
        return SELECTING_BOOK
    
    category = context.user_data["category"]
    
    # 重置重试状态
    global retry_status
    retry_status = {
        "is_after_retry": False,
        "retry_count": 0
    }
    
    # 上传选中的书籍
    result = await upload_book(update, context, category, book)
    
    # 更新剩余数量
    if result:  # 只有成功上传才计数
        context.user_data["remaining_count"] -= 1
    context.user_data["selected_books"].append(book)
    
    if context.user_data["remaining_count"] <= 0:
        await with_retry(query.message.reply_text, 
            "已完成所有选定电子书的上传！",
            context=context
        )
        return ConversationHandler.END
    else:
        # 更新书籍选择键盘，排除已选择的书籍
        books = get_books_in_category(category)
        books = [b for b in books if b not in context.user_data["selected_books"]]
        
        keyboard = []
        book_id_map.clear()  # 清空之前的映射
        
        for i in range(0, min(10, len(books)), 1):
            book_name = books[i].replace(".txt", "")
            short_id = get_short_id(books[i])
            
            if len(book_name) > 30:
                book_name = book_name[:27] + "..."
            keyboard.append([InlineKeyboardButton(book_name, callback_data=f"book_{short_id}")])
        
        keyboard.append([InlineKeyboardButton("返回数量选择", callback_data="back_to_count")])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await with_retry(query.edit_message_text,
            f"已选择分类: {category}, 还需选择{context.user_data['remaining_count']}本：", 
            reply_markup=reply_markup,
            context=context
        )
        
        return SELECTING_BOOK

# 工具函数：记录已发书名（只用文件名）
def record_sent_title(title):
    sent_titles_path = 'sent_titles.json'
    try:
        if os.path.exists(sent_titles_path):
            with open(sent_titles_path, 'r', encoding='utf-8') as f:
                sent_titles = set(json.load(f))
        else:
            sent_titles = set()
        sent_titles.add(title.strip())
        with open(sent_titles_path, 'w', encoding='utf-8') as f:
            json.dump(list(sent_titles), f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f'写入 sent_titles.json 失败: {e}')

# 修改 upload_book 发送文档和记录逻辑
async def upload_book(update: Update, context: ContextTypes.DEFAULT_TYPE, category, book_name, force_channel_id=None):
    """上传单本电子书，如果找不到epub文件则跳过。异常时不影响后续。"""
    global retry_status, STOP_FLAG
    if STOP_FLAG:
        return
    try:
        if STOP_FLAG:
            return
        # 检查是否刚刚流控过，延迟更久
        if retry_status.get("just_retried"):
            logger.info("检测到刚刚流控，延迟15秒以避免再次触发")
            await with_retry(context.bot.send_message,
                chat_id=update.effective_chat.id,
                text="检测到刚刚流控，正在等待15秒以确保稳定...",
                context=context
            )
            await asyncio.sleep(15)
            retry_status["just_retried"] = False
        # 读取书籍信息
        book_info = read_book_info(category, book_name)
        epub_path = find_epub_file(book_name, category)
        # 新增：判断epub文件是否存在且非空
        if not epub_path or not os.path.exists(epub_path) or os.path.getsize(epub_path) == 0:
            await with_retry(context.bot.send_message,
                chat_id=update.effective_chat.id,
                text=f"跳过 {book_info['title']}：epub文件不存在或为空",
                context=context
            )
            logger.warning(f"epub文件不存在或为空，跳过: {book_name}")
            return False
        file_title = os.path.splitext(os.path.basename(epub_path))[0]
        formatted_category = category.replace("-", "_")
        processed_title = process_text_for_telegram(book_info['title'])
        processed_intro = process_text_for_telegram(book_info['intro'])
        message = (
            f"标题：{processed_title}\n"
            f"类型：#{formatted_category}\n"
            f"简介：{processed_intro}"
        )
        # 新增：限制caption长度不超过1024
        MAX_CAPTION_LEN = 1024
        if len(message) > MAX_CAPTION_LEN:
            message = message[:MAX_CAPTION_LEN - 3] + "..."
        # 关键：补发时强制用频道ID
        channel_id = force_channel_id if force_channel_id else context.user_data.get("channel_id", update.effective_chat.id)
        if retry_status["is_after_retry"]:
            logger.info(f"检测到重试后的首次上传，添加{RETRY_FIRST_FILE_DELAY}秒延迟")
            await with_retry(context.bot.send_message,
                chat_id=update.effective_chat.id,
                text=f"检测到重试后的首次上传，正在等待{RETRY_FIRST_FILE_DELAY}秒以确保稳定...",
                context=context
            )
            await asyncio.sleep(RETRY_FIRST_FILE_DELAY)
            retry_status["is_after_retry"] = False
        max_upload_attempts = 3
        upload_attempt = 0
        upload_success = False
        while not upload_success and upload_attempt < max_upload_attempts:
            if STOP_FLAG:
                return
            try:
                upload_attempt += 1
                # 上传操作加超时保护
                await with_retry(context.bot.send_document,
                    chat_id=channel_id,
                    document=open(epub_path, 'rb'),
                    caption=message,
                    parse_mode=None,
                    context=context
                )
                # 新增：记录已发书名（只用文件名）
                record_sent_title(file_title)
                upload_success = True
            except Exception as e:
                if upload_attempt < max_upload_attempts:
                    logger.warning(f"上传失败，尝试第{upload_attempt+1}次: {e}")
                    await asyncio.sleep(2)
                else:
                    raise
        if STOP_FLAG:
            return
        await with_retry(context.bot.send_message,
            chat_id=update.effective_chat.id,
            text=f"已上传: {book_info['title']} 到频道 {channel_id}",
            context=context
        )
        logger.info(f"已上传: {book_info['title']} 到频道 {channel_id}")
        return True
    except Exception as e:
        logger.error(f"上传书籍出错: {e}")
        try:
            if STOP_FLAG:
                return
            await with_retry(context.bot.send_message,
                chat_id=update.effective_chat.id,
                text=f"上传 {book_name} 时出错: {str(e)}",
                context=context
            )
        except Exception as ee:
            logger.error(f"通知用户上传出错时再次出错: {ee}")
        return False

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """取消操作"""
    await with_retry(update.message.reply_text, 
        "操作已取消。",
        context=context
    )
    return ConversationHandler.END

# /upload_from 命令处理
async def upload_from(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message or (update.callback_query.message if hasattr(update, 'callback_query') and update.callback_query else None)
    if not msg:
        return
    logging.info(f"[upload_from] reply_text chat_id={msg.chat_id}, text=请输入要上传到的频道ID或用户名")
    await with_retry(msg.reply_text,
        "请输入要上传到的频道ID或用户名（例如：@your_channel 或 -1001234567890）：",
        context=context
    )
    return 'UPLOAD_FROM_CHANNEL_INPUT'

async def upload_from_channel_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    channel_id = normalize_channel_id(update.message.text)
    context.user_data["channel_id"] = channel_id
    categories = get_categories()
    keyboard = []
    for i in range(0, len(categories), 2):
        row = []
        row.append(InlineKeyboardButton(categories[i], callback_data=f"catfrom_{i}"))
        if i + 1 < len(categories):
            row.append(InlineKeyboardButton(categories[i+1], callback_data=f"catfrom_{i+1}"))
        keyboard.append(row)
    context.user_data["categories_from"] = categories
    reply_markup = InlineKeyboardMarkup(keyboard)
    await with_retry(update.message.reply_text,
        "请选择要上传的电子书分类：",
        reply_markup=reply_markup,
        context=context
    )
    return SELECTING_CATEGORY_FROM

async def category_from_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cat_index = int(query.data.replace("catfrom_", ""))
    category = context.user_data["categories_from"][cat_index]
    context.user_data["category_from"] = category
    await with_retry(query.edit_message_text,
        f"已选择分类: {category}\n请输入书名关键字进行搜索：",
        context=context
    )
    return INPUT_SEARCH_KEYWORD

async def input_search_keyword(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyword = update.message.text.strip()
    category = context.user_data["category_from"]
    books = get_books_in_category(category)
    books_no_ext = [b[:-4] if b.endswith('.txt') else b for b in books]
    # 模糊匹配
    matched = [b for b in books_no_ext if keyword in b]
    if not matched:
        await with_retry(update.message.reply_text,
            f"未找到包含关键字'{keyword}'的书名，请重新输入：",
            context=context
        )
        return INPUT_SEARCH_KEYWORD
    # 最多显示10本
    matched = matched[:10]
    keyboard = [[InlineKeyboardButton(b, callback_data=f"startbook_{books_no_ext.index(b)}")] for b in matched]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await with_retry(update.message.reply_text,
        f"找到以下书名，请点击选择起始书：",
        reply_markup=reply_markup,
        context=context
    )
    # 保存books_no_ext到user_data，后续用index定位
    context.user_data["books_no_ext"] = books_no_ext
    context.user_data["books"] = books
    return SELECTING_START_BOOK

async def select_start_book(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    idx = int(query.data.replace("startbook_", ""))
    books_no_ext = context.user_data["books_no_ext"]
    books = context.user_data["books"]
    start_book = books_no_ext[idx]
    category = context.user_data["category_from"]
    # 后续所有书
    books_to_upload = books[idx+1:]
    if not books_to_upload:
        await with_retry(query.edit_message_text,
            f"{start_book} 已经是最后一本，无后续书籍可上传。",
            context=context
        )
        return ConversationHandler.END
    await with_retry(query.edit_message_text,
        f"将从 {start_book} 之后开始上传，共 {len(books_to_upload)} 本...",
        context=context
    )
    success_count = 0
    skip_count = 0
    global retry_status
    retry_status = {
        "is_after_retry": False,
        "retry_count": 0,
        "just_retried": False
    }
    for book in books_to_upload:
        result = await upload_book(update, context, category, book)
        if result:
            success_count += 1
        else:
            skip_count += 1
    await with_retry(context.bot.send_message,
        chat_id=update.effective_chat.id,
        text=f"已完成上传！\n成功: {success_count} 本\n跳过: {skip_count} 本",
        context=context
    )
    return ConversationHandler.END

STOP_FLAG = False

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global ADMIN_USER_ID, STOP_FLAG
    user_id = update.effective_user.id if update.effective_user else None
    logger.info(f"收到/stop指令，user_id={user_id}")
    if ADMIN_USER_ID is None:
        ADMIN_USER_ID = user_id
    if user_id != ADMIN_USER_ID:
        try:
            chat_id = update.effective_chat.id if update.effective_chat else None
            if chat_id:
                await with_retry(context.bot.send_message, chat_id=chat_id, text="无权限：只有管理员可以停止机器人。", context=context)
        except Exception as e:
            logger.error(f"stop权限拒绝时通知失败: {e}")
        return
    STOP_FLAG = True
    try:
        chat_id = update.effective_chat.id if update.effective_chat else None
        if chat_id:
            await with_retry(context.bot.send_message, chat_id=chat_id, text="机器人已停止，进程即将退出。", context=context)
    except Exception as e:
        logger.error(f"stop通知失败: {e}")
    logger.info(f"管理员{user_id}触发/stop，机器人即将退出。")
    try:
        await context.application.stop()
    except Exception as e:
        logger.error(f"application.stop()异常: {e}")
    os._exit(0)

# 新增：检查并补发缺失书籍功能
async def check_and_fill_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message or (update.callback_query.message if hasattr(update, 'callback_query') and update.callback_query else None)
    if not msg:
        return
    await with_retry(msg.reply_text, "请输入要检查的频道ID或用户名（例如：@your_channel 或 -1001234567890 或 https://t.me/xxx）：", context=context)
    return CHECK_CHANNEL

async def check_and_fill_channel_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    channel_id = normalize_channel_id(update.message.text)
    context.user_data['check_channel_id'] = channel_id
    # 展示分类选择
    categories = get_categories()
    context.user_data['check_categories'] = categories
    keyboard = []
    for i in range(0, len(categories), 2):
        row = []
        row.append(InlineKeyboardButton(categories[i], callback_data=f"checkcat_{i}"))
        if i + 1 < len(categories):
            row.append(InlineKeyboardButton(categories[i+1], callback_data=f"checkcat_{i+1}"))
        keyboard.append(row)
    reply_markup = InlineKeyboardMarkup(keyboard)
    await with_retry(update.message.reply_text, "请选择要检查的分类：", reply_markup=reply_markup, context=context)
    return CHECK_CATEGORY

def normalize_filename(name):
    """宽松化文件名：小写、去除空格、下划线、短横线、所有非字母数字字符"""
    name = name.lower()
    name = re.sub(r'[\s_\-]', '', name)
    name = re.sub(r'[^a-z0-9\u4e00-\u9fa5]', '', name)
    return name

async def check_and_fill_category_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    cat_index = int(query.data.replace("checkcat_", ""))
    category = context.user_data['check_categories'][cat_index]
    context.user_data['check_category'] = category
    # 读取 channel_titles.json
    channel_titles_path = 'channel_titles.json'
    channel_filenames = set()
    if os.path.exists(channel_titles_path):
        with open(channel_titles_path, 'r', encoding='utf-8') as f:
            channel_data = json.load(f)
            # 只取当前分类下的 filename，注意 category 的 - 和 _ 兼容
            for entry in channel_data:
                if isinstance(entry, dict):
                    entry_cat = entry.get('category', '').replace('-', '_')
                    if entry_cat == category.replace('-', '_'):
                        fn = entry.get('filename', '').strip()
                        if fn:
                            channel_filenames.add(normalize_filename(fn))
    # 获取本地分类下所有 epub 文件名
    local_titles = set()
    category_path = os.path.join("new_categorized_books_副本", category)
    for epub_path in glob.glob(os.path.join(category_path, "*.epub")):
        file_title = os.path.splitext(os.path.basename(epub_path))[0]
        local_titles.add(normalize_filename(file_title))
    missing_titles = list(local_titles - channel_filenames)
    # 反查原始文件名用于展示
    display_missing = []
    for epub_path in glob.glob(os.path.join(category_path, "*.epub")):
        file_title = os.path.splitext(os.path.basename(epub_path))[0]
        if normalize_filename(file_title) in missing_titles:
            display_missing.append(file_title)
    context.user_data['check_missing_titles'] = display_missing
    if not display_missing:
        await with_retry(query.message.reply_text, f"分类【{category}】与频道已同步，无需补发！", context=context)
        return ConversationHandler.END
    preview = '\n'.join(display_missing[:10])
    more = f"\n...共{len(display_missing)}本缺失" if len(display_missing) > 10 else ""
    keyboard = [
        [InlineKeyboardButton("补发", callback_data="checkconfirm_yes"), InlineKeyboardButton("不补发", callback_data="checkconfirm_no")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await with_retry(query.message.reply_text, f"分类【{category}】缺失 {len(display_missing)} 本：\n{preview}{more}\n是否补发？", reply_markup=reply_markup, context=context)
    return CHECK_CONFIRM

async def check_and_fill_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == "checkconfirm_no":
        await with_retry(query.message.reply_text, "操作已取消。", context=context)
        return ConversationHandler.END
    # 自动补发
    category = context.user_data['check_category']
    missing_titles = context.user_data['check_missing_titles']
    category_path = os.path.join("new_categorized_books_副本", category)
    sent = 0
    check_channel_id = context.user_data.get('check_channel_id')

    # 新增：用 get_books_in_category 获取所有 txt 文件名，做智能匹配
    txt_books = get_books_in_category(category)
    txt_books_no_ext = [b[:-4] if b.endswith('.txt') else b for b in txt_books]
    # 建立 epub名->txt名 映射
    epub_to_txt = {b[:-4] if b.endswith('.txt') else b: b for b in txt_books}

    for title in missing_titles:
        # 优先用 txt 文件名补发
        txt_name = epub_to_txt.get(title)
        if txt_name:
            result = await upload_book(update, context, category, txt_name, force_channel_id=check_channel_id)
        else:
            result = await upload_book(update, context, category, title, force_channel_id=check_channel_id)
        if result:
            sent += 1
            await with_retry(query.message.reply_text, f"已补发：{title}", context=context)
            await asyncio.sleep(1.5)
        else:
            await with_retry(query.message.reply_text, f"补发 {title} 失败", context=context)
    await with_retry(query.message.reply_text, f"分类【{category}】补发完成，共补发 {sent} 本！", context=context)
    return ConversationHandler.END

def main():
    """主函数，移除自动重启机制，/stop后能彻底退出"""
    try:
        application = Application.builder().token(TOKEN).build()
        # stop handler 必须最先注册，防止被吞掉
        application.add_handler(CommandHandler("stop", stop))
        application.add_handler(CommandHandler("start", start))
        # 新增：主菜单按钮回调
        application.add_handler(CallbackQueryHandler(menu_button_handler, pattern=r"^menu_"))
        conv_handler = ConversationHandler(
            entry_points=[CommandHandler("upload", upload), CommandHandler("upload_from", upload_from)],
            states={
                SELECTING_CHANNEL: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, channel_input)
                ],
                'UPLOAD_FROM_CHANNEL_INPUT': [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, upload_from_channel_input)
                ],
                SELECTING_CATEGORY: [
                    CallbackQueryHandler(category_selected, pattern=r"^cat_")
                ],
                SELECTING_COUNT: [
                    CallbackQueryHandler(count_selected, pattern=r"^count_|^back_to_categories$")
                ],
                SELECTING_BOOK: [
                    CallbackQueryHandler(book_selected, pattern=r"^book_|^back_to_count$")
                ],
                SELECTING_CATEGORY_FROM: [
                    CallbackQueryHandler(category_from_selected, pattern=r"^catfrom_")
                ],
                INPUT_SEARCH_KEYWORD: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, input_search_keyword)
                ],
                SELECTING_START_BOOK: [
                    CallbackQueryHandler(select_start_book, pattern=r"^startbook_")
                ],
            },
            fallbacks=[CommandHandler("cancel", cancel)],
        )
        application.add_handler(conv_handler)
        check_conv_handler = ConversationHandler(
            entry_points=[
                CommandHandler("check_and_fill", check_and_fill_entry),
                CallbackQueryHandler(check_and_fill_entry, pattern=r"^menu_checkfill$")
            ],
            states={
                CHECK_CHANNEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, check_and_fill_channel_input)],
                CHECK_CATEGORY: [CallbackQueryHandler(check_and_fill_category_selected, pattern=r"^checkcat_")],
                CHECK_CONFIRM: [CallbackQueryHandler(check_and_fill_confirm, pattern=r"^checkconfirm_")],
            },
            fallbacks=[CommandHandler("cancel", cancel)],
        )
        application.add_handler(check_conv_handler)
        application.run_polling()
    except Exception as e:
        logger.error(f"主循环异常: {e}")

if __name__ == "__main__":
    main()
