# main.py - Full Empire Trading Signal Bot with AI Parsing, Database, and Simple Logging
# Cleaned and simplified version - English logs, no Hindi, bulletproof error handling
# Date: November 12, 2025
# Features:
# - Loads config from JSON
# - SQLite database for signal storage
# - Telegram client with Telethon
# - AI parser with Ollama + Llama3 fallback to regex
# - Simple English logging
# - Simple format message with takeprofit and channel link
# - 24/7 ready for Replit/PythonAnywhere/Render

# Imports
from telethon import TelegramClient, events
from telethon.errors import SessionPasswordNeededError
import sqlite3
import re
import json
import os
import subprocess
from datetime import datetime, timedelta
from collections import defaultdict
import logging
import asyncio

# Logging setup - Simple English
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logging.info("Bot starting - Empire Mode On")

# Load Config
try:
    with open("config.json", "r") as f:
        config = json.load(f)
    logging.info("Config Loaded Successfully")
except FileNotFoundError:
    logging.error("Config.json missing! Bot stopping.")
    exit()
except json.JSONDecodeError:
    logging.error("Config.json invalid JSON! Bot stopping.")
    exit()

# Extract config values with defaults
api_id = config.get("api_id")
api_hash = config.get("api_hash")
phone = config.get("phone")
source_channels = config.get("channels", [])
main_group = config.get("main_group")
lot_size = config.get("lot_size", 0.25)
risk_percent = config.get("risk_percent", 1)
demo_balance = config.get("demo_balance", 1000)

if not all([api_id, api_hash, phone, source_channels, main_group]):
    logging.error("Config missing key values! Bot stopping.")
    exit()

