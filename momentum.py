import asyncio
import ccxt
import pandas as pd
import ta
import operator
import uuid
from telegram import Update
from telegram.ext import (
    ContextTypes,
    CommandHandler,
    ApplicationBuilder,
)

import os

# --- Configuration ---



TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

if not TELEGRAM_TOKEN:
    raise ValueError("Missing TELEGRAM_TOKEN environment variable!")

CHECK_INTERVAL_SECONDS = 60  # Run indicators every 60 seconds

exchange = ccxt.binance()

# --- Globals ---

last_rsi_alert = {}
last_macd_alert = {}
last_crossover_alert = {}
user_alerts = {}

# --- Helper Functions ---

OPERATORS = {
    ">": operator.gt,
    "<": operator.lt,
    ">=": operator.ge,
    "<=": operator.le,
    "==": operator.eq,
}

VALID_TIMEFRAMES = ['1m', '3m', '5m', '15m', '30m', '1h', '2h', '4h', '6h', '12h', '1d']

async def send_message(bot, chat_id, text):
    try:
        await bot.send_message(chat_id=chat_id, text=text)
    except Exception as e:
        print(f"Error sending message: {e}")

async def fetch_ohlcv_df_async(symbol, timeframe='1m', limit=100):
    def fetch_sync():
        return exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    ohlcv = await asyncio.to_thread(fetch_sync)
    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    return df

# --- Indicator checks ---

async def check_rsi_alerts(bot, chat_id, symbols, rsi_period=14, rsi_oversold=30, rsi_overbought=70):
    for symbol in symbols:
        try:
            df = await fetch_ohlcv_df_async(symbol)
            df['rsi'] = ta.momentum.rsi(df['close'], window=rsi_period)
            current_rsi = df['rsi'].iloc[-1]

            last_alert = last_rsi_alert.get(symbol, None)
            if current_rsi < rsi_oversold and last_alert != 'oversold':
                await send_message(bot, chat_id, f"⚠️ {symbol} RSI is oversold at {current_rsi:.2f} (buy opportunity?)")
                last_rsi_alert[symbol] = 'oversold'

            elif current_rsi > rsi_overbought and last_alert != 'overbought':
                await send_message(bot, chat_id, f"⚠️ {symbol} RSI is overbought at {current_rsi:.2f} (sell opportunity?)")
                last_rsi_alert[symbol] = 'overbought'

            elif rsi_oversold <= current_rsi <= rsi_overbought:
                last_rsi_alert[symbol] = None

            await asyncio.sleep(0.2)  # slight delay for rate limits
        except Exception as e:
            print(f"Error checking RSI for {symbol}: {e}")

async def check_macd_alerts(bot, chat_id, symbols, timeframe='1m', limit=100):
    for symbol in symbols:
        try:
            df = await fetch_ohlcv_df_async(symbol, timeframe=timeframe, limit=limit)
            macd_indicator = ta.trend.MACD(df['close'])
            macd = macd_indicator.macd()
            signal = macd_indicator.macd_signal()

            if len(macd) < 2 or len(signal) < 2:
                continue

            prev_macd = macd.iloc[-2]
            prev_signal = signal.iloc[-2]
            curr_macd = macd.iloc[-1]
            curr_signal = signal.iloc[-1]

            last_alert = last_macd_alert.get(symbol, None)

            # Bullish crossover
            if prev_macd < prev_signal and curr_macd > curr_signal and last_alert != 'bullish':
                await send_message(bot, chat_id, f"📈 {symbol} MACD bullish crossover detected — consider buying.")
                last_macd_alert[symbol] = 'bullish'

            # Bearish crossover
            elif prev_macd > prev_signal and curr_macd < curr_signal and last_alert != 'bearish':
                await send_message(bot, chat_id, f"📉 {symbol} MACD bearish crossover detected — consider selling.")
                last_macd_alert[symbol] = 'bearish'

            # Reset alert when no crossover
            elif (prev_macd < prev_signal and curr_macd < curr_signal) or (prev_macd > prev_signal and curr_macd > curr_signal):
                last_macd_alert[symbol] = None

            await asyncio.sleep(0.2)
        except Exception as e:
            print(f"Error checking MACD for {symbol}: {e}")

