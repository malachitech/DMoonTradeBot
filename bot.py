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
user_active_trades = {}

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
    
    keyboard = [[InlineKeyboardButton("Wallet Info", callback_data="wallet"),
                 InlineKeyboardButton("Deposit", callback_data="deposit")],
                [InlineKeyboardButton("Set Sell Target", callback_data="set_target"),
                 InlineKeyboardButton("Reset Wallet", callback_data="reset_wallet")],
                [InlineKeyboardButton("Withdraw SOL", callback_data="withdraw"),
                 InlineKeyboardButton("Active Trades", callback_data="active_trades")],
                [InlineKeyboardButton("Help", callback_data="help")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text("Welcome! Use the buttons below to manage your wallet:", reply_markup=reply_markup)

async def wallet_info(query):
    user_id = query.from_user.id
    if user_id not in user_wallets:
        await query.message.reply_text("No wallet found. Use /start to create one.")
        return
    
    wallet_data = user_wallets[user_id]
    balance = await get_sol_balance(wallet_data["address"])
    message = f"\U0001F4B0 **Wallet Info:**\n\n\U0001F538 **Address:** {wallet_data['address']}\n\U0001F538 **Balance:** {balance:.4f} SOL"
    await query.message.reply_text(message)

async def deposit_info(query):
    user_id = query.from_user.id
    if user_id not in user_wallets:
        await query.message.reply_text("No wallet found. Use /start to create one.")
        return
    
    wallet_address = user_wallets[user_id]["address"]
    message = f"To deposit SOL, send funds to:\n{wallet_address}"
    await query.message.reply_text(message)

async def confirm_reset_wallet(query):
    keyboard = [[InlineKeyboardButton("Confirm Reset", callback_data="confirm_reset")],
                [InlineKeyboardButton("Cancel", callback_data="cancel_reset")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.message.reply_text("Are you sure you want to reset your wallet? This action cannot be undone.", reply_markup=reply_markup)

async def withdraw_sol(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if len(context.args) != 2:
        await update.message.reply_text("Usage: /withdraw <amount> <recipient_address>")
        return
    try:
        amount = float(context.args[0])
        recipient = context.args[1]
        if user_id not in user_wallets:
            await update.message.reply_text("No wallet found. Use /start to create one.")
            return
        
        wallet_data = user_wallets[user_id]
        if amount > await get_sol_balance(wallet_data["address"]):
            await update.message.reply_text("Insufficient balance.")
            return
        
        transaction = Transaction()
        params = TransferParams(from_pubkey=wallet_data["keypair"].pubkey(), to_pubkey=Pubkey.from_string(recipient), lamports=int(amount * 1e9))
        transaction.add(transfer(params))
        await solana_client.send_transaction(transaction, wallet_data["keypair"])
        
        await update.message.reply_text(f"Successfully sent {amount} SOL to {recipient}.")
    except Exception as e:
        await update.message.reply_text(f"Error processing withdrawal: {e}")

async def set_sell_target(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if len(context.args) != 1:
        await update.message.reply_text("Usage: /set_target <multiplier>")
        return
    try:
        target = float(context.args[0])
        user_sell_targets[user_id] = target
        await update.message.reply_text(f"Sell target set to {target}x.")
    except ValueError:
        await update.message.reply_text("Invalid target. Please enter a number.")

async def active_trades(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    trades = user_active_trades.get(user_id, [])
    if not trades:
        await update.message.reply_text("No active trades.")
    else:
        trade_list = "\n".join(trades)
        await update.message.reply_text(f"Active Trades:\n{trade_list}")

async def help_command(update: Update, context: CallbackContext):
    message = "\U0001F4AC **Help Menu:**\n\n"
    message += "/start - Initialize wallet\n"
    message += "/set_target <multiplier> - Set sell target\n"
    message += "/active_trades - View active trades\n"
    message += "/withdraw <amount> <recipient_address> - Withdraw SOL\n"
    message += "Use the buttons to navigate."
    await update.message.reply_text(message)

async def handle_button_click(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    if query.data == "wallet":
        await wallet_info(query)
    elif query.data == "deposit":
        await deposit_info(query)
    elif query.data == "set_target":
        await query.message.reply_text("Use /set_target <multiplier> to set your sell target.")
    elif query.data == "reset_wallet":
        await confirm_reset_wallet(query)
    elif query.data == "active_trades":
        await active_trades(update, context)
    elif query.data == "help":
        await help_command(update, context)
    elif query.data == "withdraw":
        await query.message.reply_text("Use /withdraw <amount> <recipient_address> to withdraw SOL.")

async def run_bot():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("set_target", set_sell_target))
    app.add_handler(CommandHandler("active_trades", active_trades))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("withdraw", withdraw_sol))
    app.add_handler(CallbackQueryHandler(handle_button_click))
    logging.info("Bot is running...")
    await app.run_polling()

if __name__ == "__main__":
    import nest_asyncio
    nest_asyncio.apply()
    asyncio.run(run_bot())
