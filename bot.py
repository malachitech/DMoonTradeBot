import os
import logging
import asyncio
import requests
import threading
import base64
import time
import platform
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
ADMIN_WALLET = os.getenv("ADMIN_WALLET_ADDRESS")
BOT_WALLET_PRIVATE_KEY = os.getenv("BOT_WALLET_PRIVATE_KEY")
bot_wallet = Keypair.from_base58_string(BOT_WALLET_PRIVATE_KEY)
# ✅ Ensure the private key exists before using it
if not BOT_WALLET_PRIVATE_KEY:
    logging.error("🚨 BOT_WALLET_PRIVATE_KEY is missing! Check Railway environment variables.")
    raise ValueError("🚨 BOT_WALLET_PRIVATE_KEY is missing! Set it in Railway.")

logging.info(f"🔑 BOT_WALLET_PRIVATE_KEY Loaded: {BOT_WALLET_PRIVATE_KEY[:5]}... (truncated for security)")
# Output the private key
print(f"Bot Wallet Private Key: {BOT_WALLET_PRIVATE_KEY}")

user_last_withdrawal = {}

# Initialize Solana client
solana_client = AsyncClient(SOLANA_RPC_URL)
user_wallets = {}
user_sell_targets = {}
user_active_trades = {}


app = Flask(__name__)
logging.basicConfig(level=logging.INFO)


# ✅ Flask Webhook to Handle Solana Deposits
@app.route("/phantom_webhook", methods=["POST"])
def phantom_webhook():
    data = request.json
    transaction_id = data.get("transactionId")

    if not transaction_id:
        logging.error("🚨 Missing transactionId in webhook request")
        return jsonify({"error": "Missing transactionId"}), 400

    loop = asyncio.get_event_loop()
    result = loop.run_until_complete(check_transaction(transaction_id))

    if result:
        logging.info(f"✅ Transaction Approved: {transaction_id}")
        return jsonify({"status": "success"}), 200
    else:
        logging.warning(f"❌ Transaction Failed: {transaction_id}")
        return jsonify({"status": "failed"}), 400


