import os
import logging
import random
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

# Trade tracking
user_trades = {}
user_sell_targets = {}
user_wallets = {}
solana_client = AsyncClient(SOLANA_RPC_URL)

def generate_wallet():
    keypair = Keypair()
    return keypair

async def get_sol_balance(wallet_address):
    pubkey = Pubkey.from_string(wallet_address)  # Convert string to Pubkey
    response = await solana_client.get_balance(pubkey)
    if isinstance(response, GetBalanceResp):
        return response.value / 1e9  # Convert lamports to SOL
    return 0

async def start(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if user_id not in user_wallets:
        keypair = generate_wallet()
        user_wallets[user_id] = {"keypair": keypair, "address": str(keypair.pubkey()), "balance": await get_sol_balance(str(keypair.pubkey()))}
    
    keyboard = [[InlineKeyboardButton("Wallet Info", callback_data="wallet")],
                [InlineKeyboardButton("Deposit", callback_data="deposit")],
                [InlineKeyboardButton("Set Sell Target", callback_data="set_target")],
                [InlineKeyboardButton("Reset Wallet", callback_data="reset_wallet")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text("Welcome! Use the buttons below to manage your wallet:", reply_markup=reply_markup)

async def wallet_info(query, context, user_id):
    if user_id not in user_wallets:
        await query.message.reply_text("No wallet found. Use /start to create one.")
        return
    
    wallet_data = user_wallets[user_id]
    balance = await get_sol_balance(wallet_data["address"])  # Get updated balance
    
    message = f"\U0001F4B0 **Wallet Info:**\n\n\U0001F538 **Address:** `{wallet_data['address']}`\n\U0001F538 **Balance:** {balance:.4f} SOL"
    await query.message.reply_text(message)

async def deposit_info(query, context, user_id):
    if user_id not in user_wallets:
        await query.message.reply_text("No wallet found. Use /start to create one.")
        return
    
    wallet_address = user_wallets[user_id]["address"]
    message = f"To deposit SOL, send funds to:\n`{wallet_address}`"
    await query.message.reply_text(message)

async def confirm_reset_wallet(query, context, user_id):
    user_wallets.pop(user_id, None)
    await query.message.reply_text("Your wallet has been reset. Use /start to create a new one.")

def get_token_price(contract_address, platform):
    return random.uniform(0.1, 10.0)  # Simulated price tracking

async def active_trades(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if user_id not in user_trades:
        await update.message.reply_text("You have no active trades.")
        return
    trade = user_trades[user_id]
    message = f"Active Trade:\nContract: {trade['contract']}\nPlatform: {trade['platform']}\nBuy Price: {trade['buy_price']} SOL\nAmount: {trade['sol_amount']} SOL"
    await update.message.reply_text(message)

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

async def buy(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if user_id not in user_wallets:
        await update.message.reply_text("No wallet found. Use /start to create one.")
        return
    user_wallet = user_wallets[user_id]["address"]
    fee = 0.002  # 0.2% fee
    await update.message.reply_text(f"Buying tokens... 0.2% fee sent to admin wallet {ADMIN_WALLET}.")

async def sell(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if user_id not in user_wallets:
        await update.message.reply_text("No wallet found. Use /start to create one.")
        return
    user_wallet = user_wallets[user_id]["address"]
    fee = 0.002  # 0.2% fee
    await update.message.reply_text(f"Selling tokens... 0.2% fee sent to admin wallet {ADMIN_WALLET}.")

async def check_price(update: Update, context: CallbackContext):
    await update.message.reply_text("Checking price functionality coming soon.")

async def help_command(update: Update, context: CallbackContext):
    help_text = "Available commands:\n/start - Start the bot\n/set_target <multiplier> - Set your sell target\n/active_trades - View your active trades\n/buy - Buy tokens\n/sell - Sell tokens\n/check_price - Check token price"
    await update.message.reply_text(help_text)

async def button_click(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    if query.data == "wallet":
        await wallet_info(query, context, user_id)
    elif query.data == "deposit":
        await deposit_info(query, context, user_id)
    elif query.data == "set_target":
        await query.message.reply_text("Use /set_target <multiplier> (e.g., /set_target 3)")
    elif query.data == "reset_wallet":
        await confirm_reset_wallet(query, context, user_id)
    else:
        await query.message.reply_text("Invalid selection.")

async def run_bot():
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("set_target", set_target))
    app.add_handler(CommandHandler("active_trades", active_trades))
    app.add_handler(CommandHandler("buy", buy))
    app.add_handler(CommandHandler("sell", sell))
    app.add_handler(CommandHandler("check_price", check_price))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CallbackQueryHandler(button_click))
    
    logging.info("Bot is running...")
    await app.run_polling()

if __name__ == "__main__":
    import nest_asyncio
    nest_asyncio.apply()
    
    asyncio.run(run_bot())
