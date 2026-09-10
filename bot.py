import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime

import requests
import telebot
from telebot import types


# =========================================================
# CONFIG
# =========================================================

# توکن واقعی را اینجا دستی قرار بده
TOKEN = "8656542929:AAHnxARjFQccTmx2ffBnK1n8BAi6csaGtPI"

if not TOKEN or TOKEN == "اینجا توکن رباتتو بذار":
    raise RuntimeError("توکن ربات را در قسمت TOKEN قرار بده.")

bot = telebot.TeleBot(
    TOKEN,
    threaded=True,
    num_threads=8
)

CHANNEL_USERNAME = os.getenv("REPORT_CHANNEL", "@andksaox")
FORCED_CHANNEL = os.getenv("FORCED_CHANNEL", "@Rbnwei")

ADMIN_IDS = {
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip().isdigit()
}

REFERRAL_REWARD = 8.0
MIN_WITHDRAW = 75.0
MIN_REFS = 9

DB_FILE = os.getenv("DB_FILE", "bot_database.db")
GUIDE_IMAGE = os.getenv("GUIDE_IMAGE", "guide.jpg")

BOT_USERNAME = None

user_states = {}
state_lock = threading.RLock()


# =========================================================
# TELEGRAM API
# =========================================================

def telegram_api(method, data=None, files=None):
    url = f"https://api.telegram.org/bot{TOKEN}/{method}"

    try:
        response = requests.post(
            url,
            data=data or {},
            files=files,
            timeout=25
        )

        if not response.ok:
            print(
                f"Telegram API HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )
            return None

        result = response.json()

        if not result.get("ok"):
            print(
                f"Telegram API error: "
                f"{result.get('description', 'Unknown error')}"
            )

        return result

    except requests.RequestException as e:
        print("Telegram request error:", e)
        return None


def styled_button(text, callback_data=None, url=None, style="primary"):
    """
    Telegram استاندارد رنگ دکمه برای InlineKeyboard ندارد.
    style برای حفظ ساختار سورس قبلی نگه داشته شده است.
    """

    button = {
        "text": text
    }

    if callback_data is not None:
        button["callback_data"] = callback_data

    if url is not None:
        button["url"] = url

    return button


def copy_button(text, value):
    return styled_button(
        text,
        callback_data=f"copy:{value}"
    )


def send_styled_message(chat_id, text, buttons=None, **kwargs):
    markup = None

    if buttons:
        markup = types.InlineKeyboardMarkup(row_width=2)

        for row in buttons:
            keyboard_row = []

            for button in row:
                if isinstance(button, dict):
                    keyboard_row.append(
                        types.InlineKeyboardButton(
                            text=button.get("text", ""),
                            callback_data=button.get("callback_data"),
                            url=button.get("url")
                        )
                    )

            if keyboard_row:
                markup.row(*keyboard_row)

    return bot.send_message(
        chat_id,
        text,
        reply_markup=markup,
        parse_mode="HTML",
        disable_web_page_preview=True,
        **kwargs
    )


def edit_styled_message(
    chat_id,
    message_id,
    text,
    buttons=None
):
    markup = None

    if buttons:
        markup = types.InlineKeyboardMarkup(row_width=2)

        for row in buttons:
            keyboard_row = []

            for button in row:
                if isinstance(button, dict):
                    keyboard_row.append(
                        types.InlineKeyboardButton(
                            text=button.get("text", ""),
                            callback_data=button.get("callback_data"),
                            url=button.get("url")
                        )
                    )

            if keyboard_row:
                markup.row(*keyboard_row)

    try:
        return bot.edit_message_text(
            text,
            chat_id,
            message_id,
            reply_markup=markup,
            parse_mode="HTML",
            disable_web_page_preview=True
        )
    except Exception as e:
        print("Edit message error:", e)
        return None


# =========================================================
# DATABASE
# =========================================================

db_lock = threading.RLock()


@contextmanager
def get_db():
    conn = sqlite3.connect(
        DB_FILE,
        timeout=30,
        check_same_thread=False
    )

    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")

        yield conn

        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


def init_db():
    with db_lock:
        with get_db() as conn:

            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT DEFAULT '',
                    first_name TEXT DEFAULT '',
                    last_name TEXT DEFAULT '',
                    wallet TEXT DEFAULT '',
                    balance REAL DEFAULT 0,
                    referral_count INTEGER DEFAULT 0,
                    referred_by INTEGER DEFAULT NULL,
                    created_at TEXT DEFAULT '',
                    updated_at TEXT DEFAULT ''
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS referrals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    referrer_id INTEGER NOT NULL,
                    referred_id INTEGER NOT NULL UNIQUE,
                    reward REAL DEFAULT 0,
                    created_at TEXT DEFAULT ''
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS withdrawals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    amount REAL NOT NULL,
                    wallet TEXT DEFAULT '',
                    status TEXT DEFAULT 'pending',
                    channel_message_id INTEGER DEFAULT NULL,
                    created_at TEXT DEFAULT '',
                    paid_at TEXT DEFAULT NULL
                )
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_referrals_referrer
                ON referrals(referrer_id)
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_withdrawals_user
                ON withdrawals(user_id)
            """)


# =========================================================
# USER FUNCTIONS
# =========================================================

def get_user(user_id):
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE user_id = ?",
            (user_id,)
        ).fetchone()

        return dict(row) if row else None


def create_user(
    user_id,
    username="",
    first_name="",
    last_name=""
):
    now = datetime.utcnow().isoformat()

    with db_lock:
        with get_db() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO users (
                    user_id,
                    username,
                    first_name,
                    last_name,
                    wallet,
                    balance,
                    referral_count,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, '', 0, 0, ?, ?)
            """, (
                user_id,
                username or "",
                first_name or "",
                last_name or "",
                now,
                now
            ))


