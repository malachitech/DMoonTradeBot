import os
import logging
import asyncio
import requests
import threading
import base64
import time
user_last_withdrawal = {}
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackContext, CallbackQueryHandler
from solders.keypair import Keypair
from solders.rpc.responses import GetBalanceResp
from solders.pubkey import Pubkey
from solana.rpc.async_api import AsyncClient
from solders.transaction import Transaction
from solders.system_program import TransferParams, transfer
from flask import Flask, request, jsonify



# Load environment variables
load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
SOLANA_RPC_URL = os.getenv("SOLANA_RPC_URL")
ADMIN_WALLET = os.getenv("ADMIN_WALLET")
BOT_WALLET_PRIVATE_KEY = os.getenv("BOT_WALLET_PRIVATE_KEY")

bot_wallet = Keypair.from_base58_string(BOT_WALLET_PRIVATE_KEY)

app = Flask(__name__)

# Initialize Solana client
solana_client = AsyncClient(SOLANA_RPC_URL)
user_wallets = {}
user_sell_targets = {}
user_active_trades = {}



# ✅ Proper Phantom Webhook to Verify Solana Transactions
@app.route("/phantom_webhook", methods=["POST"])
async def phantom_webhook():
    data = request.json
    transaction_id = data.get("transactionId")

    if not transaction_id:
        return jsonify({"error": "Missing transactionId"}), 400

    async with AsyncClient(SOLANA_RPC_URL) as client:
        response = await client.get_confirmed_transaction(transaction_id)

        if response and response.get("result"):  # ✅ Improved validation
            print(f"✅ Transaction Approved: {transaction_id}")
            return jsonify({"status": "success"}), 200
        else:
            print(f"❌ Transaction Failed: {transaction_id}")
            return jsonify({"status": "failed"}), 400

# ✅ Flask Function for Running on a Separate Thread
def run_flask():
    print("🌍 Flask Webhook is Running on port 5000...")
    app.run(host="0.0.0.0", port=5000, debug=True)

# ✅ Telegram Bot Function
async def run_telegram_bot():
    bot = Application.builder().token(TOKEN).build()
    bot.add_handler(CommandHandler("start", start))
    print("🤖 Telegram Bot is Running...")
    await bot.run_polling()

if __name__ == "__main__":
    # Run Flask & Telegram in separate threads for Railway Deployment
    threading.Thread(target=run_flask, daemon=True).start()
    asyncio.run(run_telegram_bot())



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



async def start(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if user_id not in user_wallets:
        keypair = Keypair()
        address = str(keypair.pubkey())
        balance = await get_sol_balance(address)
        user_wallets[user_id] = {"keypair": keypair, "address": address, "balance": balance}
        await update.message.reply_text("Welcome! Your wallet has been created.")
    else:
        await update.message.reply_text("Welcome back! Your wallet is already set up.")

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


async def monitor_wallet(wallet_address):
    async with AsyncClient(SOLANA_RPC_URL) as client:
        sub_id = await client.websocket_subscribe(
            f"accountSubscribe {wallet_address} commitment=finalized"
        )
        async for msg in client.websocket_recv():
            print("🔔 New transaction detected:", msg)
async def withdraw_phantom(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    now = time.time()

    # Prevent spam (Only allow withdrawal every 60 seconds)
    if user_id in user_last_withdrawal and now - user_last_withdrawal[user_id] < 60:
        await update.message.reply_text("⚠️ You can only withdraw once per minute.")
        return

    user_last_withdrawal[user_id] = now  # Update last withdrawal time
    if len(context.args) != 2:
        await update.message.reply_text("Usage: /withdraw <amount> <recipient_address>")
        return

    try:
        amount = float(context.args[0])
        recipient = context.args[1]

        # Validate recipient address
        try:
            recipient_pubkey = Pubkey.from_string(recipient)
        except:
            await update.message.reply_text("Invalid recipient address.")
            return

        # Check bot wallet balance
        bot_balance = await get_sol_balance(str(bot_wallet.pubkey()))
        if amount > bot_balance:
            await update.message.reply_text(f"Insufficient bot balance. Available: {bot_balance:.4f} SOL")
            return

        # Construct transaction
        transaction = Transaction()
        params = TransferParams(
            from_pubkey=bot_wallet.pubkey(),
            to_pubkey=recipient_pubkey,
            lamports=int(amount * 1e9),
        )
        transaction.add(transfer(params))

        # Send and confirm transaction
        response = await solana_client.send_transaction(transaction, bot_wallet)
        await update.message.reply_text(f"✅ Successfully sent {amount} SOL to {recipient}\nTransaction: {response}")

    except Exception as e:
        logging.error(f"Error processing withdrawal: {e}")
        await update.message.reply_text(f"⚠️ Error: {e}")


async def monitor_bot_wallet():
    async with AsyncClient(SOLANA_RPC_URL) as client:
        sub_id = await client.websocket_subscribe(
            f"accountSubscribe {bot_wallet.pubkey()} commitment=finalized"
        )
        async for msg in client.websocket_recv():
            print("🔔 New deposit detected:", msg)



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
    elif query.data == "cancel_reset":
        await query.message.edit_text("Wallet refresh canceled.")
    elif query.data == "active_trades":
        await active_trades(update, context)
    elif query.data == "help":
        await help_command(update, context)
    elif query.data == "withdraw":
        await query.message.reply_text("Use /withdraw <amount> <recipient_address> to withdraw SOL.")
    elif query.data == "confirm_reset":
        user_id = query.from_user.id
    if user_id in user_wallets:
        balance = await get_sol_balance(user_wallets[user_id]["address"])
        user_wallets[user_id]["balance"] = balance
        await query.message.edit_text(f"Your wallet has been refreshed.\n\nNew Balance: {balance:.4f} SOL")
    else:
        await query.message.edit_text("No wallet found to refresh.")
    


async def run_telegram_bot():
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("withdraw", withdraw_phantom))
    
    logging.info("🤖 Telegram Bot is Running...")
    await app.run_polling()

def run_flask():
    logging.info("🌍 Flask Webhook is Running...")
    app.run(host="0.0.0.0", port=5000)

async def main():
    # Run Telegram bot & Flask server together
    loop = asyncio.get_running_loop()
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    await run_telegram_bot()

if __name__ == "__main__":
    import nest_asyncio
    nest_asyncio.apply()  # Allows async functions to run in Jupyter or nested loops

    asyncio.run(main())  # Run both Flask and Telegram Bot
    

