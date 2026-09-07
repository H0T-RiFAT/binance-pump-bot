import sqlite3
import os
import time
from datetime import datetime, timedelta, timezone

DB_PATH = os.path.join(os.path.dirname(__file__), 'database.db')

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    # Signals table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            entry_price REAL NOT NULL,
            vol_spike REAL NOT NULL,
            price_change_pct REAL NOT NULL,
            avg_vol_usdt REAL,
            current_vol_usdt REAL,
            score INTEGER,
            stars INTEGER,
            status_label TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            timestamp_ms INTEGER
        )
    ''')

    # Trades table (Executed / Simulated Paper Trades & Real Trades)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id INTEGER,
            symbol TEXT NOT NULL,
            entry_price REAL NOT NULL,
            exit_price REAL DEFAULT NULL,
            tp_price REAL NOT NULL,
            sl_price REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'OPEN',
            pnl_pct REAL DEFAULT 0.0,
            pnl_usdt REAL DEFAULT 0.0,
            margin_usdt REAL DEFAULT 20.0,
            leverage INTEGER DEFAULT 5,
            execution_mode TEXT DEFAULT 'PAPER',
            real_order_id TEXT DEFAULT NULL,
            max_price_reached REAL,
            min_price_reached REAL,
            max_pump_pct REAL DEFAULT 0.0,
            entry_time DATETIME DEFAULT CURRENT_TIMESTAMP,
            entry_timestamp_ms INTEGER,
            exit_time DATETIME DEFAULT NULL,
            exit_timestamp_ms INTEGER,
            hold_time_mins INTEGER DEFAULT 30,
            FOREIGN KEY(signal_id) REFERENCES signals(id)
        )
    ''')

    # Migration check for new columns if DB exists
    cursor.execute("PRAGMA table_info(signals)")
    cols = [row['name'] for row in cursor.fetchall()]
    if 'timestamp_ms' not in cols:
        cursor.execute("ALTER TABLE signals ADD COLUMN timestamp_ms INTEGER")

    cursor.execute("PRAGMA table_info(trades)")
    t_cols = [row['name'] for row in cursor.fetchall()]
    if 'entry_timestamp_ms' not in t_cols:
        cursor.execute("ALTER TABLE trades ADD COLUMN entry_timestamp_ms INTEGER")
    if 'exit_timestamp_ms' not in t_cols:
        cursor.execute("ALTER TABLE trades ADD COLUMN exit_timestamp_ms INTEGER")
    if 'margin_usdt' not in t_cols:
        cursor.execute("ALTER TABLE trades ADD COLUMN margin_usdt REAL DEFAULT 20.0")
    if 'leverage' not in t_cols:
        cursor.execute("ALTER TABLE trades ADD COLUMN leverage INTEGER DEFAULT 5")
    if 'pnl_usdt' not in t_cols:
        cursor.execute("ALTER TABLE trades ADD COLUMN pnl_usdt REAL DEFAULT 0.0")
    if 'execution_mode' not in t_cols:
        cursor.execute("ALTER TABLE trades ADD COLUMN execution_mode TEXT DEFAULT 'PAPER'")
    if 'real_order_id' not in t_cols:
        cursor.execute("ALTER TABLE trades ADD COLUMN real_order_id TEXT DEFAULT NULL")

    # Settings table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    ''')

    # Insert default settings if not exists
    default_settings = {
        'tp_pct': '2.0',
        'sl_pct': '1.0',
        'hold_time_mins': '30',
        'min_spike_multiplier': '5.0',
        'send_telegram_trade_close': 'true',
        'execution_mode': 'PAPER',
        'paper_balance': '1000.0',
        'paper_initial_capital': '1000.0',
        'trade_margin': '20.0',
        'trade_leverage': '5',
        'max_open_trades': '3',
        'binance_api_key': '',
        'binance_api_secret': ''
    }

    for key, val in default_settings.items():
        cursor.execute('INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)', (key, val))

    conn.commit()
    conn.close()

def get_setting(key, default=None):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT value FROM settings WHERE key = ?', (key,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return row['value']
    return default

def set_setting(key, value):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', (key, str(value)))
    conn.commit()
    conn.close()