def update_user_profile(
    user_id,
    username=None,
    first_name=None,
    last_name=None
):
    user = get_user(user_id)

    if not user:
        return

    username = (
        user["username"]
        if username is None
        else username
    )

    first_name = (
        user["first_name"]
        if first_name is None
        else first_name
    )

    last_name = (
        user["last_name"]
        if last_name is None
        else last_name
    )

    with db_lock:
        with get_db() as conn:
            conn.execute("""
                UPDATE users
                SET username = ?,
                    first_name = ?,
                    last_name = ?,
                    updated_at = ?
                WHERE user_id = ?
            """, (
                username or "",
                first_name or "",
                last_name or "",
                datetime.utcnow().isoformat(),
                user_id
            ))


def ensure_user(user):
    create_user(
        user.id,
        getattr(user, "username", "") or "",
        getattr(user, "first_name", "") or "",
        getattr(user, "last_name", "") or ""
    )

    update_user_profile(
        user.id,
        getattr(user, "username", "") or "",
        getattr(user, "first_name", "") or "",
        getattr(user, "last_name", "") or ""
    )


def update_wallet(user_id, wallet):
    with db_lock:
        with get_db() as conn:
            conn.execute("""
                UPDATE users
                SET wallet = ?,
                    updated_at = ?
                WHERE user_id = ?
            """, (
                wallet.strip(),
                datetime.utcnow().isoformat(),
                user_id
            ))


def add_balance(user_id, amount):
    with db_lock:
        with get_db() as conn:
            conn.execute("""
                UPDATE users
                SET balance = balance + ?,
                    updated_at = ?
                WHERE user_id = ?
            """, (
                float(amount),
                datetime.utcnow().isoformat(),
                user_id
            ))


# =========================================================
# REFERRALS
# =========================================================

def process_referral(referrer_id, referred_id):
    if not referrer_id:
        return False

    if referrer_id == referred_id:
        return False

    referred = get_user(referred_id)

    if not referred:
        return False

    if referred.get("referred_by"):
        return False

    referrer = get_user(referrer_id)

    if not referrer:
        return False

    with db_lock:
        with get_db() as conn:

            existing = conn.execute("""
                SELECT id
                FROM referrals
                WHERE referred_id = ?
            """, (referred_id,)).fetchone()

            if existing:
                return False

            now = datetime.utcnow().isoformat()

            conn.execute("""
                INSERT INTO referrals (
                    referrer_id,
                    referred_id,
                    reward,
                    created_at
                )
                VALUES (?, ?, ?, ?)
            """, (
                referrer_id,
                referred_id,
                REFERRAL_REWARD,
                now
            ))

            conn.execute("""
                UPDATE users
                SET balance = balance + ?,
                    referral_count = referral_count + 1,
                    updated_at = ?
                WHERE user_id = ?
            """, (
                REFERRAL_REWARD,
                now,
                referrer_id
            ))

            conn.execute("""
                UPDATE users
                SET referred_by = ?,
                    updated_at = ?
                WHERE user_id = ?
            """, (
                referrer_id,
                now,
                referred_id
            ))

    return True


# =========================================================
# WITHDRAWALS
# =========================================================

