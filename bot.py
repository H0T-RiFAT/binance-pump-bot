import time
import requests
import ccxt
import threading
from flask import Flask

# Flask app to satisfy Render Web Service port check
app = Flask(__name__)

@app.route('/')
def home():
    return "Pre-Pump Bot is Running Active!"

def run_flask():
    import os
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

# Start Flask in a background thread
threading.Thread(target=run_flask, daemon=True).start()

# ==================== CONFIGURATION ====================
TELEGRAM_BOT_TOKEN = "8868734831:AAG5dN3JDANsRjQbcLsPdUqZX5_7yjSPXvM"
TELEGRAM_CHAT_ID = "8825999665"

# Scan Parameters
TIMEFRAME = '1m'
MA_PERIOD = 20
TOP_N_COINS = 50
MIN_SPIKE_MULTIPLIER = 5.0

exchange = ccxt.binance({
    'enableRateLimit': True,
    'rateLimit': 1500,
    'options': {'defaultType': 'future'},
    'headers': {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
})

def send_telegram_alert(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Telegram Connection Error: {e}")

def get_binance_top_coins(limit=50):
    try:
        tickers = exchange.fetch_tickers()
        usdt_pairs = []
        for symbol, ticker in tickers.items():
            if symbol.endswith('/USDT:USDT') or symbol.endswith('/USDT'):
                vol_24h = ticker.get('quoteVolume', 0)
                if vol_24h:
                    usdt_pairs.append((symbol, vol_24h))
        usdt_pairs.sort(key=lambda x: x[1], reverse=True)
        return [item[0] for item in usdt_pairs[:limit]]
    except Exception as e:
        print(f"Error fetching tickers: {e}")
        return []

def calculate_score_and_stars(vol_spike):
    if vol_spike >= 100.0:
        return 10, 10, "SUPER PUMP DETECTED 🚀"
    elif vol_spike >= 50.0:
        return 9, 9, "HIGH PUMP CHANCE 🔥"
    elif vol_spike >= 15.0:
        return 7, 7, "RAPID SPIKE ⚡"
    elif vol_spike >= 8.0:
        return 6, 6, "VOLUME BUILDING 📈"
    else:
        return 5, 5, "EARLY SPIKE DETECTED 👀"

def scan_binance_market():
    coins = get_binance_top_coins(TOP_N_COINS)
    if not coins:
        return

    for symbol in coins:
        try:
            time.sleep(0.1)
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=MA_PERIOD + 1)
            if len(ohlcv) < MA_PERIOD + 1:
                continue

            past_candles = ohlcv[:-1]
            current_candle = ohlcv[-1]

            avg_vol_usdt = sum(c[5] * c[4] for c in past_candles) / MA_PERIOD
            current_vol_usdt = current_candle[5] * current_candle[4]

            if avg_vol_usdt == 0:
                continue

            vol_spike = current_vol_usdt / avg_vol_usdt
            open_price = current_candle[1]
            current_price = current_candle[4]
            price_change = ((current_price - open_price) / open_price) * 100

            if vol_spike >= MIN_SPIKE_MULTIPLIER and price_change > 0.2:
                score, stars, status_label = calculate_score_and_stars(vol_spike)
                star_str = "⭐" * stars
                clean_symbol = symbol.split('/')[0] + "USDT"

                telegram_msg = (
                    f"🚨 *PRE-PUMP ALERT • {clean_symbol}* `[{TIMEFRAME}]`\n"
                    f"💥 *{status_label}*\n\n"
                    f"Stars: {star_str}\n"
                    f"Entry: `${current_price:.6f}`\n"
                    f"Volume Spike: *{vol_spike:.1f}x avg*\n"
                    f"Avg Vol (20x1m): `${avg_vol_usdt/1000:.1f}k` | Current: `${current_vol_usdt/1000000:.2f}m`\n"
                    f"Price Change: `+{price_change:.2f}%`\n\n"
                    f"📈 *Signal Score:* `{score}/10` | *Timeframe:* `{TIMEFRAME}`\n"
                    f"----------------------------------------"
                )

                print(f"[SPIKE FOUND] {clean_symbol} - Volume Spike: {vol_spike:.1f}x")
                send_telegram_alert(telegram_msg)

        except Exception:
            time.sleep(1)
            continue

if __name__ == "__main__":
    startup_msg = (
        "🚀 *Binance Pre-Pump Scanner Started!*\n\n"
        f"• *Timeframe:* `{TIMEFRAME}`\n"
        f"• *Scan Scope:* Top `{TOP_N_COINS}` Binance Pairs\n"
        f"• *Min Spike Trigger:* `{MIN_SPIKE_MULTIPLIER}x`\n\n"
        "🟢 Scanner is active and monitoring market spikes..."
    )
    send_telegram_alert(startup_msg)
    
    while True:
        scan_binance_market()
        time.sleep(60)
