import asyncio
import ccxt
import operator
import uuid
from telegram import Update
from telegram.ext import (
    ContextTypes,
    CommandHandler,
    ApplicationBuilder,
)

from dotenv import load_dotenv
import os
load_dotenv()

from indicators import (
    check_rsi_alerts,
    check_macd_alerts,
    check_ma_crossover_alerts
)

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler

# --- Config ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
if not TELEGRAM_TOKEN:
    raise ValueError("Missing TELEGRAM_TOKEN environment variable!")

CHECK_INTERVAL_SECONDS = 60

# Use Bybit exchange instead of Binance
exchange = ccxt.bybit({
    'enableRateLimit': True,
    'options': {
        'adjustForTimeDifference': True,
    }
})

# Globals
last_rsi_alert = {}
last_macd_alert = {}
last_crossover_alert = {}
user_alerts = {}
registered_chat_ids = set()


OPERATORS = {
    ">": operator.gt,
    "<": operator.lt,
    ">=": operator.ge,
    "<=": operator.le,
    "==": operator.eq,
}

VALID_TIMEFRAMES = ['1m', '3m', '5m', '15m', '30m', '1h', '2h', '4h', '6h', '12h', '1d', "1w"]

async def send_message(bot, chat_id, text):
    try:
        await bot.send_message(chat_id=chat_id, text=text)
    except Exception as e:
        print(f"Error sending message: {e}")

# User price alerts check (keeping it here for brevity)
async def fetch_ohlcv_df_async(symbol, timeframe='1m', limit=100):
    def fetch_sync():
        return exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    import pandas as pd
    ohlcv = await asyncio.to_thread(fetch_sync)
    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    return df

async def check_user_price_alerts(bot):
    for chat_id, alerts in user_alerts.items():
        for alert in alerts:
            symbol = alert['symbol']
            operator_fn = OPERATORS[alert['operator']]
            price_target = alert['price']
            timeframe = alert['timeframe']

            try:
                df = await fetch_ohlcv_df_async(symbol, timeframe=timeframe, limit=1)
                if df.empty:
                    continue
                last_price = df['close'].iloc[-1]

                if operator_fn(last_price, price_target):
                    await send_message(
                        bot,
                        chat_id,
                        f"💰 Alert: {symbol} price is {last_price:.4f} which is {alert['operator']} {price_target}"
                    )
                    alerts.remove(alert)
            except Exception as e:
                print(f"Error checking user alert for {symbol} in chat {chat_id}: {e}")

# Telegram commands

async def setprice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    args = context.args

    if len(args) not in [3, 4]:
        await update.message.reply_text(
            "Usage: /setprice SYMBOL OPERATOR PRICE [TIMEFRAME]\nExample: /setprice BTC/USDT > 30000 5m"
        )
        return

    symbol, op_str, price_str = args[:3]
    timeframe = args[3] if len(args) == 4 else '1m'

    if op_str not in OPERATORS:
        await update.message.reply_text(f"Invalid operator. Use one of: {', '.join(OPERATORS.keys())}")
        return

    try:
        price = float(price_str)
    except ValueError:
        await update.message.reply_text("Invalid price. Must be a number.")
        return

    if timeframe not in VALID_TIMEFRAMES:
        await update.message.reply_text(f"Invalid timeframe. Choose from: {', '.join(VALID_TIMEFRAMES)}")
        return

    alert = {
        "id": str(uuid.uuid4()),
        "symbol": symbol.upper(),
        "operator": op_str,
        "price": price,
        "timeframe": timeframe,
    }

    user_alerts.setdefault(chat_id, []).append(alert)
    await update.message.reply_text(f"Alert set: {symbol} {op_str} {price} on {timeframe} timeframe")

async def listalerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    alerts = user_alerts.get(chat_id, [])
    if not alerts:
        await update.message.reply_text("You have no active alerts.")
        return

    lines = []
    for alert in alerts:
        lines.append(
            f"{alert['id'][:8]}: {alert['symbol']} {alert['operator']} {alert['price']} on {alert['timeframe']}"
        )
    await update.message.reply_text("Your alerts:\n" + "\n".join(lines))

async def removealert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    args = context.args

    if len(args) != 1:
        await update.message.reply_text("Usage: /removealert ALERT_ID\nUse /listalerts to get IDs.")
        return

    alert_id = args[0]
    alerts = user_alerts.get(chat_id, [])
    for alert in alerts:
        if alert['id'].startswith(alert_id):
            alerts.remove(alert)
            await update.message.reply_text(f"Removed alert {alert_id}")
            return

    await update.message.reply_text(f"No alert found with ID starting '{alert_id}'")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "SUI/USDT","NEAR/USDT"]
    msg_lines = ["📊 Current Prices:"]

    for symbol in symbols:
        try:
            df = await fetch_ohlcv_df_async(symbol, timeframe="1m", limit=1)
            if not df.empty:
                last_price = df["close"].iloc[-1]
                msg_lines.append(f"{symbol}: {last_price:.4f}")
        except Exception as e:
            msg_lines.append(f"{symbol}: error fetching price")
            print(f"Error fetching price for {symbol}: {e}")

    await update.message.reply_text("\n".join(msg_lines))

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "/setprice SYMBOL OPERATOR PRICE [TIMEFRAME] - Set a price alert (timeframe optional, default 1m)\n"
        "Example: /setprice BTC/USDT > 30000 5m\n"
        "/listalerts - List your alerts\n"
        "/removealert ALERT_ID - Remove an alert\n"
        "/status - Show tracked symbols\n"
        "/help - Show this help message\n"
        "/commands - Show this list of commands\n"
    )
    await update.message.reply_text(help_text)