def create_withdrawal(user_id, amount, wallet):
    with db_lock:
        with get_db() as conn:

            user = conn.execute("""
                SELECT balance
                FROM users
                WHERE user_id = ?
            """, (user_id,)).fetchone()

            if not user:
                return None

            balance = float(user["balance"])

            if amount > balance:
                return None

            if amount < MIN_WITHDRAW:
                return None

            now = datetime.utcnow().isoformat()

            conn.execute("""
                UPDATE users
                SET balance = balance - ?,
                    updated_at = ?
                WHERE user_id = ?
            """, (
                amount,
                now,
                user_id
            ))

            cursor = conn.execute("""
                INSERT INTO withdrawals (
                    user_id,
                    amount,
                    wallet,
                    status,
                    created_at
                )
                VALUES (?, ?, ?, 'pending', ?)
            """, (
                user_id,
                amount,
                wallet,
                now
            ))

            return cursor.lastrowid


def get_withdrawal(withdrawal_id):
    with get_db() as conn:
        row = conn.execute("""
            SELECT *
            FROM withdrawals
            WHERE id = ?
        """, (withdrawal_id,)).fetchone()

        return dict(row) if row else None


def set_channel_message_id(
    withdrawal_id,
    message_id
):
    with db_lock:
        with get_db() as conn:
            conn.execute("""
                UPDATE withdrawals
                SET channel_message_id = ?
                WHERE id = ?
            """, (
                message_id,
                withdrawal_id
            ))


def mark_withdrawal_paid(withdrawal_id):
    with db_lock:
        with get_db() as conn:
            conn.execute("""
                UPDATE withdrawals
                SET status = 'paid',
                    paid_at = ?
                WHERE id = ?
            """, (
                datetime.utcnow().isoformat(),
                withdrawal_id
            ))


# =========================================================
# MEMBERSHIP
# =========================================================

def check_membership(user_id):
    if not FORCED_CHANNEL:
        return True

    try:
        result = bot.get_chat_member(
            FORCED_CHANNEL,
            user_id
        )

        status = result.status

        return status in (
            "creator",
            "administrator",
            "member"
        )

    except Exception as e:
        print("Membership check error:", e)
        return False


# =========================================================
# MENUS
# =========================================================

def main_menu():
    return [
        [
            styled_button(
                "💰 موجودی",
                callback_data="balance",
                style="success"
            ),
            styled_button(
                "👤 حساب کاربری",
                callback_data="account",
                style="primary"
            )
        ],
        [
            styled_button(
                "👥 زیرمجموعه‌گیری",
                callback_data="referrals",
                style="success"
            ),
            styled_button(
                "💳 برداشت",
                callback_data="withdraw",
                style="danger"
            )
        ],
        [
            styled_button(
                "📖 راهنما",
                callback_data="help",
                style="primary"
            )
        ]
    ]


def send_main_menu(chat_id, text=None):
    if text is None:
        text = (
            "🌟 <b>به ربات خوش آمدید</b>\n\n"
            "از منوی زیر گزینه موردنظر خود را انتخاب کنید."
        )

    return send_styled_message(
        chat_id,
        text,
        main_menu()
    )


def forced_sub_menu():
    return [
        [
            styled_button(
                "📢 عضویت در کانال",
                url=(
                    f"https://t.me/"
                    f"{FORCED_CHANNEL.lstrip('@')}"
                )
            )
        ],
        [
            styled_button(
                "✅ بررسی عضویت",
                callback_data="check_join",
                style="success"
            )
        ]
    ]


def send_forced_join(chat_id):
    return send_styled_message(
        chat_id,
        (
            "🔒 <b>عضویت در کانال الزامی است</b>\n\n"
            "برای استفاده از ربات ابتدا در کانال عضو شوید "
            "و سپس روی «بررسی عضویت» بزنید."
        ),
        forced_sub_menu()
    )


# =========================================================
# ACCOUNT
# =========================================================

def show_account(chat_id):
    user = get_user(chat_id)

    if not user:
        return

    username = user["username"] or "ندارد"

    text = (
        "👤 <b>حساب کاربری</b>\n\n"
        f"🆔 شناسه: <code>{chat_id}</code>\n"
        f"👤 نام کاربری: @{username}\n"
        f"💰 موجودی: <b>{float(user['balance']):.2f}</b>\n"
        f"👥 تعداد زیرمجموعه: "
        f"<b>{user['referral_count']}</b>\n"
        f"💳 کیف پول: "
        f"<code>{user['wallet'] or 'ثبت نشده'}</code>"
    )

    buttons = [
        [
            styled_button(
                "💳 درخواست برداشت",
                callback_data="withdraw",
                style="danger"
            )
        ],
        [
            styled_button(
                "🔙 بازگشت",
                callback_data="home",
                style="primary"
            )
        ]
    ]

    send_styled_message(
        chat_id,
        text,
        buttons
    )


# =========================================================
# REFERRALS VIEW
# =========================================================