# Database Setup
conn = sqlite3.connect('signals.db', check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''
CREATE TABLE IF NOT EXISTS signals
(id INTEGER PRIMARY KEY AUTOINCREMENT,
 channel TEXT NOT NULL,
 direction TEXT NOT NULL,
 entry REAL NOT NULL,
 sl REAL NOT NULL,
 tp1 REAL,
 tp2 REAL,
 tp3 REAL,
 tp4 REAL,
 time TEXT NOT NULL,
 hit_tp1 TEXT DEFAULT 'OPEN',
 hit_tp2 TEXT DEFAULT 'OPEN',
 hit_tp3 TEXT DEFAULT 'OPEN',
 hit_tp4 TEXT DEFAULT 'OPEN',
 profit REAL DEFAULT 0,
 notes TEXT
)
''')
cursor.execute('CREATE INDEX IF NOT EXISTS idx_channel ON signals (channel)')
cursor.execute('CREATE INDEX IF NOT EXISTS idx_time ON signals (time)')
conn.commit()
logging.info("Database ready")

# Telegram Client Setup
client = TelegramClient('bot_session', api_id, api_hash)

async def login():
    try:
        await client.start(phone=phone)
        if not await client.is_user_authorized():
            await client.send_code_request(phone)
            code = input("Enter Telegram code: ")
            try:
                await client.sign_in(phone, code)
            except SessionPasswordNeededError:
                password = input("Enter 2FA password: ")
                await client.sign_in(password=password)
        logging.info("Telegram Login Success")
    except Exception as e:
        logging.error(f"Login Error: {e}")
        exit()

# AI Parser with Fallback Regex
def ai_parse_signal(text):
    prompt = f'''
    Parse this text to JSON only. No extra words or text:
    {{
      "direction": "BUY" or "SELL",
      "pair": "XAUUSD" or "BTC" or other,
      "entry": number (average if zone),
      "sl": number,
      "tp1": number or null,
      "tp2": number or null,
      "tp3": number or null,
      "tp4": number or null
    }}
    Text: "{text}"
    '''
    try:
        result = subprocess.run(['ollama', 'run', 'llama3'], input=prompt, text=True, capture_output=True, timeout=60)
        output = result.stdout.strip()
        logging.info(f"[AI RAW]: {output}")
        json_match = re.search(r'\{.*\}', output, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group())
                logging.info(f"[AI PARSED]: {data}")
                return data
            except json.JSONDecodeError as e:
                logging.error(f"AI JSON Error: {e}")
    except Exception as e:
        logging.error(f"AI Run Error: {e}")

    # Fallback Regex Parser
    logging.info("[AI FAILED] Using Regex Fallback")
    text_up = text.upper()
    direction = "BUY" if "BUY" in text_up or "LONG" in text_up else "SELL" if "SELL" in text_up or "SHORT" in text_up else None
    if not direction:
        logging.error("No direction found — Skip")
        return None

    # Pair
    pair_match = re.search(r'(XAUUSD|GOLD|BTC|EURUSD|GBPUSD|USDJPY)', text_up)
    pair = pair_match.group(1) if pair_match else "XAUUSD"

    # Numbers extract
    numbers = [float(x) for x in re.findall(r'\d+\.?\d*', text)]
    if len(numbers) < 2:
        logging.error("Not enough numbers — Skip")
        return None

    # Entry (zone handling)
    entry = numbers[0]
    zone_match = re.search(r'(\d+\.?\d*)\s*[-]?\s*(\d+\.?\d*)', text)
    if zone_match and zone_match.group(2):
        a, b = float(zone_match.group(1)), float(zone_match.group(2))
        entry = (a + b) / 2

    # SL (near "SL" or "STOP")
    sl_index = next((i for i, w in enumerate(re.split(r'\s+', text_up)) if w in ['SL', 'STOP', 'STOPLOSS']), None)
    sl = numbers[sl_index + 1] if sl_index is not None and sl_index + 1 < len(numbers) else numbers[1]

    # TP1, TP2, TP3, TP4 (next numbers)
    tp_start = 2
    tp1 = numbers[tp_start] if len(numbers) > tp_start else None
    tp2 = numbers[tp_start + 1] if len(numbers) > tp_start + 1 else None
    tp3 = numbers[tp_start + 2] if len(numbers) > tp_start + 2 else None
    tp4 = numbers[tp_start + 3] if len(numbers) > 3 + tp_start else None

    result = {
        "direction": direction,
        "pair": pair,
        "entry": round(entry, 1),
        "sl": round(sl, 1),
        "tp1": round(tp1, 1) if tp1 else None,
        "tp2": round(tp2, 1) if tp2 else None,
        "tp3": round(tp3, 1) if tp3 else None,
        "tp4": round(tp4, 1) if tp4 else None
    }
    logging.info(f"[REGEX PARSED]: {result}")
    return result

# Handler for signals
@client.on(events.NewMessage(chats=source_channels))
async def handler(event):
    try:
        text = event.message.message or ""
        logging.info(f"[RAW TEXT]: {text}")
        chat = await event.get_chat()
        channel_name = chat.title or "Unknown"
        signal = ai_parse_signal(text)
        if signal:
            # Save to DB
            conn.execute("""
            INSERT INTO signals (channel, direction, entry, sl, tp1, tp2, tp3, tp4, time, hit_tp1, hit_tp2, hit_tp3, hit_tp4, profit)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', 'OPEN', 'OPEN', 'OPEN', 0)
            """, (channel_name, signal['direction'], signal['entry'], signal['sl'],
                  signal.get('tp1'), signal.get('tp2'), signal.get('tp3'), signal.get('tp4'),
                  datetime.now().strftime("%Y-%m-%d %H:%M")))
            conn.commit()

            # Simple Format
            tp_lines = ""
            if signal.get('tp1'): tp_lines += f"Take Profit 1: `{signal['tp1']}`\n"
            if signal.get('tp2'): tp_lines += f"Take Profit 2: `{signal['tp2']}`\n"
            if signal.get('tp3'): tp_lines += f"Take Profit 3: `{signal['tp3']}`\n"
            if signal.get('tp4'): tp_lines += f"Take Profit 4: `{signal['tp4']}`\n"

            # Channel Link
            channel_username = chat.username if chat.username else f"c/{abs(chat.id)}"
            message_id = event.message.id
            link = f"https://t.me/{channel_username}/{message_id}"

            msg = f"""
**{channel_name} SIGNAL**

**{signal['direction'].upper()} {signal['pair']}**
Entry: `{signal['entry']}`
SL: `{signal['sl']}`

{tp_lines}
Check Original: [Click Here]({link})
            """
            await client.send_message(main_group, msg, parse_mode='md')
            logging.info("Signal sent")
        else:
            logging.info("No signal found — Skip")
    except Exception as e:
        logging.error(f"Handler Error: {e}")

# Main function
async def main():
    await login()
    logging.info("Bot live - Empire Mode On")
    await client.run_until_disconnected()

if __name__ == '__main__':
    asyncio.run(main())

