import os
import logging
import re
import requests
import asyncio
import random
import string
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext, CallbackQueryHandler

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

def get_token_price(contract_address, platform):
    # Simulating live price tracking; replace with actual API call
    return random.uniform(0.1, 10.0)

def execute_trade(user_id, contract_address, platform, sol_amount):
    global collected_fees
    if user_wallets.get(user_id, {}).get("balance", 0) < sol_amount:
        logging.error("Insufficient balance for trade")
        return
    
    buy_price = get_token_price(contract_address, platform)
    if not buy_price:
        logging.error("Failed to fetch token price")
        return
    
    fee = sol_amount * 0.005  # 0.5% buy fee
    collected_fees += fee
    user_wallets[user_id]["balance"] -= (sol_amount + fee)
    user_trades[user_id] = {"contract": contract_address, "platform": platform, "buy_price": buy_price, "sol_amount": sol_amount}
    logging.info(f"Bought {sol_amount} SOL worth of {contract_address}. Fee: {fee}")

def check_sell_orders():
    for user_id, trade in user_trades.items():
        contract_address = trade["contract"]
        platform = trade["platform"]
        buy_price = trade["buy_price"]
        target_multiplier = user_sell_targets.get(user_id, None)
        
        if target_multiplier:
            current_price = get_token_price(contract_address, platform)
            if current_price >= buy_price * target_multiplier:
                sell_amount = trade["sol_amount"]
                fee = sell_amount * 0.03  # 3% sell fee
                collected_fees += fee
                user_wallets[user_id]["balance"] += (sell_amount * current_price - fee)
                del user_trades[user_id]
                logging.info(f"Sold {sell_amount} SOL worth of {contract_address} at {current_price}. Fee: {fee}")
                
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

async def button_click(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()

    if query.data == "wallet":
        await wallet_info(update, context)
    elif query.data == "deposit":
        await deposit_info(update, context)
    elif query.data == "set_target":
        await query.message.reply_text("Use /set_target <multiplier> (e.g., /set_target 3)")
    elif query.data == "reset_wallet":
        await reset_wallet(update, context)
async def wallet_info(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    wallet = user_wallets.get(user_id, {"address": "Not set", "balance": 0})
    await update.message.reply_text(f"Wallet Address: {wallet['address']}\nBalance: {wallet['balance']} SOL")

async def deposit_info(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    user_wallets[user_id]["balance"] += 5  # Simulated deposit
    await update.message.reply_text(f"5 SOL deposited to your wallet! New Balance: {user_wallets[user_id]['balance']} SOL")

async def reset_wallet(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    user_wallets[user_id] = {"address": generate_wallet(), "balance": 0}
    await update.message.reply_text("Your wallet has been reset! New wallet generated.")

async def set_target(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if not context.args:
        await update.message.reply_text("Please provide a target multiplier. Usage: /set_target 3")
        return
    
    try:
        target = float(context.args[0])
        user_sell_targets[user_id] = target
        await update.message.reply_text(f"Sell target set to {target}X.")
    except ValueError:
        await update.message.reply_text("Invalid target. Please provide a numeric multiplier.")

async def run_bot():
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("set_target", set_target))
    app.add_handler(CommandHandler("wallet", wallet_info))
    app.add_handler(CommandHandler("deposit", deposit_info))
    app.add_handler(CommandHandler("reset_wallet", reset_wallet))
    app.add_handler(CallbackQueryHandler(button_click))
    
    logging.info("Bot is running...")
    
    while True:
        check_sell_orders()
        await asyncio.sleep(10)  # Check sell orders every 10 seconds
    
    await app.run_polling()

if __name__ == "__main__":
    import nest_asyncio
    nest_asyncio.apply()
    
    loop = asyncio.get_event_loop()
    if loop.is_running():
        loop.create_task(run_bot())  # Run as a background task
    else:
        loop.run_until_complete(run_bot())
