import os
import logging
import re
import requests
import asyncio
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext

# Load environment variables from .env file
load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_WALLET = os.getenv("ADMIN_WALLET_ADDRESS")
SOLSCAN_API_URL = "https://solscan.io/api/account/"

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
    app.add_handler(CommandHandler("check_balance", check_balance))
    app.add_handler(CommandHandler("fund_bot", fund_bot))
    app.add_handler(CommandHandler("check_account_details", check_account_details))
    app.add_handler(CommandHandler("deposit_sol", deposit_sol))
    app.add_handler(CommandHandler("buy_sol", buy_sol))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, receive_group_link))
    
    logging.info("Bot is running...")
    loop = asyncio.get_event_loop()
    loop.create_task(monitor_prices())  # Background monitoring
    
    app.run_polling()

async def start(update: Update, context: CallbackContext):
    keyboard = [
        [InlineKeyboardButton("Check Balance", callback_data='check_balance')],
        [InlineKeyboardButton("Fund Bot", callback_data='fund_bot')],
        [InlineKeyboardButton("Check Account Details", callback_data='check_account_details')],
        [InlineKeyboardButton("Deposit SOL", callback_data='deposit_sol')],
        [InlineKeyboardButton("Buy SOL", callback_data='buy_sol')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Welcome to the Auto Trading Bot! Use the buttons below to navigate:", reply_markup=reply_markup)

async def check_balance(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    wallet_address = user_wallets.get(user_id)
    if not wallet_address:
        await update.message.reply_text("No wallet linked. Please link your wallet first.")
        return
    
    try:
        response = requests.get(f"{SOLSCAN_API_URL}{wallet_address}", timeout=5)
        response.raise_for_status()
        data = response.json()
        balance = data.get("data", {}).get("lamports", 0) / 1e9  # Convert lamports to SOL
        await update.message.reply_text(f"Your bot balance is: {balance:.4f} SOL")
    except requests.RequestException as e:
        logging.error(f"Error fetching balance: {e}")
        await update.message.reply_text("Failed to retrieve balance. Please try again later.")

async def fund_bot(update: Update, context: CallbackContext):
    await update.message.reply_text("Send SOL to the provided deposit address.")

async def check_account_details(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    wallet_address = user_wallets.get(user_id, "Not linked")
    await update.message.reply_text(f"Account details: \nWallet: {wallet_address} \nTrades: {len(user_trades)} Active")

async def deposit_sol(update: Update, context: CallbackContext):
    await update.message.reply_text("Deposit SOL to: wallet_address (Mock Data)")

async def buy_sol(update: Update, context: CallbackContext):
    await update.message.reply_text("Enter the amount of SOL you want to buy.")

if __name__ == "__main__":
    main()
