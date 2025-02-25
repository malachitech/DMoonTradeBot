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
    app.add_handler(CallbackQueryHandler(button_callback))
    
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

async def button_callback(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    
    if query.data == "check_balance":
        await check_balance(update, context, query.message)
    elif query.data == "fund_bot":
        await fund_bot(update, context, query.message)
    elif query.data == "check_account_details":
        await check_account_details(update, context, query.message)
    elif query.data == "deposit_sol":
        await deposit_sol(update, context, query.message)
    elif query.data == "buy_sol":
        await buy_sol(update, context, query.message)

async def check_balance(update: Update, context: CallbackContext, message=None):
    user_id = update.effective_user.id
    wallet_address = user_wallets.get(user_id)
    if not wallet_address:
        await message.reply_text("No wallet linked. Please link your wallet first.")
        return
    
    try:
        response = requests.get(f"{SOLSCAN_API_URL}{wallet_address}", timeout=5)
        response.raise_for_status()
        data = response.json()
        balance = data.get("data", {}).get("lamports", 0) / 1e9  # Convert lamports to SOL
        await message.reply_text(f"Your bot balance is: {balance:.4f} SOL")
    except requests.RequestException as e:
        logging.error(f"Error fetching balance: {e}")
        await message.reply_text("Failed to retrieve balance. Please try again later.")

async def set_target(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if len(context.args) == 0:
        await update.message.reply_text("Usage: /set_target <multiplier> (e.g., /set_target 3)")
        return
    
    try:
        target_multiplier = float(context.args[0])
        if target_multiplier <= 1:
            await update.message.reply_text("Target must be greater than 1X (e.g., 2X, 3X).")
            return
        
        user_sell_targets[user_id] = target_multiplier
        await update.message.reply_text(f"✅ Sell target set to {target_multiplier}X. Bot will sell when price reaches this.")
    except ValueError:
        await update.message.reply_text("Invalid target! Use a number (e.g., /set_target 3).")

async def fund_bot(update: Update, context: CallbackContext, message=None):
    await message.reply_text("Send SOL to the provided deposit address.")

async def check_account_details(update: Update, context: CallbackContext, message=None):
    user_id = update.effective_user.id
    wallet_address = user_wallets.get(user_id, "Not linked")
    await message.reply_text(f"Account details: \nWallet: {wallet_address} \nTrades: {len(user_trades)} Active")

async def deposit_sol(update: Update, context: CallbackContext, message=None):
    await message.reply_text("Deposit SOL to: wallet_address (Mock Data)")

async def buy_sol(update: Update, context: CallbackContext, message=None):
    await message.reply_text("Enter the amount of SOL you want to buy.")

async def check_active_trades(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    active_trades = user_trades.get(user_id, [])
    if not active_trades:
        await update.message.reply_text("You have no active trades.")
    else:
        trades_list = '\n'.join(active_trades)
        await update.message.reply_text(f"Your active trades:\n{trades_list}")

if __name__ == "__main__":
    main()