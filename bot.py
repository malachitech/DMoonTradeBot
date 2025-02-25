import os
import logging
import re
import requests
import asyncio
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext

# Load environment variables from .env file
load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_WALLET = os.getenv("ADMIN_WALLET_ADDRESS")
DEX_API_URL = "https://api.pancakeswap.info/api/v2/tokens/"
PUMPFUN_API_URL = "https://pump.fun/api/v1/token/"
RAYDIUM_API_URL = "https://api.raydium.io/pairs"

# Set up logging
logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

# Trade tracking
default_buy_amount = 0.1  # Default SOL buy amount
user_trades = {}  # Stores active trades
user_sell_targets = {}  # Stores user-defined sell targets
user_wallets = {}  # Store user wallet addresses securely
collected_fees = 0  # Track collected fees

def main():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("set_target", set_target))
    app.add_handler(CommandHandler("active_trades", check_active_trades))
    app.add_handler(CommandHandler("total_fees", show_collected_fees))
    app.add_handler(CommandHandler("set_buy_amount", set_buy_amount))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_group_link))
    
    logging.info("Bot is running...")
    loop = asyncio.get_event_loop()
    loop.create_task(monitor_prices())  # Background monitoring
    
    app.run_polling()

async def start(update: Update, context: CallbackContext):
    await update.message.reply_text("Welcome to the Auto Trading Bot! Use /set_buy_amount to configure your investment.")

async def set_buy_amount(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if len(context.args) == 0:
        await update.message.reply_text("Usage: /set_buy_amount <amount> (e.g., /set_buy_amount 0.2)")
        return
    
    try:
        amount = float(context.args[0])
        if amount <= 0:
            await update.message.reply_text("Amount must be greater than zero.")
            return
        
        user_wallets[user_id] = {"buy_amount": amount}
        await update.message.reply_text(f"✅ Buy amount set to {amount} SOL.")
    except ValueError:
        await update.message.reply_text("Invalid input! Use a number (e.g., /set_buy_amount 0.2).")

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
    match = re.search(r'0x[a-fA-F0-9]{40}', message)
    if match:
        return match.group(0), "Ethereum"
    
    match = re.search(r'[A-Za-z0-9]{32,44}', message)
    if match:
        if "pump.fun" in message:
            return match.group(0), "Pump.fun"
        elif "raydium.io" in message:
            return match.group(0), "Raydium"
    
    return None, None

def execute_trade(user_id, contract_address, platform):
    global collected_fees
    buy_amount = user_wallets.get(user_id, {}).get("buy_amount", default_buy_amount)
    buy_price = get_token_price(contract_address, platform)
    if not buy_price:
        logging.error(f"Failed to fetch token price for {platform}")
        return
    
    fee = buy_amount * 0.005  # 0.5% fee on buy
    collected_fees += fee
    
    target_multiplier = user_sell_targets.get(user_id, 2)
    target_price = buy_price * target_multiplier
    
    user_trades[user_id] = {
        "contract": contract_address,
        "platform": platform,
        "buy_price": buy_price,
        "target_price": target_price
    }
    
    logging.info(f"Bought {buy_amount} SOL worth of token {contract_address} on {platform} for user {user_id}. Fee: {fee}")
    logging.info(f"Sell target set at {target_price}X")

async def monitor_prices():
    global collected_fees
    while True:
        await asyncio.sleep(10)
        for user_id, trade in list(user_trades.items()):
            current_price = get_token_price(trade["contract"], trade["platform"])
            if current_price and current_price >= trade["target_price"]:
                fee = trade["buy_price"] * 0.03  # 3% fee on sell
                collected_fees += fee
                del user_trades[user_id]
                logging.info(f"Sold token for user {user_id} on {trade['platform']} at {trade['target_price']} ({current_price}). Fee: {fee}")

async def check_active_trades(update: Update, context: CallbackContext):
    await update.message.reply_text(f"Currently monitoring {len(user_trades)} trades.")

async def show_collected_fees(update: Update, context: CallbackContext):
    await update.message.reply_text(f"Total collected fees: {collected_fees} tokens")

async def set_target(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if len(context.args) == 0:
        await update.message.reply_text("Usage: /set_target <multiplier> (e.g., /set_target 3)")
        return
    
    try:
        target_multiplier = float(context.args[0])
        if target_multiplier <= 1:
            await update.message.reply_text("Target must be greater than 1X.")
            return
        
        user_sell_targets[user_id] = target_multiplier
        await update.message.reply_text(f"✅ Sell target set to {target_multiplier}X.")
    except ValueError:
        await update.message.reply_text("Invalid target! Use a number (e.g., /set_target 3).")

if __name__ == "__main__":
    main()