async def commands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    commands_text = (
        "/setprice SYMBOL OPERATOR PRICE [TIMEFRAME] - Set a price alert (timeframe optional, default 1m)\n"
        "/listalerts - List your active alerts\n"
        "/removealert ALERT_ID - Remove an alert by ID\n"
        "/status - Show tracked symbols\n"
        "/help - Show help message\n"
        "/commands - Show this list of commands\n"
    )
    await update.message.reply_text(commands_text)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    registered_chat_ids.add(chat_id)  # Add to your chat_ids set

    keyboard = [
        [InlineKeyboardButton("Set Price Alert", callback_data='setprice')],
        [InlineKeyboardButton("List Alerts", callback_data='listalerts')],
        [InlineKeyboardButton("Remove Alert", callback_data='removealert')],
        [InlineKeyboardButton("Show Status", callback_data='status')],
        [InlineKeyboardButton("Help", callback_data='help')],
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "Welcome! Choose an option:",
        reply_markup=reply_markup
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data

    if data == "setprice":
        await query.message.reply_text(
            "To set price alert, use command:\n/setprice SYMBOL OPERATOR PRICE [TIMEFRAME]\nExample:\n/setprice BTC/USDT > 30000 5m"
        )

    elif data == "listalerts":
        chat_id = query.message.chat_id
        alerts = user_alerts.get(chat_id, [])
        if not alerts:
            await query.message.reply_text("You have no active alerts.")
        else:
            lines = []
            for alert in alerts:
                lines.append(
                    f"{alert['id'][:8]}: {alert['symbol']} {alert['operator']} {alert['price']} on {alert['timeframe']}"
                )
            await query.message.reply_text("Your alerts:\n" + "\n".join(lines))

    elif data == "removealert":
        await query.message.reply_text(
            "To remove alert, use command:\n/removealert ALERT_ID\nUse /listalerts to get IDs."
        )

    elif data == "status":
        symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "SUI/USDT", "NEAR/USDT"]
        msg_lines = ["📊 Current Prices:"]
        for symbol in symbols:
            try:
                df = await fetch_ohlcv_df_async(symbol, timeframe="1m", limit=1)
                if not df.empty:
                    last_price = df["close"].iloc[-1]
                    msg_lines.append(f"{symbol}: {last_price:.4f}")
            except Exception as e:
                msg_lines.append(f"{symbol}: error fetching price")
                print(f"Error fetching price for {symbol}: {e}")
        await query.message.reply_text("\n".join(msg_lines))

    elif data == "help":
        help_text = (
            "/setprice SYMBOL OPERATOR PRICE [TIMEFRAME] - Set a price alert (timeframe optional, default 1m)\n"
            "Example: /setprice BTC/USDT > 30000 5m\n"
            "/listalerts - List your alerts\n"
            "/removealert ALERT_ID - Remove an alert\n"
            "/status - Show tracked symbols\n"
            "/help - Show this help message\n"
            "/commands - Show this list of commands\n"
        )
        await query.message.reply_text(help_text)

#indicators 
async def scheduled_checks(bot, chat_ids, symbols):
    timeframes = ['15m', '30m', '1h', '4h', '1d', '1w']
    while True:
        for chat_id in chat_ids:
            await check_rsi_alerts(bot, chat_id, symbols, exchange, send_message, last_rsi_alert, timeframes=timeframes)
            await check_macd_alerts(bot, chat_id, symbols, exchange, send_message, last_macd_alert, timeframes=timeframes)
            await check_ma_crossover_alerts(bot, chat_id, symbols, exchange, send_message, last_crossover_alert, timeframes=timeframes)
        await check_user_price_alerts(bot)
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


#build and run bot
def build_app(token):
    application = ApplicationBuilder().token(token).build()

    application.add_handler(CommandHandler("setprice", setprice))
    application.add_handler(CommandHandler("listalerts", listalerts))
    application.add_handler(CommandHandler("removealert", removealert))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("commands", commands))
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler)) 

    return application


def main():
    global registered_chat_ids

    application = build_app(TELEGRAM_TOKEN)

    symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "SUI/USDT", "NEAR/USDT"]
    bot = application.bot

    async def on_startup(app):
        asyncio.create_task(scheduled_checks(bot, registered_chat_ids, symbols))

    application.post_init = on_startup

    application.run_polling()

if __name__ == "__main__":
    main()