def show_referrals(chat_id):
    user = get_user(chat_id)

    if not user:
        return

    global BOT_USERNAME

    try:
        if not BOT_USERNAME:
            me = bot.get_me()
            BOT_USERNAME = me.username

    except Exception:
        BOT_USERNAME = None

    if BOT_USERNAME:
        referral_link = (
            f"https://t.me/{BOT_USERNAME}"
            f"?start={chat_id}"
        )
    else:
        referral_link = (
            f"https://t.me/?start={chat_id}"
        )

    text = (
        "👥 <b>سیستم زیرمجموعه‌گیری</b>\n\n"
        f"👤 تعداد زیرمجموعه‌ها: "
        f"<b>{user['referral_count']}</b>\n"
        f"💰 پاداش هر نفر: "
        f"<b>{REFERRAL_REWARD:.2f}</b>\n\n"
        "🔗 <b>لینک دعوت شما:</b>\n"
        f"<code>{referral_link}</code>"
    )

    buttons = [
        [
            styled_button(
                "📋 کپی لینک",
                callback_data=f"copy:{referral_link}",
                style="success"
            )
        ],
        [
            styled_button(
                "🔙 بازگشت",
                callback_data="home",
                style="primary"
            )
        ]
    ]

    send_styled_message(
        chat_id,
        text,
        buttons
    )


# =========================================================
# BALANCE
# =========================================================

def show_balance(chat_id):
    user = get_user(chat_id)
    if not user:
        return

    text = (
        "💰 <b>موجودی حساب</b>\n\n"
        f"موجودی فعلی شما:\n"
        f"<b>{float(user['balance']):.2f}</b>\n\n"
        f"حداقل برداشت: <b>{MIN_WITHDRAW:.2f}</b>"
    )

    buttons = [
        [
            styled_button(
                "💳 درخواست برداشت",
                callback_data="withdraw",
                style="danger"
            )
        ],
        [
            styled_button(
                "🔙 بازگشت",
                callback_data="home",
                style="primary"
            )
        ]
    ]

    send_styled_message(chat_id, text, buttons)


# =========================================================
# GUIDE
# =========================================================

def show_help(chat_id):
    text = (
        "📖 <b>راهنمای ربات</b>\n\n"
        "از منوی اصلی می‌توانید موجودی، حساب کاربری و زیرمجموعه‌های خود را مشاهده کنید."
    )
    send_styled_message(
        chat_id,
        text,
        [[styled_button("🔙 بازگشت", callback_data="home", style="primary")]]
    )


# =========================================================
# HANDLERS
# =========================================================

@bot.message_handler(commands=["start"])
def start_handler(message):
    try:
        ensure_user(message.from_user)

        args = message.text.split(maxsplit=1)
        if len(args) == 2 and args[1].strip().isdigit():
            process_referral(int(args[1].strip()), message.from_user.id)

        if check_membership(message.from_user.id):
            send_main_menu(message.chat.id)
        else:
            send_forced_join(message.chat.id)
    except Exception as e:
        print("Start handler error:", e)
        try:
            bot.send_message(message.chat.id, "❌ خطایی رخ داد. لطفاً دوباره /start را بزنید.")
        except Exception as send_error:
            print("Send error:", send_error)


@bot.callback_query_handler(func=lambda call: True)
def callback_handler(call):
    chat_id = call.message.chat.id
    data = call.data or ""

    try:
        bot.answer_callback_query(call.id)
        ensure_user(call.from_user)

        if data == "check_join":
            if check_membership(call.from_user.id):
                send_main_menu(chat_id)
            else:
                send_forced_join(chat_id)
            return

        if not check_membership(call.from_user.id):
            send_forced_join(chat_id)
            return

        if data == "home":
            send_main_menu(chat_id)
        elif data == "balance":
            show_balance(chat_id)
        elif data == "account":
            show_account(chat_id)
        elif data == "referrals":
            show_referrals(chat_id)
        elif data == "help":
            show_help(chat_id)
        elif data.startswith("copy:"):
            value = data[5:]
            bot.send_message(chat_id, f"📋 <code>{value}</code>", parse_mode="HTML")
        elif data == "withdraw":
            bot.send_message(
                chat_id,
                "💳 بخش برداشت در این نسخه از فایل، هندلر کامل و متصل ندارد."
            )
    except Exception as e:
        print("Callback handler error:", e)
        try:
            bot.answer_callback_query(call.id, "خطایی رخ داد.", show_alert=True)
        except Exception:
            pass


# =========================================================
# REPLIT STARTUP
# =========================================================

if __name__ == "__main__":
    init_db()
    print("Bot is starting...")
    bot.infinity_polling(
        skip_pending=True,
        timeout=30,
        long_polling_timeout=30,
        allowed_updates=["message", "callback_query"]
    )
