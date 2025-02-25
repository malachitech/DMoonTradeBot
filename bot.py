import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Retrieve the bot token from the environment variable
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Check if the token is correctly loaded
if not TOKEN or TOKEN.startswith("%") or TOKEN == "YOUR_BOT_TOKEN":
    raise ValueError("Invalid or missing TELEGRAM_BOT_TOKEN in .env file")


import logging
import re
import requests
import asyncio
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext

# Set up logging
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

# Load sensitive information from environment variables
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_WALLET = os.getenv("ADMIN_WALLET_ADDRESS")
DEX_API_URL = "https://api.pancakeswap.info/api/v2/tokens/"  # Example API for price lookup
PUMPFUN_API_URL = "https://pump.fun/api/v1/token/"
RAYDIUM_API_URL = "https://api.raydium.io/pairs"

# Trade tracking
user_trades = {}  # Stores active trades
user_sell_targets = {}  # Stores user-defined sell targets
collected_fees = 0  # Track collected fees

def main():
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_group_link))
    app.add_handler(CommandHandler("active_trades", check_active_trades))
    app.add_handler(CommandHandler("total_fees", show_collected_fees))
    app.add_handler(CommandHandler("set_target", set_target))  # New command for setting sell target
    
    logging.info("Bot is running...")
    
    loop = asyncio.get_event_loop()
    loop.create_task(monitor_prices())  # Start monitoring prices in the background
    
    app.run_polling()

async def start(update: Update, context: CallbackContext):
    await update.message.reply_text("Welcome to the Auto Trading Bot! Send a Telegram group link to start trading.")

async def receive_group_link(update: Update, context: CallbackContext):
    group_link = update.message.text
    await update.message.reply_text(f"Received group link: {group_link}\nMonitoring for token launch...")
    
    contract_address, platform = extract_contract_address(group_link)
    if contract_address:
        await update.message.reply_text(f"Detected contract address: {contract_address} on {platform}\nInitiating trade...")
        execute_trade(update.message.chat_id, contract_address, platform)
    else:
        await update.message.reply_text("No contract address found yet. Monitoring continues...")

def extract_contract_address(message: str):
    match = re.search(r'0x[a-fA-F0-9]{40}', message)  # Ethereum-style contract
    if match:
        return match.group(0), "Ethereum"
    
    match = re.search(r'[A-Za-z0-9]{32,44}', message)  # Solana contract detection
    if match:
        if "pump.fun" in message:
            return match.group(0), "Pump.fun"
        elif "raydium.io" in message:
            return match.group(0), "Raydium"
    
    return None, None

def execute_trade(user_id, contract_address, platform):
    global collected_fees
    buy_price = get_token_price(contract_address, platform)
    if not buy_price:
        logging.error(f"Failed to fetch token price for {platform}")
        return
    
    buy_amount = 1  # Example fixed buy amount
    fee = buy_amount * 0.005  # 0.5% fee on buy
    collected_fees += fee
    
    # Get user-defined target if set; otherwise, default to 2X
    target_multiplier = user_sell_targets.get(user_id, 2)
    target_price = buy_price * target_multiplier

    user_trades[user_id] = {
        "contract": contract_address,
        "platform": platform,
        "buy_price": buy_price,
        "target_price": target_price
    }

    logging.info(f"Bought token {contract_address} on {platform} for user {user_id} at {buy_price}. Fee collected: {fee}")
    logging.info(f"Sell target set at {target_price} (Multiplier: {target_multiplier}X)")

async def set_target(update: Update, context: CallbackContext):
    """Command to set a custom sell target."""
    user_id = update.effective_user.id
    
    # Ensure user provided a target value
    if len(context.args) == 0:
        await update.message.reply_text("Usage: /set_target <multiplier> (e.g., /set_target 3)")
        return

    try:
        target_multiplier = float(context.args[0])  # Convert input to float
        if target_multiplier <= 1:
            await update.message.reply_text("Target must be greater than 1X (e.g., 2X, 3X).")
            return
        
        # Save user target
        user_sell_targets[user_id] = target_multiplier
        await update.message.reply_text(f"✅ Sell target set to {target_multiplier}X. Bot will sell when price reaches this.")

    except ValueError:
        await update.message.reply_text("Invalid target! Use a number (e.g., /set_target 3).")

def get_token_price(contract_address, platform):
    try:
        if platform == "Pump.fun":
            response = requests.get(f"{PUMPFUN_API_URL}{contract_address}", timeout=5)
        elif platform == "Raydium":
            response = requests.get(f"{RAYDIUM_API_URL}", timeout=5)
            pairs = response.json()
            for pair in pairs:
                if pair["lp_mint"] == contract_address:
                    return float(pair["price"])
            return None
        else:
            response = requests.get(f"{DEX_API_URL}{contract_address}", timeout=5)
        
        response.raise_for_status()
        data = response.json()
        return float(data.get("data", {}).get("price", 0))
    except (requests.RequestException, KeyError, ValueError) as e:
        logging.error(f"Error fetching price from {platform}: {e}")
        return None

async def monitor_prices():
    global collected_fees
    while True:
        await asyncio.sleep(10)  # Check prices every 10 seconds
        for user_id, trade in list(user_trades.items()):
            current_price = get_token_price(trade["contract"], trade["platform"])
            if current_price and current_price >= trade["target_price"]:
                fee = trade["buy_price"] * 0.03  # 3% fee on sell
                collected_fees += fee
                del user_trades[user_id]
                logging.info(f"Sold token for user {user_id} on {trade['platform']} at {trade['target_price']} ({current_price}). Fee collected: {fee}")

async def check_active_trades(update: Update, context: CallbackContext):
    active_trades = len(user_trades)
    await update.message.reply_text(f"Currently monitoring {active_trades} trades.")

async def show_collected_fees(update: Update, context: CallbackContext):
    await update.message.reply_text(f"Total collected fees: {collected_fees} tokens")

if __name__ == "__main__":
    main()
