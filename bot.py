import os
import logging
import asyncio
import requests
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackContext, CallbackQueryHandler
from solders.keypair import Keypair
from solders.rpc.responses import GetBalanceResp
from solders.pubkey import Pubkey
from solana.rpc.async_api import AsyncClient
from solders.transaction import Transaction
from solders.system_program import TransferParams, transfer

# Load environment variables
load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
SOLANA_RPC_URL = os.getenv("SOLANA_RPC_URL")
ADMIN_WALLET = os.getenv("ADMIN_WALLET")

# Initialize Solana client
solana_client = AsyncClient(SOLANA_RPC_URL)
user_wallets = {}
user_sell_targets = {}

# Generate a new wallet
def generate_wallet():
    return Keypair()

# Get SOL balance
async def get_sol_balance(wallet_address):
    try:
        pubkey = Pubkey.from_string(wallet_address)
        response = await solana_client.get_balance(pubkey)
        if isinstance(response, GetBalanceResp):
            return response.value / 1e9
    except Exception as e:
        logging.error(f"Error fetching balance: {e}")
    return 0

# Start command
async def start(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if user_id not in user_wallets:
        keypair = generate_wallet()
        address = str(keypair.pubkey())
        balance = await get_sol_balance(address)
        user_wallets[user_id] = {"keypair": keypair, "address": address, "balance": balance}
    
    keyboard = [[InlineKeyboardButton("Wallet Info", callback_data="wallet")],
                [InlineKeyboardButton("Deposit", callback_data="deposit")],
                [InlineKeyboardButton("Set Sell Target", callback_data="set_target")],
                [InlineKeyboardButton("Reset Wallet", callback_data="reset_wallet")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text("Welcome! Use the buttons below to manage your wallet:", reply_markup=reply_markup)

async def wallet_info(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if user_id not in user_wallets:
        await update.message.reply_text("No wallet found. Use /start to create one.")
        return
    
    wallet_data = user_wallets[user_id]
    balance = await get_sol_balance(wallet_data["address"])
    message = f"\U0001F4B0 **Wallet Info:**\n\n\U0001F538 **Address:** {wallet_data['address']}\n\U0001F538 **Balance:** {balance:.4f} SOL"
    await update.message.reply_text(message)

async def deposit_info(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if user_id not in user_wallets:
        await update.message.reply_text("No wallet found. Use /start to create one.")
        return
    
    wallet_address = user_wallets[user_id]["address"]
    message = f"To deposit SOL, send funds to:\n{wallet_address}"
    await update.message.reply_text(message)

async def confirm_reset_wallet(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    user_wallets.pop(user_id, None)
    await update.message.reply_text("Your wallet has been reset. Use /start to create a new one.")

async def set_target(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if len(context.args) != 1:
        await update.message.reply_text("Usage: /set_target <multiplier>")
        return
    try:
        multiplier = float(context.args[0])
        user_sell_targets[user_id] = multiplier
        await update.message.reply_text(f"Sell target set to {multiplier}X.")
    except ValueError:
        await update.message.reply_text("Invalid multiplier. Use a number (e.g., 2.5).")

async def check_price(update: Update, context: CallbackContext):
    await update.message.reply_text("Checking price functionality is under development.")

async def execute_real_trade(user_id, trade_type, amount):
    # Implement Raydium trade execution logic here
    await asyncio.sleep(2)
    return True

async def buy(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if user_id not in user_wallets:
        await update.message.reply_text("No wallet found. Use /start to create one.")
        return
    
    if len(context.args) != 1:
        await update.message.reply_text("Usage: /buy <amount>")
        return
    try:
        buy_amount = float(context.args[0])
        fee = buy_amount * 0.002
        success = await execute_real_trade(user_id, "buy", buy_amount - fee)
        if success:
            await update.message.reply_text(f"Successfully bought {buy_amount - fee} SOL after fee deduction.")
    except ValueError:
        await update.message.reply_text("Invalid amount.")

async def sell(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if user_id not in user_wallets:
        await update.message.reply_text("No wallet found. Use /start to create one.")
        return
    
    if len(context.args) != 1:
        await update.message.reply_text("Usage: /sell <amount>")
        return
    try:
        sell_amount = float(context.args[0])
        fee = sell_amount * 0.03
        success = await execute_real_trade(user_id, "sell", sell_amount - fee)
        if success:
            await update.message.reply_text(f"Successfully sold {sell_amount - fee} SOL after fee deduction.")
    except ValueError:
        await update.message.reply_text("Invalid amount.")

async def handle_button_click(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    if query.data == "wallet":
        await wallet_info(update, context)
    elif query.data == "deposit":
        await deposit_info(update, context)
    elif query.data == "set_target":
        await query.message.reply_text("Use /set_target <multiplier> to set your sell target.")
    elif query.data == "reset_wallet":
        await confirm_reset_wallet(update, context)

async def run_bot():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_button_click))
    logging.info("Bot is running...")
    await app.run_polling()

if __name__ == "__main__":
    import nest_asyncio
    nest_asyncio.apply()
    asyncio.run(run_bot())
