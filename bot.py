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
from solana.rpc.async_api import AsyncClient
from solana.transaction import Transaction
from solana.system_program import TransferParams, transfer

# Load environment variables
load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
SOLANA_RPC_URL = os.getenv("SOLANA_RPC_URL")

# Trade tracking
user_trades = {}
user_sell_targets = {}
user_wallets = {}
collected_fees = 0
solana_client = AsyncClient(SOLANA_RPC_URL)

def generate_wallet():
    keypair = Keypair()
    return keypair

def get_sol_balance(wallet_address):
    response = asyncio.run(solana_client.get_balance(wallet_address))
    if isinstance(response, GetBalanceResp):
        return response.value / 1e9  # Convert lamports to SOL
    return 0

def get_token_price(contract_address, platform):
    return random.uniform(0.1, 10.0)  # Simulated price tracking

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
    for user_id, trade in list(user_trades.items()):
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

async def check_sell_orders_loop():
    while True:
        check_sell_orders()
        await asyncio.sleep(10)  # Check every 10 seconds

async def start(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if user_id not in user_wallets:
        keypair = generate_wallet()
        user_wallets[user_id] = {"keypair": keypair, "address": str(keypair.pubkey()), "balance": get_sol_balance(str(keypair.pubkey()))}
    
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

async def wallet_info(update_or_query, context: CallbackContext, user_id):
    wallet = user_wallets.get(user_id, {"address": "Not set", "balance": 0})
    wallet["balance"] = get_sol_balance(wallet["address"])
    await update_or_query.message.reply_text(f"Wallet Address: {wallet['address']}\nBalance: {wallet['balance']} SOL")

async def deposit_info(update_or_query, context: CallbackContext, user_id):
    wallet = user_wallets.get(user_id, {"address": "Not set", "balance": 0})
    await update_or_query.message.reply_text(f"Send SOL to this address: {wallet['address']}")
    
    # Simulated transaction detection (in real application, integrate with Solana webhook)
    await asyncio.sleep(10)  # Simulate delay for transaction detection
    wallet["balance"] = get_sol_balance(wallet["address"])
    await update_or_query.message.reply_text(f"Deposit received! New Balance: {wallet['balance']} SOL")

async def confirm_reset_wallet(update_or_query, context: CallbackContext, user_id):
    keyboard = [
        [InlineKeyboardButton("Yes, reset my wallet", callback_data="confirm_reset")],
        [InlineKeyboardButton("Cancel", callback_data="cancel_reset")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update_or_query.message.reply_text("Are you sure you want to reset your wallet? Your balance will remain intact.", reply_markup=reply_markup)

async def reset_wallet(update_or_query, context: CallbackContext, user_id):
    balance = user_wallets[user_id]["balance"]
    keypair = generate_wallet()
    user_wallets[user_id] = {"keypair": keypair, "address": str(keypair.pubkey()), "balance": balance}
    await update_or_query.message.reply_text("Your wallet has been reset! New wallet generated.")

async def run_bot():
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("set_target", set_target))
    app.add_handler(CallbackQueryHandler(button_click))
    
    logging.info("Bot is running...")
    asyncio.create_task(check_sell_orders_loop())  # Run order checking separately
    await app.run_polling()

if __name__ == "__main__":
    import nest_asyncio
    nest_asyncio.apply()
    
    asyncio.run(run_bot())
