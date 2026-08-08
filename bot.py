import time
import requests
import ccxt

# ==================== CONFIGURATION ====================
TELEGRAM_BOT_TOKEN = "8868734831:AAG5dN3JDANsRjQbcLsPdUqZX5_7yjSPXvM"
TELEGRAM_CHAT_ID = "8825999665"

# Scan Parameters
TIMEFRAME = '1m'            # 1 minute candle
MA_PERIOD = 20              # 20 historical candles average
TOP_N_COINS = 200           # Binance Top 200 volume coins
MIN_SPIKE_MULTIPLIER = 15.0 # Minimum 15x Volume Spike trigger

# Binance Setup (Futures Pairs)
exchange = ccxt.binance({
    'enableRateLimit': True,
    'options': {'defaultType': 'future'}
})

def send_telegram_alert(message):
    """Send Notification to Telegram"""
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code != 200:
            print(f"Telegram API Error: {response.text}")
    except Exception as e:
        print(f"Telegram Connection Error: {e}")

def get_binance_top_coins(limit=200):
    """Fetch Top N Binance USDT Futures pairs sorted by 24h volume"""
    tickers = exchange.fetch_tickers()
    usdt_pairs = []
    
    for symbol, ticker in tickers.items():
        if symbol.endswith('/USDT:USDT') or symbol.endswith('/USDT'):
            vol_24h = ticker.get('quoteVolume', 0)
            usdt_pairs.append((symbol, vol_24h))
            
    usdt_pairs.sort(key=lambda x: x[1], reverse=True)
    return [item[0] for item in usdt_pairs[:limit]]

def calculate_score(vol_spike):
    """Score logic based on Spike Multiplier"""
    if vol_spike >= 150:
        return 10, 10, "Super Strong"
    elif vol_spike >= 80:
        return 9, 9, "Very Strong"
    elif vol_spike >= 40:
        return 8, 8, "Strong"
    elif vol_spike >= 20:
        return 7, 7, "Moderate"
    else:
        return 6, 6, "Normal"

def scan_binance_market():
    coins = get_binance_top_coins(TOP_N_COINS)

    for symbol in coins:
        try:
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

            if vol_spike >= MIN_SPIKE_MULTIPLIER and price_change > 0.3:
                score, stars, label = calculate_score(vol_spike)
                star_str = "⭐" * stars
                clean_symbol = symbol.split('/')[0] + "USDT"

                telegram_msg = (
                    f"🚨 *CONFIRMED PUMP • {clean_symbol}* `[{TIMEFRAME}]`\n"
                    f"💥 *High Chance of Pump — Rapid Spike Detected!*\n\n"
                    f"Stars: {star_str}\n"
                    f"Status: *({label})*\n"
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
            continue

if __name__ == "__main__":
    startup_msg = (
        "🚀 *Binance Pre-Pump Scanner Started!*\n\n"
        f"• *Timeframe:* `{TIMEFRAME}`\n"
        f"• *Scan Scope:* Top `{TOP_N_COINS}` Binance Coins\n"
        f"• *Min Spike Trigger:* `{MIN_SPIKE_MULTIPLIER}x`\n\n"
        "🟢 Bot is now actively scanning the market..."
    )
    print("Sending startup alert to Telegram...")
    send_telegram_alert(startup_msg)
    
    print("🚀 Binance Pre-Pump Bot Started Scanning...")
    while True:
        scan_binance_market()
        time.sleep(60)  # 1-minute interval loop