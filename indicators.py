import asyncio
import pandas as pd
import ta
from typing import List, Optional

# Helper to fetch OHLCV inside indicator file if needed
async def fetch_ohlcv_df_async(exchange, symbol: str, timeframe: str = '1m', limit: int = 100) -> pd.DataFrame:
    def fetch_sync():
        return exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    ohlcv = await asyncio.to_thread(fetch_sync)
    df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
    return df

async def check_rsi_alerts(
    bot,
    chat_id: int,
    symbols: List[str],
    exchange,
    send_message,
    last_rsi_alert: dict,
    rsi_period: int = 14,
    rsi_oversold: int = 30,
    rsi_overbought: int = 70,
    timeframes: Optional[List[str]] = None
):
    if timeframes is None:
        timeframes = ['1m']

    for symbol in symbols:
        for timeframe in timeframes:
            try:
                df = await fetch_ohlcv_df_async(exchange, symbol, timeframe, 100)
                df['rsi'] = ta.momentum.rsi(df['close'], window=rsi_period)
                current_rsi = df['rsi'].iloc[-1]

                key = (symbol, timeframe)
                last_alert = last_rsi_alert.get(key, None)

                if current_rsi < rsi_oversold and last_alert != 'oversold':
                    await send_message(bot, chat_id, f"⚠️ {symbol} RSI is oversold at {current_rsi:.2f} on {timeframe} timeframe (buy opportunity?)")
                    last_rsi_alert[key] = 'oversold'

                elif current_rsi > rsi_overbought and last_alert != 'overbought':
                    await send_message(bot, chat_id, f"⚠️ {symbol} RSI is overbought at {current_rsi:.2f} on {timeframe} timeframe (sell opportunity?)")
                    last_rsi_alert[key] = 'overbought'

                elif rsi_oversold <= current_rsi <= rsi_overbought:
                    last_rsi_alert[key] = None

                await asyncio.sleep(0.2)
            except Exception as e:
                print(f"Error checking RSI for {symbol} on {timeframe}: {e}")

async def check_macd_alerts(
    bot,
    chat_id: int,
    symbols: List[str],
    exchange,
    send_message,
    last_macd_alert: dict,
    timeframes: Optional[List[str]] = None,
    limit: int = 100
):
    if timeframes is None:
        timeframes = ['1m']

    for symbol in symbols:
        for timeframe in timeframes:
            try:
                df = await fetch_ohlcv_df_async(exchange, symbol, timeframe=timeframe, limit=limit)
                macd_indicator = ta.trend.MACD(df['close'])
                macd = macd_indicator.macd()
                signal = macd_indicator.macd_signal()

                if len(macd) < 2 or len(signal) < 2:
                    continue

                prev_macd = macd.iloc[-2]
                prev_signal = signal.iloc[-2]
                curr_macd = macd.iloc[-1]
                curr_signal = signal.iloc[-1]

                key = (symbol, timeframe)
                last_alert = last_macd_alert.get(key, None)

                if prev_macd < prev_signal and curr_macd > curr_signal and last_alert != 'bullish':
                    await send_message(bot, chat_id, f"📈 {symbol} MACD bullish crossover detected on {timeframe} — consider buying.")
                    last_macd_alert[key] = 'bullish'

                elif prev_macd > prev_signal and curr_macd < curr_signal and last_alert != 'bearish':
                    await send_message(bot, chat_id, f"📉 {symbol} MACD bearish crossover detected on {timeframe} — consider selling.")
                    last_macd_alert[key] = 'bearish'

                elif (prev_macd < prev_signal and curr_macd < curr_signal) or (prev_macd > prev_signal and curr_macd > curr_signal):
                    last_macd_alert[key] = None

                await asyncio.sleep(0.2)
            except Exception as e:
                print(f"Error checking MACD for {symbol} on {timeframe}: {e}")

async def check_ma_crossover_alerts(
    bot,
    chat_id: int,
    symbols: List[str],
    exchange,
    send_message,
    last_crossover_alert: dict,
    timeframes: Optional[List[str]] = None,
    limit: int = 100,
    fast_period: int = 12,
    slow_period: int = 26
):
    if timeframes is None:
        timeframes = ['1m']

    for symbol in symbols:
        for timeframe in timeframes:
            try:
                df = await fetch_ohlcv_df_async(exchange, symbol, timeframe=timeframe, limit=limit)

                df['ema_fast'] = ta.trend.EMAIndicator(df['close'], window=fast_period).ema_indicator()
                df['sma_slow'] = ta.trend.SMAIndicator(df['close'], window=slow_period).sma_indicator()

                if len(df) < 2:
                    continue

                prev_ema = df['ema_fast'].iloc[-2]
                prev_sma = df['sma_slow'].iloc[-2]
                curr_ema = df['ema_fast'].iloc[-1]
                curr_sma = df['sma_slow'].iloc[-1]

                key = (symbol, timeframe)
                last_alert = last_crossover_alert.get(key, None)

                if prev_ema < prev_sma and curr_ema > curr_sma and last_alert != 'bullish':
                    await send_message(bot, chat_id, f"📈 {symbol} EMA({fast_period}) crossed above SMA({slow_period}) on {timeframe} — bullish signal.")
                    last_crossover_alert[key] = 'bullish'

                elif prev_ema > prev_sma and curr_ema < curr_sma and last_alert != 'bearish':
                    await send_message(bot, chat_id, f"📉 {symbol} EMA({fast_period}) crossed below SMA({slow_period}) on {timeframe} — bearish signal.")
                    last_crossover_alert[key] = 'bearish'

                elif (prev_ema < prev_sma and curr_ema < curr_sma) or (prev_ema > prev_sma and curr_ema > curr_sma):
                    last_crossover_alert[key] = None

                await asyncio.sleep(0.2)
            except Exception as e:
                print(f"Error checking MA crossover for {symbol} on {timeframe}: {e}")