def get_all_settings():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT key, value FROM settings')
    rows = cursor.fetchall()
    conn.close()
    settings = {row['key']: row['value'] for row in rows}
    # Mask API Secret for security when returning settings
    if settings.get('binance_api_secret'):
        settings['binance_api_secret_masked'] = '*' * (len(settings['binance_api_secret']) - 4) + settings['binance_api_secret'][-4:]
    return settings

def add_signal(symbol, entry_price, vol_spike, price_change_pct, score, stars, status_label, avg_vol_usdt=0, current_vol_usdt=0):
    conn = get_connection()
    cursor = conn.cursor()
    now_ms = int(time.time() * 1000)
    now_utc_str = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

    cursor.execute('''
        INSERT INTO signals (symbol, entry_price, vol_spike, price_change_pct, score, stars, status_label, avg_vol_usdt, current_vol_usdt, timestamp, timestamp_ms)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (symbol, entry_price, vol_spike, price_change_pct, score, stars, status_label, avg_vol_usdt, current_vol_usdt, now_utc_str, now_ms))
    signal_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return signal_id

def create_trade(signal_id, symbol, entry_price, tp_pct=2.0, sl_pct=1.0, hold_time_mins=30, margin_usdt=20.0, leverage=5, execution_mode='PAPER', real_order_id=None):
    tp_price = entry_price * (1 + tp_pct / 100.0)
    sl_price = entry_price * (1 - sl_pct / 100.0)
    now_ms = int(time.time() * 1000)
    now_utc_str = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO trades (signal_id, symbol, entry_price, tp_price, sl_price, status, max_price_reached, min_price_reached, hold_time_mins, margin_usdt, leverage, execution_mode, real_order_id, entry_time, entry_timestamp_ms)
        VALUES (?, ?, ?, ?, ?, 'OPEN', ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (signal_id, symbol, entry_price, tp_price, sl_price, entry_price, entry_price, hold_time_mins, margin_usdt, leverage, execution_mode, real_order_id, now_utc_str, now_ms))
    trade_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return trade_id

def get_open_trades():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM trades WHERE status = "OPEN"')
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def close_trade(trade_id, status, exit_price, pnl_pct, max_price_reached, max_pump_pct):
    conn = get_connection()
    cursor = conn.cursor()
    now_ms = int(time.time() * 1000)
    now_utc_str = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')

    # Fetch trade details to calculate USDT PnL and update paper balance
    cursor.execute('SELECT * FROM trades WHERE id = ?', (trade_id,))
    trade = cursor.fetchone()

    pnl_usdt = 0.0
    if trade:
        margin = trade['margin_usdt'] or 20.0
        lev = trade['leverage'] or 5
        # Return USDT = margin * leverage * (pnl_pct / 100)
        pnl_usdt = round(margin * lev * (pnl_pct / 100.0), 2)
        exec_mode = trade['execution_mode'] or 'PAPER'

        # If PAPER mode, update paper_balance in settings
        if exec_mode == 'PAPER':
            cursor.execute('SELECT value FROM settings WHERE key = "paper_balance"')
            row = cursor.fetchone()
            curr_bal = float(row['value']) if row else 1000.0
            new_bal = round(curr_bal + pnl_usdt, 2)
            cursor.execute('INSERT OR REPLACE INTO settings (key, value) VALUES ("paper_balance", ?)', (str(new_bal),))

    cursor.execute('''
        UPDATE trades 
        SET status = ?, exit_price = ?, pnl_pct = ?, pnl_usdt = ?, exit_time = ?, exit_timestamp_ms = ?, max_price_reached = ?, max_pump_pct = ?
        WHERE id = ?
    ''', (status, exit_price, pnl_pct, pnl_usdt, now_utc_str, now_ms, max_price_reached, max_pump_pct, trade_id))
    conn.commit()
    conn.close()

def update_open_trade_extremes(trade_id, max_price_reached, min_price_reached, max_pump_pct):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        UPDATE trades 
        SET max_price_reached = ?, min_price_reached = ?, max_pump_pct = ?
        WHERE id = ?
    ''', (max_price_reached, min_price_reached, max_pump_pct, trade_id))
    conn.commit()
    conn.close()