async def check_ma_crossover_alerts(bot, chat_id, symbols, timeframe='1m', limit=100, fast_period=12, slow_period=26):
    for symbol in symbols:
        try:
            df = await fetch_ohlcv_df_async(symbol, timeframe=timeframe, limit=limit)

            df['ema_fast'] = ta.trend.EMAIndicator(df['close'], window=fast_period).ema_indicator()
            df['sma_slow'] = ta.trend.SMAIndicator(df['close'], window=slow_period).sma_indicator()

            if len(df) < 2:
                continue

            prev_ema = df['ema_fast'].iloc[-2]
            prev_sma = df['sma_slow'].iloc[-2]
            curr_ema = df['ema_fast'].iloc[-1]
            curr_sma = df['sma_slow'].iloc[-1]

            last_alert = last_crossover_alert.get(symbol, None)

            # Bullish crossover
            if prev_ema < prev_sma and curr_ema > curr_sma and last_alert != 'bullish':
                await send_message(bot, chat_id, f"📈 {symbol} EMA({fast_period}) crossed above SMA({slow_period}) — bullish signal.")
                last_crossover_alert[symbol] = 'bullish'

            # Bearish crossover
            elif prev_ema > prev_sma and curr_ema < curr_sma and last_alert != 'bearish':
                await send_message(bot, chat_id, f"📉 {symbol} EMA({fast_period}) crossed below SMA({slow_period}) — bearish signal.")
                last_crossover_alert[symbol] = 'bearish'

            # Reset alert when no crossover
            elif (prev_ema < prev_sma and curr_ema < curr_sma) or (prev_ema > prev_sma and curr_ema > curr_sma):
                last_crossover_alert[symbol] = None

            await asyncio.sleep(0.2)
        except Exception as e:
            print(f"Error checking MA crossover for {symbol}: {e}")

# --- User price alerts check ---

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
                    # Remove alert after triggering to avoid repeats
                    alerts.remove(alert)
            except Exception as e:
                print(f"Error checking user alert for {symbol} in chat {chat_id}: {e}")

# --- Telegram Bot Commands ---

async def setprice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    args = context.args

    if len(args) not in [3, 4]:
        await update.message.reply_text(
            "Usage: /setprice SYMBOL OPERATOR PRICE [TIMEFRAME]\nExample: /setprice BTC/USDT > 30000 5m"
        )
        return

    symbol, op_str, price_str = args[:3]
    timeframe = args[3] if len(args) == 4 else '1m'  # default timeframe

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
    symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "SUI/USDT"]
    msg = "Tracked symbols:\n" + "\n".join(symbols)
    await update.message.reply_text(msg)

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

# --- Build and run bot ---

def build_app(token):
    application = ApplicationBuilder().token(token).build()

    application.add_handler(CommandHandler("setprice", setprice))
    application.add_handler(CommandHandler("listalerts", listalerts))
    application.add_handler(CommandHandler("removealert", removealert))
    application.add_handler(CommandHandler("status", status))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("commands", commands))

    return application

async def scheduled_checks(bot, chat_ids, symbols):
    while True:
        for chat_id in chat_ids:
            await check_rsi_alerts(bot, chat_id, symbols)
            await check_macd_alerts(bot, chat_id, symbols)
            await check_ma_crossover_alerts(bot, chat_id, symbols)
        await check_user_price_alerts(bot)
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)

def main():
    application = build_app(TELEGRAM_TOKEN)

    chat_ids = []  # your chat IDs
    symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT", "SUI/USDT"]

    bot = application.bot

    async def on_startup(app):
        asyncio.create_task(scheduled_checks(bot, chat_ids, symbols))

    application.post_init = on_startup

    application.run_polling()

if __name__ == "__main__":
    main()
