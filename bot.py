import time
import os
import requests
import ccxt
import threading
from datetime import datetime, timezone
from flask import Flask, render_template, jsonify, request
import database

app = Flask(__name__, template_folder='templates', static_folder='static')

# Initialize DB on import/start
database.init_db()

# Configuration (with Environment Variable overrides)
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "8868734831:AAG5dN3JDANsRjQbcLsPdUqZX5_7yjSPXvM")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "8825999665")
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL", "https://binance-pump-bot-tuq8.onrender.com")

TIMEFRAME = '1m'
MA_PERIOD = 20
TOP_N_COINS = 50

# Public Market Data Exchange Instance
public_exchange = ccxt.binance({
    'enableRateLimit': True,
    'rateLimit': 1500,
    'options': {'defaultType': 'future'},
    'headers': {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
})

def get_real_binance_exchange():
    api_key = os.environ.get("BINANCE_API_KEY") or database.get_setting('binance_api_key', '')
    secret_key = os.environ.get("BINANCE_API_SECRET") or database.get_setting('binance_api_secret', '')
    
    if not api_key or not secret_key:
        return None

    return ccxt.binance({
        'apiKey': api_key,
        'secret': secret_key,
        'enableRateLimit': True,
        'options': {'defaultType': 'future'}
    })

def send_telegram_alert(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
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

# ==================== FLASK WEB ROUTES ====================

@app.route('/')
def dashboard():
    return render_template('index.html')

@app.route('/ping')
def ping():
    return jsonify({
        'status': 'ok',
        'message': 'Binance Pre-Pump Bot & Overview Server Active',
        'timestamp': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
    })

@app.route('/api/stats')
def get_stats():
    days = request.args.get('days', 30, type=int)
    stats = database.get_dashboard_stats(days)
    return jsonify(stats)

@app.route('/api/signals')
def get_signals():
    limit = request.args.get('limit', 50, type=int)
    signals = database.get_recent_signals(limit)
    return jsonify(signals)

@app.route('/api/trades')
def get_trades():
    limit = request.args.get('limit', 100, type=int)
    open_trades = database.get_open_trades()
    
    # Calculate live current price and PnL for open trades
    for trade in open_trades:
        try:
            ticker = public_exchange.fetch_ticker(trade['symbol'])
            curr_price = ticker.get('last', trade['entry_price'])
            pnl_pct = ((curr_price - trade['entry_price']) / trade['entry_price']) * 100.0
            margin = trade.get('margin_usdt', 20.0) or 20.0
            lev = trade.get('leverage', 5) or 5
            
            trade['current_price'] = curr_price
            trade['live_pnl_pct'] = round(pnl_pct, 2)
            trade['live_pnl_usdt'] = round(margin * lev * (pnl_pct / 100.0), 2)
        except Exception:
            trade['current_price'] = trade['entry_price']
            trade['live_pnl_pct'] = 0.0
            trade['live_pnl_usdt'] = 0.0

    history = database.get_trade_history(limit)
    return jsonify({
        'open_trades': open_trades,
        'history': history
    })

@app.route('/api/settings', methods=['GET', 'POST'])
def handle_settings():
    if request.method == 'POST':
        data = request.json or {}
        allowed_keys = [
            'tp_pct', 'sl_pct', 'hold_time_mins', 'min_spike_multiplier', 
            'send_telegram_trade_close', 'execution_mode', 'paper_initial_capital',
            'trade_margin', 'trade_leverage', 'max_open_trades',
            'binance_api_key', 'binance_api_secret'
        ]
        for key in allowed_keys:
            if key in data and data[key] is not None:
                database.set_setting(key, data[key])
                
        return jsonify({'status': 'success', 'settings': database.get_all_settings()})
    else:
        return jsonify(database.get_all_settings())

@app.route('/api/mode', methods=['GET', 'POST'])
def handle_mode():
    if request.method == 'POST':
        data = request.json or {}
        new_mode = data.get('mode', 'PAPER').upper()
        if new_mode in ('PAPER', 'REAL'):
            database.set_setting('execution_mode', new_mode)
            print(f"[SYSTEM MODE] Execution mode updated to: {new_mode}")
            return jsonify({'status': 'success', 'execution_mode': new_mode})
        return jsonify({'status': 'error', 'message': 'Invalid mode'}), 400
    else:
        current_mode = database.get_setting('execution_mode', 'PAPER')
        return jsonify({'execution_mode': current_mode})

# ==================== BACKGROUND MONITORING ENGINE ====================

def get_binance_top_coins(limit=50):
    try:
        tickers = public_exchange.fetch_tickers()
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

def execute_trade_on_binance_real(symbol, entry_price, tp_price, sl_price, margin_usdt, leverage):
    """Executes a Real Market Long Order on Binance Futures with TP & SL orders."""
    real_ex = get_real_binance_exchange()
    if not real_ex:
        print(f"[REAL TRADE ERROR] Missing Binance API Key or Secret!")
        return None

    try:
        clean_sym = symbol.split(':')[0]
        # 1. Set leverage on Binance
        try:
            real_ex.set_leverage(int(leverage), clean_sym)
        except Exception as e:
            print(f"[REAL TRADE WARN] Could not set leverage: {e}")

        # 2. Calculate quantity based on margin * leverage / price
        notional = float(margin_usdt) * int(leverage)
        amount = notional / float(entry_price)

        # 3. Create Market Buy (Long) Order
        order = real_ex.create_market_buy_order(clean_sym, amount)
        order_id = order.get('id')
        print(f"[REAL TRADE EXECUTED] {clean_sym} Long Market Order ID: {order_id} | Amount: {amount:.4f}")

        # 4. Attach Take Profit & Stop Loss Orders
        try:
            real_ex.create_order(clean_sym, 'TAKE_PROFIT_MARKET', 'sell', amount, None, {'stopPrice': tp_price, 'reduceOnly': True})
            real_ex.create_order(clean_sym, 'STOP_MARKET', 'sell', amount, None, {'stopPrice': sl_price, 'reduceOnly': True})
        except Exception as e_ord:
            print(f"[REAL TRADE WARN] Could not attach TP/SL bracket orders on Binance: {e_ord}")

        return order_id

    except Exception as e:
        print(f"[REAL TRADE FAILED] Failed to place order on Binance for {symbol}: {e}")
        return None

def monitor_open_trades():
    open_trades = database.get_open_trades()
    if not open_trades:
        return

    send_close_alert = database.get_setting('send_telegram_trade_close', 'true').lower() == 'true'

    for trade in open_trades:
        symbol = trade['symbol']
        trade_id = trade['id']
        entry_price = trade['entry_price']
        tp_price = trade['tp_price']
        sl_price = trade['sl_price']
        hold_mins = trade['hold_time_mins']

        try:
            entry_ms = trade.get('entry_timestamp_ms')
            if not entry_ms:
                dt = datetime.strptime(trade['entry_time'], '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
                entry_ms = int(dt.timestamp() * 1000)

            # Fetch 1m candles starting slightly before entry_ms
            ohlcv = public_exchange.fetch_ohlcv(symbol, timeframe='1m', since=entry_ms - 60000)
            if not ohlcv:
                continue

            # Strict filter: ONLY evaluate candles from trade entry minute onward
            valid_candles = [c for c in ohlcv if c[0] >= (entry_ms - 30000)]
            if not valid_candles:
                continue

            max_price_so_far = trade['max_price_reached'] or entry_price
            min_price_so_far = trade['min_price_reached'] or entry_price

            hit_tp = False
            hit_sl = False
            exit_price = None

            for candle in valid_candles:
                c_high = candle[2]
                c_low = candle[3]

                max_price_so_far = max(max_price_so_far, c_high)
                min_price_so_far = min(min_price_so_far, c_low)

                # Check if this candle hit TP or SL
                if c_high >= tp_price:
                    hit_tp = True
                    exit_price = tp_price
                    break
                elif c_low <= sl_price:
                    hit_sl = True
                    exit_price = sl_price
                    break

            latest_close = valid_candles[-1][4]
            max_pump_pct = ((max_price_so_far - entry_price) / entry_price) * 100.0

            # Update max/min extremes in DB
            database.update_open_trade_extremes(trade_id, max_price_so_far, min_price_so_far, max_pump_pct)

            margin = trade.get('margin_usdt', 20.0) or 20.0
            lev = trade.get('leverage', 5) or 5
            exec_mode = trade.get('execution_mode', 'PAPER')

            # 1. Check Take Profit Hit
            if hit_tp:
                tp_pct_val = float(database.get_setting('tp_pct', '2.0'))
                pnl_usdt = round(margin * lev * (tp_pct_val / 100.0), 2)

                database.close_trade(trade_id, 'WON', exit_price, tp_pct_val, max_price_so_far, max_pump_pct)
                print(f"[TRADE WON] [{exec_mode}] {symbol} hit TP target at ${exit_price:.6f} (+{tp_pct_val}%, +${pnl_usdt})")
                if send_close_alert:
                    clean_sym = symbol.split('/')[0] + "USDT"
                    send_telegram_alert(
                        f"🎯 *TRADE CLOSED [WIN] • {clean_sym}* `[{exec_mode}]`\n\n"
                        f"Status: *TAKE PROFIT HIT* 🚀\n"
                        f"Entry: `${entry_price:.6f}` | Exit: `${exit_price:.6f}`\n"
                        f"PnL: `+{tp_pct_val:.2f}%` (`+${pnl_usdt:.2f} USDT`)\n"
                        f"Max Pump: `+{max_pump_pct:.2f}%` | Margin: `${margin:.0f} @ {lev}x`"
                    )
                continue

            # 2. Check Stop Loss Hit
            if hit_sl:
                sl_pct_val = float(database.get_setting('sl_pct', '1.0'))
                pnl_pct = -abs(sl_pct_val)
                pnl_usdt = round(margin * lev * (pnl_pct / 100.0), 2)

                database.close_trade(trade_id, 'LOST', exit_price, pnl_pct, max_price_so_far, max_pump_pct)
                print(f"[TRADE LOST] [{exec_mode}] {symbol} hit SL target at ${exit_price:.6f} ({pnl_pct}%, ${pnl_usdt})")
                if send_close_alert:
                    clean_sym = symbol.split('/')[0] + "USDT"
                    send_telegram_alert(
                        f"🔻 *TRADE CLOSED [LOSS] • {clean_sym}* `[{exec_mode}]`\n\n"
                        f"Status: *STOP LOSS HIT* 🛑\n"
                        f"Entry: `${entry_price:.6f}` | Exit: `${exit_price:.6f}`\n"
                        f"PnL: `{pnl_pct:.2f}%` (`${pnl_usdt:.2f} USDT`)\n"
                        f"Max Pump: `+{max_pump_pct:.2f}%` | Margin: `${margin:.0f} @ {lev}x`"
                    )
                continue

            # 3. Check Expiry
            now_ms = int(time.time() * 1000)
            elapsed_mins = (now_ms - entry_ms) / 60000.0
            if elapsed_mins >= hold_mins:
                pnl_pct = ((latest_close - entry_price) / entry_price) * 100.0
                pnl_usdt = round(margin * lev * (pnl_pct / 100.0), 2)
                status = 'WON' if pnl_pct > 0 else ('LOST' if pnl_pct < 0 else 'EXPIRED')

                database.close_trade(trade_id, status, latest_close, round(pnl_pct, 2), max_price_so_far, max_pump_pct)
                print(f"[TRADE EXPIRED] [{exec_mode}] {symbol} closed at hold limit ({hold_mins}m): PnL {pnl_pct:.2f}% (${pnl_usdt})")
                if send_close_alert:
                    clean_sym = symbol.split('/')[0] + "USDT"
                    send_telegram_alert(
                        f"⏱️ *TRADE CLOSED [{status}] • {clean_sym}* `[{exec_mode}]`\n\n"
                        f"Status: *{status} (TIME LIMIT)*\n"
                        f"Entry: `${entry_price:.6f}` | Exit: `${latest_close:.6f}`\n"
                        f"PnL: `{pnl_pct:+.2f}%` (`{pnl_usdt:+.2f} USDT`)\n"
                        f"Max Pump: `+{max_pump_pct:.2f}%` | Margin: `${margin:.0f} @ {lev}x`"
                    )

        except Exception as e:
            print(f"Error updating trade {trade_id} ({symbol}): {e}")

def scan_binance_market():
    min_spike = float(database.get_setting('min_spike_multiplier', '5.0'))
    coins = get_binance_top_coins(TOP_N_COINS)
    if not coins:
        return

    # Check Max Open Trades Limit
    max_open = int(database.get_setting('max_open_trades', '3'))
    current_open_trades = database.get_open_trades()

    for symbol in coins:
        try:
            # Re-check open trade limit dynamically
            if len(database.get_open_trades()) >= max_open:
                break

            time.sleep(0.1)
            ohlcv = public_exchange.fetch_ohlcv(symbol, timeframe=TIMEFRAME, limit=MA_PERIOD + 1)
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

            if vol_spike >= min_spike and price_change > 0.2:
                score, stars, status_label = calculate_score_and_stars(vol_spike)
                clean_symbol = symbol.split('/')[0] + "USDT"

                # Fetch strategy parameters
                tp_pct = float(database.get_setting('tp_pct', '2.0'))
                sl_pct = float(database.get_setting('sl_pct', '1.0'))
                hold_mins = int(database.get_setting('hold_time_mins', '30'))
                margin_usdt = float(database.get_setting('trade_margin', '20.0'))
                leverage = int(database.get_setting('trade_leverage', '5'))
                exec_mode = database.get_setting('execution_mode', 'PAPER').upper()

                # 1. Save signal to DB
                signal_id = database.add_signal(
                    symbol=symbol,
                    entry_price=current_price,
                    vol_spike=round(vol_spike, 1),
                    price_change_pct=round(price_change, 2),
                    score=score,
                    stars=stars,
                    status_label=status_label,
                    avg_vol_usdt=avg_vol_usdt,
                    current_vol_usdt=current_vol_usdt
                )

                real_order_id = None

                # 2. Execute on Binance if mode is REAL
                if exec_mode == 'REAL':
                    tp_price = current_price * (1 + tp_pct / 100.0)
                    sl_price = current_price * (1 - sl_pct / 100.0)
                    real_order_id = execute_trade_on_binance_real(symbol, current_price, tp_price, sl_price, margin_usdt, leverage)

                # 3. Save trade entry to DB
                database.create_trade(
                    signal_id=signal_id,
                    symbol=symbol,
                    entry_price=current_price,
                    tp_pct=tp_pct,
                    sl_pct=sl_pct,
                    hold_time_mins=hold_mins,
                    margin_usdt=margin_usdt,
                    leverage=leverage,
                    execution_mode=exec_mode,
                    real_order_id=real_order_id
                )

                # 4. Send Telegram Pre-Pump Signal Alert
                star_str = "⭐" * stars
                mode_badge = "🟢 *PAPER DEMO*" if exec_mode == 'PAPER' else "⚡ *REAL BINANCE LIVE*"

                telegram_msg = (
                    f"🚨 *PRE-PUMP ALERT • {clean_symbol}* `[{TIMEFRAME}]`\n"
                    f"💥 *{status_label}*\n"
                    f"Mode: {mode_badge}\n\n"
                    f"Stars: {star_str}\n"
                    f"Entry: `${current_price:.6f}`\n"
                    f"Volume Spike: *{vol_spike:.1f}x avg*\n"
                    f"Margin & Leverage: `${margin_usdt:.0f} @ {leverage}x`\n"
                    f"Target TP: `+{tp_pct}%` | SL: `-{sl_pct}%`\n\n"
                    f"📈 *Signal Score:* `{score}/10`\n"
                    f"----------------------------------------"
                )

                print(f"[SPIKE FOUND] [{exec_mode}] {clean_symbol} - Spike: {vol_spike:.1f}x (Margin: ${margin_usdt} @ {leverage}x)")
                send_telegram_alert(telegram_msg)

        except Exception:
            time.sleep(0.5)
            continue

    # After market scan, monitor all active open trades
    monitor_open_trades()

def keep_alive_worker():
    """Background worker to ping Render server every 10 minutes to prevent sleep."""
    port = int(os.environ.get("PORT", 10000))
    local_url = f"http://127.0.0.1:{port}/ping"
    
    while True:
        time.sleep(600)  # Every 10 minutes
        try:
            if RENDER_EXTERNAL_URL:
                requests.get(f"{RENDER_EXTERNAL_URL.rstrip('/')}/ping", timeout=10)
            requests.get(local_url, timeout=10)
            print("[KEEP-ALIVE] Render server ping successful.")
        except Exception as e:
            print(f"[KEEP-ALIVE] Ping failed: {e}")

def background_loop():
    exec_mode = database.get_setting('execution_mode', 'PAPER')
    startup_msg = (
        "🚀 *Binance Pre-Pump Scanner & Overview System Started!*\n\n"
        f"• *Execution Mode:* `{exec_mode}`\n"
        f"• *Timeframe:* `{TIMEFRAME}`\n"
        f"• *Scan Scope:* Top `{TOP_N_COINS}` Binance Pairs\n"
        f"• *Min Spike Trigger:* `{database.get_setting('min_spike_multiplier', '5.0')}x`\n"
        f"• *Margin / Leverage:* `${database.get_setting('trade_margin', '20.0')} @ {database.get_setting('trade_leverage', '5')}x`\n"
        f"• *Dashboard:* `{RENDER_EXTERNAL_URL}`\n\n"
        "🟢 Scanner & 24/7 Execution Tracking is active..."
    )
    send_telegram_alert(startup_msg)

    # Start keep alive thread
    threading.Thread(target=keep_alive_worker, daemon=True).start()

    while True:
        try:
            scan_binance_market()
        except Exception as e:
            print(f"Scanner Loop Error: {e}")
        time.sleep(60)

if __name__ == "__main__":
    # Start scanner loop in a background thread
    threading.Thread(target=background_loop, daemon=True).start()

    # Run Flask Web Application
    port = int(os.environ.get("PORT", 10000))
    print(f"Starting Overview Dashboard Server on port {port}...")
    app.run(host='0.0.0.0', port=port)
