import os
import logging
import re
import requests
import asyncio
import random
import string
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext

# Load environment variables
load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Trade tracking
user_trades = {}
user_sell_targets = {}
user_wallets = {}
collected_fees = 0

def generate_wallet():
    return "SOL_WALLET_" + ''.join(random.choices(string.ascii_letters + string.digits, k=10))

async def start(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if user_id not in user_wallets:
        user_wallets[user_id] = {"address": generate_wallet(), "balance": 0}
    
    keyboard = [
        [InlineKeyboardButton("Wallet", callback_data="wallet"),
         InlineKeyboardButton("Deposit SOL", callback_data="deposit")],
        [InlineKeyboardButton("Set Target", callback_data="set_target"),
         InlineKeyboardButton("Reset Wallet", callback_data="reset_wallet")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text("Welcome to the Auto Trading Bot!", reply_markup=reply_markup)

async def receive_group_link(update: Update, context: CallbackContext):
    group_link = update.message.text
    contract_address, platform = extract_contract_address(group_link)
    if contract_address:
        await update.message.reply_text(f"Detected contract address: {contract_address} on {platform}. Initiating trade...")
        execute_trade(update.message.chat_id, contract_address, platform, 0.1)  # Default to 0.1 SOL
    else:
        await update.message.reply_text("No contract address found yet. Monitoring continues...")

def extract_contract_address(message: str):
    match = re.search(r'[A-Za-z0-9]{32,44}', message)
    return (match.group(0), "Solana") if match else (None, None)

def execute_trade(user_id, contract_address, platform, sol_amount):
    global collected_fees
    buy_price = get_token_price(contract_address, platform)
    if not buy_price:
        logging.error("Failed to fetch token price")
        return
    fee = sol_amount * 0.005  # 0.5% buy fee
    collected_fees += fee
    user_trades[user_id] = {"contract": contract_address, "platform": platform, "buy_price": buy_price, "sol_amount": sol_amount}
    logging.info(f"Bought {sol_amount} SOL worth of {contract_address}. Fee: {fee}")

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

def get_token_price(contract_address, platform):
    try:
        response = requests.get(f"https://api.solscan.io/token/price/{contract_address}", timeout=5)
        response.raise_for_status()
        return float(response.json().get("price", 0))
    except requests.RequestException:
        return None

async def monitor_prices():
    while True:
        await asyncio.sleep(10)
        for user_id, trade in list(user_trades.items()):
            current_price = get_token_price(trade["contract"], trade["platform"])
            if current_price and user_sell_targets.get(user_id, 2) * trade["buy_price"] <= current_price:
                del user_trades[user_id]
                logging.info(f"Sold for user {user_id} at {current_price}")

async def wallet_info(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    wallet = user_wallets.get(user_id, {"address": "Not set", "balance": 0})
    await update.message.reply_text(f"Wallet Address: {wallet['address']}\nBalance: {wallet['balance']} SOL")

async def deposit_info(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    wallet = user_wallets.get(user_id, {"address": "Not set"})
    await update.message.reply_text(f"Send SOL to: {wallet['address']}")

async def reset_wallet(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    user_wallets[user_id] = {"address": generate_wallet(), "balance": 0}
    await update.message.reply_text("Your wallet has been reset! New wallet generated.")

async def check_active_trades(update: Update, context: CallbackContext):
    await update.message.reply_text(f"Currently monitoring {len(user_trades)} trades.")

async def run_bot():
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("set_target", set_target))
    app.add_handler(CommandHandler("wallet", wallet_info))
    app.add_handler(CommandHandler("deposit", deposit_info))
    app.add_handler(CommandHandler("reset_wallet", reset_wallet))
    app.add_handler(CommandHandler("active_trades", check_active_trades))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_group_link))
    
    logging.info("Bot is running...")
    
    asyncio.create_task(monitor_prices())
    
    await app.run_polling()




if __name__ == "__main__":
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    loop.create_task(run_bot())  # Schedule the bot without blocking
    loop.run_forever()  # Keep the event loop running