def get_recent_signals(limit=50):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM signals ORDER BY id DESC LIMIT ?', (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def get_trade_history(limit=100):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        SELECT t.*, s.vol_spike, s.score, s.stars, s.status_label 
        FROM trades t
        LEFT JOIN signals s ON t.signal_id = s.id
        ORDER BY t.id DESC LIMIT ?
    ''', (limit,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def get_dashboard_stats(days=30):
    conn = get_connection()
    cursor = conn.cursor()

    cutoff_dt = datetime.now(timezone.utc) - timedelta(days=days)
    cutoff_str = cutoff_dt.strftime('%Y-%m-%d %H:%M:%S')
    cutoff_ms = int(cutoff_dt.timestamp() * 1000)

    # Total Signals
    cursor.execute('SELECT COUNT(*) as count FROM signals WHERE timestamp_ms >= ? OR timestamp >= ?', (cutoff_ms, cutoff_str))
    total_signals = cursor.fetchone()['count']

    # Trades stats
    cursor.execute('SELECT * FROM trades WHERE entry_timestamp_ms >= ? OR entry_time >= ?', (cutoff_ms, cutoff_str))
    trades = [dict(row) for row in cursor.fetchall()]

    total_trades = len(trades)
    wins = len([t for t in trades if t['status'] == 'WON'])
    losses = len([t for t in trades if t['status'] == 'LOST'])
    expired = len([t for t in trades if t['status'] == 'EXPIRED'])
    open_trades_count = len([t for t in trades if t['status'] == 'OPEN'])

    closed_trades = [t for t in trades if t['status'] in ('WON', 'LOST', 'EXPIRED')]
    closed_count = len(closed_trades)

    win_rate = (wins / closed_count * 100.0) if closed_count > 0 else 0.0
    total_pnl_pct = sum(t['pnl_pct'] for t in closed_trades)
    total_pnl_usdt = sum(t.get('pnl_usdt', 0.0) or 0.0 for t in closed_trades)

    win_pnls = [t['pnl_pct'] for t in closed_trades if t['status'] == 'WON']
    loss_pnls = [t['pnl_pct'] for t in closed_trades if t['status'] in ('LOST', 'EXPIRED') and t['pnl_pct'] <= 0]

    gross_profit_usdt = sum(t['pnl_usdt'] for t in closed_trades if (t['pnl_usdt'] or 0.0) > 0)
    gross_loss_usdt = abs(sum(t['pnl_usdt'] for t in closed_trades if (t['pnl_usdt'] or 0.0) < 0))

    profit_factor = (gross_profit_usdt / gross_loss_usdt) if gross_loss_usdt > 0 else (gross_profit_usdt if gross_profit_usdt > 0 else 1.0)

    avg_win = (sum(win_pnls) / len(win_pnls)) if win_pnls else 0.0
    avg_loss = (sum(loss_pnls) / len(loss_pnls)) if loss_pnls else 0.0
    
    best_trade = max([t['pnl_pct'] for t in closed_trades], default=0.0)
    worst_trade = min([t['pnl_pct'] for t in closed_trades], default=0.0)

    # Balance and Max Drawdown calculation
    cursor.execute('SELECT value FROM settings WHERE key = "paper_initial_capital"')
    init_cap_row = cursor.fetchone()
    initial_capital = float(init_cap_row['value']) if init_cap_row else 1000.0

    cursor.execute('SELECT value FROM settings WHERE key = "paper_balance"')
    paper_bal_row = cursor.fetchone()
    current_paper_balance = float(paper_bal_row['value']) if paper_bal_row else 1000.0

    # Calculate Max Drawdown % (MDD) across closed trade balance curve
    running_balance = initial_capital
    peak_balance = initial_capital
    max_drawdown_pct = 0.0

    for t in sorted(closed_trades, key=lambda x: x['id']):
        running_balance += (t.get('pnl_usdt') or 0.0)
        if running_balance > peak_balance:
            peak_balance = running_balance
        dd = ((peak_balance - running_balance) / peak_balance) * 100.0 if peak_balance > 0 else 0.0
        if dd > max_drawdown_pct:
            max_drawdown_pct = dd

    # 30-Day Daily Chart Data Generation
    pnl_by_date = {}
    pnl_usdt_by_date = {}
    signals_by_date = {}
    wins_by_date = {}
    losses_by_date = {}

    for d in range(days, -1, -1):
        dt_str = (datetime.now(timezone.utc) - timedelta(days=d)).strftime('%Y-%m-%d')
        pnl_by_date[dt_str] = 0.0
        pnl_usdt_by_date[dt_str] = 0.0
        signals_by_date[dt_str] = 0
        wins_by_date[dt_str] = 0
        losses_by_date[dt_str] = 0

    cursor.execute('''
        SELECT DATE(timestamp) as date_str, COUNT(*) as cnt 
        FROM signals 
        WHERE timestamp >= ? OR timestamp_ms >= ?
        GROUP BY DATE(timestamp)
    ''', (cutoff_str, cutoff_ms))
    for row in cursor.fetchall():
        if row['date_str'] and row['date_str'] in signals_by_date:
            signals_by_date[row['date_str']] = row['cnt']

    for t in closed_trades:
        if t['exit_time']:
            date_str = t['exit_time'][:10]
            if date_str in pnl_by_date:
                pnl_by_date[date_str] += t['pnl_pct']
                pnl_usdt_by_date[date_str] += (t.get('pnl_usdt') or 0.0)
                if t['status'] == 'WON':
                    wins_by_date[date_str] += 1
                elif t['status'] in ('LOST', 'EXPIRED'):
                    losses_by_date[date_str] += 1

    chart_dates = sorted(pnl_by_date.keys())
    chart_pnl_daily = [round(pnl_by_date[d], 2) for d in chart_dates]
    chart_pnl_usdt_daily = [round(pnl_usdt_by_date[d], 2) for d in chart_dates]
    
    cum_pct = 0.0
    cum_usdt = 0.0
    chart_pnl_cum = []
    chart_pnl_usdt_cum = []
    chart_balance_history = []
    
    balance_acc = initial_capital
    for idx, d in enumerate(chart_dates):
        cum_pct += chart_pnl_daily[idx]
        cum_usdt += chart_pnl_usdt_daily[idx]
        balance_acc += chart_pnl_usdt_daily[idx]
        
        chart_pnl_cum.append(round(cum_pct, 2))
        chart_pnl_usdt_cum.append(round(cum_usdt, 2))
        chart_balance_history.append(round(balance_acc, 2))

    chart_signals = [signals_by_date[d] for d in chart_dates]
    chart_wins = [wins_by_date[d] for d in chart_dates]
    chart_losses = [losses_by_date[d] for d in chart_dates]

    conn.close()

    return {
        'total_signals': total_signals,
        'total_trades': total_trades,
        'wins': wins,
        'losses': losses,
        'expired': expired,
        'open_trades_count': open_trades_count,
        'closed_count': closed_count,
        'win_rate': round(win_rate, 1),
        'total_pnl_pct': round(total_pnl_pct, 2),
        'total_pnl_usdt': round(total_pnl_usdt, 2),
        'initial_capital': round(initial_capital, 2),
        'paper_balance': round(current_paper_balance, 2),
        'profit_factor': round(profit_factor, 2),
        'max_drawdown_pct': round(max_drawdown_pct, 2),
        'avg_win': round(avg_win, 2),
        'avg_loss': round(avg_loss, 2),
        'best_trade': round(best_trade, 2),
        'worst_trade': round(worst_trade, 2),
        'chart_dates': chart_dates,
        'chart_pnl_daily': chart_pnl_daily,
        'chart_pnl_cum': chart_pnl_cum,
        'chart_pnl_usdt_daily': chart_pnl_usdt_daily,
        'chart_pnl_usdt_cum': chart_pnl_usdt_cum,
        'chart_balance_history': chart_balance_history,
        'chart_signals': chart_signals,
        'chart_wins': chart_wins,
        'chart_losses': chart_losses
    }

if __name__ == '__main__':
    init_db()
    print("Database initialized & updated for Dual-Mode & Portfolio Analytics!")
