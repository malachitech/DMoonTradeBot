import os
import logging
import requests
import asyncio
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext, CallbackQueryHandler

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

def start(update: Update, context: CallbackContext):
    keyboard = [[InlineKeyboardButton("Check Balance", callback_data='check_balance')],
                [InlineKeyboardButton("Fund Bot", callback_data='fund_bot')],
                [InlineKeyboardButton("Check Account Details", callback_data='check_account_details')],
                [InlineKeyboardButton("Deposit SOL", callback_data='deposit_sol')],
                [InlineKeyboardButton("Buy SOL", callback_data='buy_sol')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text("🤖 Welcome! Choose an option:", reply_markup=reply_markup)

def set_target(update: Update, context: CallbackContext):
    update.message.reply_text("⚙️ Set target feature coming soon!")

def check_active_trades(update: Update, context: CallbackContext):
    update.message.reply_text("📊 Active trades feature coming soon!")

def show_collected_fees(update: Update, context: CallbackContext):
    global collected_fees
    update.message.reply_text(f"Total collected fees: {collected_fees} SOL")

def set_buy_amount(update: Update, context: CallbackContext):
    global default_buy_amount
    try:
        amount = float(context.args[0])
        if amount > 0:
            default_buy_amount = amount
            update.message.reply_text(f"✅ Buy amount set to {default_buy_amount} SOL")
        else:
            update.message.reply_text("❌ Amount must be greater than zero.")
    except (IndexError, ValueError):
        update.message.reply_text("Usage: /set_buy_amount <amount>")

def check_balance(update: Update, context: CallbackContext):
    user_id = update.message.chat_id
    wallet_address = user_wallets.get(user_id)
    if not wallet_address:
        update.message.reply_text("❌ No wallet linked. Use /check_account_details to verify.")
        return
    
    try:
        response = requests.get(f"{SOLSCAN_API_URL}{wallet_address}")
        response.raise_for_status()
        balance = response.json().get("sol", 0)
        update.message.reply_text(f"💰 Your balance: {balance} SOL")
    except requests.RequestException:
        update.message.reply_text("⚠️ Error retrieving balance. Try again later.")

def fund_bot(update: Update, context: CallbackContext):
    update.message.reply_text("💳 Send funds to the bot wallet: [Wallet Address Here]")

def check_account_details(update: Update, context: CallbackContext):
    user_id = update.message.chat_id
    wallet_address = user_wallets.get(user_id, "Not linked")
    update.message.reply_text(f"🔍 Your linked wallet: {wallet_address}")

def deposit_sol(update: Update, context: CallbackContext):
    update.message.reply_text("📥 Deposit SOL to: [Bot Wallet Address]")

def buy_sol(update: Update, context: CallbackContext):
    update.message.reply_text("🛒 Buying SOL... (Feature in development)")

def button_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    query.answer()
    if query.data == "check_balance":
        check_balance(update, context)
    elif query.data == "fund_bot":
        fund_bot(update, context)
    elif query.data == "check_account_details":
        check_account_details(update, context)
    elif query.data == "deposit_sol":
        deposit_sol(update, context)
    elif query.data == "buy_sol":
        buy_sol(update, context)

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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: u.message.reply_text("Unknown command.")))
    app.add_handler(CallbackQueryHandler(button_callback))
    
    logging.info("Bot is running...")
    loop = asyncio.get_event_loop()
    loop.create_task(asyncio.sleep(1))  # Placeholder task
    
    app.run_polling()

if __name__ == "__main__":
    main()