async def start(update: Update, context: CallbackContext):
    user_id = update.effective_user.id

    if user_id not in user_wallets:
        keypair = Keypair()
        address = str(keypair.pubkey())
        balance = await get_sol_balance(address)

        user_wallets[user_id] = {
            "keypair": keypair,
            "address": address,
            "balance": balance
        }
        message = (
            f"✅ Welcome! Your wallet has been created.\n"
            f"📌 Address: `{address}`\n"
            f"💰 Balance: {balance:.4f} SOL"
        )
    else:
        wallet_data = user_wallets[user_id]
        message = (
            f"👋 Welcome back!\n"
            f"📌 Address: `{wallet_data['address']}`\n"
            f"💰 Balance: {wallet_data['balance']:.4f} SOL"
        )

    # ✅ Inline buttons for actions
    keyboard = [
        [InlineKeyboardButton("💼 Wallet Info", callback_data="wallet"),
         InlineKeyboardButton("💰 Deposit", callback_data="deposit")],
        [InlineKeyboardButton("🎯 Set Sell Target", callback_data="set_target"),
         InlineKeyboardButton("🔄 Reset Wallet", callback_data="reset_wallet")],
        [InlineKeyboardButton("📤 Withdraw SOL", callback_data="withdraw"),
         InlineKeyboardButton("📊 Active Trades", callback_data="active_trades")],
        [InlineKeyboardButton("❓ Help", callback_data="help")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(message, parse_mode="Markdown", reply_markup=reply_markup)
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

# async def get_sol_balance(wallet_address):
#     try:
#         pubkey = Pubkey.from_string(wallet_address)
#         async with AsyncClient(SOLANA_RPC_URL) as client:  # Use a fresh client each time
#             response = await client.get_balance(pubkey)
#             if isinstance(response, GetBalanceResp):
#                 return response.value / 1e9
#         return 0
#     except Exception as e:
#         logging.error(f"Error fetching balance: {e}")
#         return 0


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


# async def monitor_wallet(wallet_address):
#     async with AsyncClient(SOLANA_RPC_URL) as client:
#         sub_id = await client.websocket_subscribe(
#             f"accountSubscribe {wallet_address} commitment=finalized"
#         )
#         async for msg in client.websocket_recv():
#             print("🔔 New transaction detected:", msg)
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
    
# ✅ Check Solana Transaction Validity
async def check_transaction(transaction_id):
    async with AsyncClient(SOLANA_RPC_URL) as client:
        response = await client.get_confirmed_transaction(transaction_id)
        return bool(response and response.get("result"))


# ✅ Telegram Bot Function
async def run_telegram_bot():
    bot = Application.builder().token(TOKEN).build()

    # ✅ Register all command handlers
    bot.add_handler(CommandHandler("start", start))
    bot.add_handler(CommandHandler("withdraw", withdraw_phantom))
    bot.add_handler(CommandHandler("set_target", set_sell_target))
    bot.add_handler(CommandHandler("active_trades", active_trades))
    bot.add_handler(CommandHandler("help", help_command))

 # ✅ Register callback handler for button clicks
    bot.add_handler(CallbackQueryHandler(handle_button_click))

    logging.info("🤖 Telegram Bot is Running...")
    await bot.run_polling()
    # ✅ Fix for "event loop already running" error
    try:
        await bot.run_polling(close_loop=False)  # ✅ Prevents forced event loop closure
    except RuntimeError as e:
        logging.error(f"🚨 Telegram bot crashed: {e}")

    
# ✅ Prevent Railway from Stopping the Bot
@app.route("/keep-alive", methods=["GET"])
def keep_alive():
    return "Bot is running", 200

def run_flask():
    logging.info("🚀 Running Flask in Production Mode...")

    try:
        if platform.system() == "Windows":  
            # ✅ Use Waitress on Windows
            from waitress import serve
            logging.info("🌍 Using Waitress WSGI server on Windows...")
            serve(app, host="0.0.0.0", port=5000)

        else:  
            # ✅ Use Gunicorn on Linux/macOS
            from gunicorn.app.base import BaseApplication

            class FlaskApp(BaseApplication):
                def __init__(self, app, options=None):
                    self.options = options or {}
                    self.application = app
                    super().__init__()

                def load_config(self):
                    for key, value in self.options.items():
                        self.cfg.set(key, value)

                def load(self):
                    return self.application

            options = {"bind": "0.0.0.0:5000", "workers": 2}
            logging.info("🌍 Using Gunicorn WSGI server on Linux/macOS...")
            FlaskApp(app, options).run()

    except Exception as e:
        logging.error(f"❌ Error starting Flask: {e}")


if __name__ == "__main__":
    import asyncio

    # Run Flask in the main thread
    logging.info("🚀 Starting Flask Webhook & Telegram Bot...")
    from waitress import serve  # Use Waitress universally for simplicity

    # Start Flask in a thread (compatible with Waitress)
    flask_thread = threading.Thread(
        target=serve, 
        args=(app,), 
        kwargs={"host": "0.0.0.0", "port": 5000},
        daemon=True
    )
    flask_thread.start()

    # Run the Telegram bot in the main thread
    asyncio.run(run_telegram_bot())


# if __name__ == "__main__":
#     import threading
#     import asyncio

#     logging.info("🚀 Starting Flask Webhook & Telegram Bot...")

#     # ✅ Start Flask in a separate thread
#     threading.Thread(target=run_flask, daemon=True).start()

#     # ✅ Run Telegram bot inside the existing event loop
#     loop = asyncio.get_event_loop()
#     loop.create_task(run_telegram_bot())  # ✅ Run Telegram bot properly

#     loop.run_forever()  # ✅ Keeps the event loop running without crashes