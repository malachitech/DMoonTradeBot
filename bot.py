import os
import logging
import asyncio
import requests
import threading
import base64
import time
# import nest_asyncio
import platform
import json
import datetime
from multiprocessing import Process  
import sys  
import httpx
from waitress import serve
# nest_asyncio.apply()
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
from cryptography.fernet import Fernet
from filelock import FileLock

# Load environment variables
load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TOKEN_MINT = os.getenv("TOKEN_MINT")
TOKEN_DECIMALS = int(os.getenv("TOKEN_DECIMALS", 6))
SOLANA_RPC_URL = os.getenv("SOLANA_RPC_URL")
ADMIN_WALLET = os.getenv("ADMIN_WALLET_ADDRESS")
BOT_WALLET_PRIVATE_KEY = os.getenv("BOT_WALLET_PRIVATE_KEY")
bot_wallet = Keypair.from_base58_string(BOT_WALLET_PRIVATE_KEY)

# File to store permanent wallets
WALLETS_FILE = "bot-wallet.json"
lock = FileLock(WALLETS_FILE + ".lock")
JUPITER_API = os.getenv("JUPITER_API")
DEX_PROGRAM_ID = os.getenv("BOT_WALLET_PRIVATE_KEY")
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")
cipher = Fernet(ENCRYPTION_KEY.encode())

# File storage
WALLETS_FILE = "user_wallets.json"
user_wallets = {}
user_sell_targets = {}
user_entry_prices = {}
user_last_withdrawal = {}
user_active_trades = {}
# Initialize Solana client
solana_client = AsyncClient(SOLANA_RPC_URL)

required_env_vars = [
    "TELEGRAM_BOT_TOKEN",
    "SOLANA_RPC_URL",
    "BOT_WALLET_PRIVATE_KEY",
    "ENCRYPTION_KEY",
    "JUPITER_API",
    "TOKEN_MINT"
]

missing = [var for var in required_env_vars if not os.getenv(var)]
if missing:
    raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

# Validate Jupiter URL format
if not JUPITER_API.startswith(("http://", "https://")):
    raise ValueError("JUPITER_API must include http/https scheme")

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class RateLimiter:
    """Enhanced rate limiting with per-user tracking"""
    def __init__(self, max_calls=3, period=60):
        self.max_calls = max_calls
        self.period = period
        self.users = {}
        
    def check(self, user_id: str) -> bool:
        now = time.time()
        if user_id not in self.users:
            self.users[user_id] = [now]
            return True
            
        # Remove old timestamps
        self.users[user_id] = [t for t in self.users[user_id] if now - t < self.period]
        
        if len(self.users[user_id]) < self.max_calls:
            self.users[user_id].append(now)
            return True
        return False

rate_limiter = RateLimiter(max_calls=5, period=60)

# --- Core Functions ---

def load_wallets():
    """Load and upgrade wallet format if needed"""
    global user_wallets
    try:
        with lock:
            if os.path.exists(WALLETS_FILE):
                with open(WALLETS_FILE, "rb") as f:
                    encrypted = f.read()
                    decrypted = cipher.decrypt(encrypted)
                    raw_wallets = json.loads(decrypted)
                    
                    # Validate wallet structure
                    valid_wallets = {}
                    for user_id, wallet in raw_wallets.items():
                        if all(k in wallet for k in ["address", "encrypted_key"]):
                            valid_wallets[user_id] = {
                                "address": wallet["address"],
                                "encrypted_key": wallet["encrypted_key"],
                                "sol_balance": wallet.get("sol_balance", 0.0),
                                "token_balance": wallet.get("token_balance", 0.0),
                                "transactions": wallet.get("transactions", [])
                            }
                    user_wallets = valid_wallets
    except Exception as e:
        logging.error(f"Wallet load failed: {str(e)}")

def save_wallets():
    try:
        with lock:
            # Preserve existing wallets even if saving fails
            temp_file = WALLETS_FILE + ".tmp"
            encrypted = cipher.encrypt(json.dumps(user_wallets).encode())
            
            with open(temp_file, "wb") as f:
                f.write(encrypted)
                
            os.replace(temp_file, WALLETS_FILE)
    except Exception as e:
        logging.error(f"Wallet save failed: {str(e)}")

async def get_sol_balance(wallet_address: str) -> float:
    """Get SOL balance with retries"""
    for _ in range(3):
        try:
            resp = await solana_client.get_balance(Pubkey.from_string(wallet_address))
            return resp.value / 1e9 if isinstance(resp, GetBalanceResp) else 0.0
        except Exception as e:
            logger.error(f"Balance check failed: {str(e)}")
            await asyncio.sleep(1)
    return 0.0

async def get_token_balance(wallet_address: str) -> float:
    """Get token balance with retries"""
    for _ in range(3):
        try:
            resp = await solana_client.get_token_accounts_by_owner(
                Pubkey.from_string(wallet_address),
                mint=Pubkey.from_string(TOKEN_MINT)
            )
            return sum(
                t.account.data.parsed["info"]["tokenAmount"]["uiAmount"] 
                for t in resp.value
            )
        except Exception as e:
            logger.error(f"Token balance error: {str(e)}")
            await asyncio.sleep(1)
    return 0.0

async def update_wallet_balances(user_id: str):
    """Update and cache balances"""
    if user_id not in user_wallets:
        return
    
    wallet = user_wallets[user_id]
    try:
        wallet["sol_balance"] = await get_sol_balance(wallet["address"])
        wallet["token_balance"] = await get_token_balance(wallet["address"])
        save_wallets()
    except Exception as e:
        logger.error(f"Balance update failed: {str(e)}")




async def execute_swap(user_id: str, is_buy: bool, amount: float) -> dict:
    """Execute DEX swap using Jupiter"""
    try:
        wallet = user_wallets.get(user_id)
        if not wallet:
            return {"status": "error", "message": "Wallet not found"}
            
        # Get current price
        params = {
            "inputMint": TOKEN_MINT if is_buy else "So11111111111111111111111111111111111111112",
            "outputMint": "So11111111111111111111111111111111111111112" if is_buy else TOKEN_MINT,
            "amount": int(amount * (10**9 if is_buy else 10**TOKEN_DECIMALS)),
            "slippageBps": 100  # 1% slippage
        }
        
        # Get quote
        headers = {"Authorization": f"Bearer {os.getenv('JUPITER_API_KEY')}"} if os.getenv("JUPITER_API_KEY") else {}
        response = requests.get(JUPITER_API, params=params, headers=headers)
        response.raise_for_status()
        quote = response.json()
        
        # Build transaction
        transaction = Transaction.deserialize(base64.b64decode(quote["tx"]))
        keypair = Keypair.from_base58_string(cipher.decrypt(wallet["encrypted_key"].encode()).decode())
        transaction.sign(keypair)
        
        # Execute with retries
        for attempt in range(3):
            try:
                result = await solana_client.send_transaction(transaction)
                return {"status": "success", "txid": result.value}
            except Exception as e:
                if "Blockhash" in str(e):
                    await asyncio.sleep(1)
                    continue
                raise
                
        return {"status": "error", "message": "Transaction failed after 3 attempts"}
        
    except Exception as e:
        logger.error(f"Swap error: {str(e)}")
        return {"status": "error", "message": str(e)}





app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

@app.route("/keep-alive", methods=["GET"])
def keep_alive():
    """Prevent hosting platform from sleeping the bot"""
    return "Bot is running", 200

@app.route("/phantom_webhook", methods=["POST"])
def phantom_webhook():
    """Essential for receiving transaction notifications"""
    try:
        data = request.json
        transaction_id = data.get("transactionId")
        if not transaction_id:
            return jsonify({"error": "Missing transactionId"}), 400
            
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(check_transaction(transaction_id))
        
        return jsonify({"status": "success" if result else "failed"}), 200
    except Exception as e:
        logger.error(f"Webhook error: {str(e)}")
        return jsonify({"status": "error"}), 500




async def price_monitor():
    """Monitor prices with enhanced error handling"""
    while True:
        try:
            # Get current price with validation
            params = {
                "inputMint": TOKEN_MINT,
                "outputMint": "So11111111111111111111111111111111111111112",
                "amount": 1 * 10**TOKEN_DECIMALS
            }
            
            response = requests.get(
                JUPITER_API,
                params=params,
                timeout=10,
                headers={"Content-Type": "application/json"}
            )
            response.raise_for_status()
            
            data = response.json()
            current_price = float(data["outAmount"]) / 1e9
            
            # Process price updates
            for user_id, target in user_sell_targets.items():
                entry_price = user_entry_prices.get(user_id, current_price)
                if current_price >= entry_price * target:
                    await handle_sell_now(user_id)
                    
            await asyncio.sleep(60)
            
        except Exception as e:
            logger.error(f"Price monitor error: {str(e)}")
            await asyncio.sleep(300)  # Backoff on errors


        
async def get_token_price(token_address: str):
    try:
        params = {
            "inputMint": token_address,
            "outputMint": "So11111111111111111111111111111111111111112",
            "amount": 1_000_000  # 1 token assuming 6 decimals
        }
        
        response = requests.get(JUPITER_API, params=params)
        return float(response.json()["outAmount"]) / 1_000_000
    except Exception as e:
        logging.error(f"Price check error: {e}")
        return 0

async def start(update: Update, context: CallbackContext):
    """Wallet creation with atomic safety"""
    try:
        user_id = str(update.effective_user.id)
        
        # Load fresh data
        load_wallets()
        
        if user_id in user_wallets:
            # Never overwrite existing wallet
            wallet = user_wallets[user_id]
            await update_wallet_balances(user_id)
            message = (
                f"👋 Welcome back!\n"
                f"📌 Permanent Address: `{wallet['address']}`\n"
                f"💰 SOL Balance: {wallet['sol_balance']:.4f}\n"
                f"🎯 Token Balance: {wallet['token_balance']:.2f}"
            )
        else:
            # Create new wallet atomically
            keypair = Keypair()
            encrypted_key = cipher.encrypt(keypair.to_base58_string().encode()).decode()
            
            new_wallet = {
                "address": str(keypair.pubkey()),
                "encrypted_key": encrypted_key,
                "sol_balance": 0.0,
                "token_balance": 0.0,
                "transactions": []
            }
            
            # Atomic update
            with lock:
                load_wallets()
                if user_id not in user_wallets:
                    user_wallets[user_id] = new_wallet
                    save_wallets()
                else:  # Handle concurrent creation
                    wallet = user_wallets[user_id]
                    
            message = (
                "✅ **Immutable Wallet Created**\n"
                f"📌 Permanent Address: `{new_wallet['address']}`\n"
                "🔐 Private key encrypted & stored securely"
            )

        keyboard = [
            [InlineKeyboardButton("💼 Wallet Info", callback_data="wallet"),
             InlineKeyboardButton("💰 Deposit", callback_data="deposit")],
            [InlineKeyboardButton("🎯 Set Target", callback_data="set_target"),
             InlineKeyboardButton("🔄 Refresh", callback_data="refresh")],
            [InlineKeyboardButton("🚀 Buy Now", callback_data="buy_now"),
             InlineKeyboardButton("📉 Sell Now", callback_data="sell_now")],
            [InlineKeyboardButton("🔍 Solscan", callback_data="solscan"),
             InlineKeyboardButton("❓ Help", callback_data="help")]
        ]
        
        await update.message.reply_text(
            message,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        
    except Exception as e:
        logger.error(f"Start error: {str(e)}")
        await update.message.reply_text("🚨 System error - contact support")

async def handle_sell_now(update: Update, context: CallbackContext):
    """Instant sell with price validation"""
    user_id = str(update.effective_user.id)
    
    try:
        # Get user balance
        await update_wallet_balances(user_id)
        wallet = user_wallets.get(user_id)
        if not wallet or wallet["token_balance"] <= 0:
            await update.message.reply_text("❌ No tokens to sell")
            return
            
        # Execute swap
        result = await execute_swap(user_id, is_buy=False, amount=wallet["token_balance"])
        
        if result["status"] == "success":
            # Update transaction history
            user_wallets[user_id]["transactions"].append({
                "type": "sell",
                "amount": wallet["token_balance"],
                "txid": result["txid"],
                "timestamp": datetime.now().isoformat()
            })
            save_wallets()
            
            await update.message.reply_text(
                f"✅ Sold {wallet['token_balance']:.2f} tokens\n"
                f"🔗 Transaction: https://solscan.io/tx/{result['txid']}"
            )
        else:
            await update.message.reply_text(f"❌ Sell failed: {result['message']}")
            
    except Exception as e:
        logger.error(f"Sell error: {str(e)}")
        await update.message.reply_text("🚨 Critical error during sale")






async def wallet_info(query):
    user_id = query.from_user.id
    if user_id not in user_wallets:
        await query.message.reply_text("No wallet found. Use /start to create one.")
        return
    
    wallet_data = user_wallets[user_id]
    balance = await get_sol_balance(wallet_data["address"])
    message = f"\U0001F4B0 **Wallet Info:**\n\n\U0001F538 **Address:** {wallet_data['address']}\n\U0001F538 **Balance:** {balance:.4f} SOL"
    await query.message.reply_text(message)

    token_balance = await get_token_balance(wallet_data["address"], "DezXAZ...")
    message = f"...\n\U0001F538 Token Balance: {token_balance:.2f} BONK"


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


async def withdraw_phantom(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    now = time.time()

    # Prevent spam (Only allow withdrawal every 60 seconds)
    if user_id in user_last_withdrawal and now - user_last_withdrawal[user_id] < 60:
        await update.message.reply_text("⚠️ You can only withdraw once per minute.")
        return
        
    for attempt in range(3):
        try:
            response = await solana_client.send_transaction(...)
            break
        except Exception as e:
            if attempt == 2:
                await update.message.reply_text("❌ Withdrawal failed after 3 attempts")
            await asyncio.sleep(1)

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
    async with httpx.AsyncClient() as client:  
        try:  
            async with client.websocket_connect("wss://api.example.com/ws") as ws:  
                while True:  
                    message = await ws.receive_text()
                    f"accountSubscribe {bot_wallet.pubkey()} commitment=finalized"
                    print("🔔 New deposit detected:", message)
        except Exception as e:  
            # Log errors (e.g., Sentry, Cloudwatch)  
            logger.error(f"WebSocket error: {e}")


async def set_sell_target(update: Update, context: CallbackContext):
    user_id = str(update.effective_user.id)
    
    if user_id not in user_wallets:
        await update.message.reply_text("❌ Create wallet first with /start")
        return

    try:
        target = float(context.args[0])
        user_sell_targets[user_id] = target
        
        # Store entry price when target is set
        token_address = "TOKEN_ADDRESS_YOU_TRADE"  # e.g., Bonk token address
        current_price = await get_token_price(token_address)
        user_entry_prices[user_id] = current_price
        
        await update.message.reply_text(
            f"🎯 Sell target set to {target}x\n" 
            f"Current price: {current_price} SOL"
        )
    except Exception as e:
        logging.error(f"Set target error: {e}")
        await update.message.reply_text("❌ Invalid target format")

async def buy_now(update: Update, context: CallbackContext):
    user_id = str(update.effective_user.id)
    if user_id not in user_wallets:
        await update.message.reply_text("❌ Create a wallet first with /start")
        return
    
    # Add your custom buy logic here
    await update.message.reply_text(
        "Redirecting to SOL purchase...",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("Buy SOL Now", url="YOUR_BUY_LINK_HERE")]
        ])
    )


async def sell_now(update: Update, context: CallbackContext):
    user_id = str(update.effective_user.id)
    
    try:
        if user_id not in user_wallets:
            await update.message.reply_text("❌ Create wallet first with /start")
            return

        wallet = user_wallets[user_id]
        keypair = Keypair.from_base58_string(cipher.decrypt(wallet["keypair"].encode()).decode())
        
        # Get token balance
        token_address = "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263"  # BONK example
        token_balance = await get_token_balance(wallet["address"], token_address)
        
        # Get best price
        params = {
            "inputMint": token_address,
            "outputMint": "So11111111111111111111111111111111111111112",
            "amount": int(token_balance * 1e6),  # Adjust for decimals
            "slippageBps": 100  # 1% slippage
        }
        
        # Get quote
        quote = requests.get(JUPITER_API, params=params).json()
        
        # Build transaction
        tx = Transaction.deserialize(base64.b64decode(quote["tx"]))
        tx.sign(keypair)
        
        # Execute with retries
        for attempt in range(3):
            try:
                result = await solana_client.send_transaction(tx)
                await update.message.reply_text(
                    f"✅ Sold {token_balance:.2f} BONK\n"
                    f"🔗 Tx: https://solscan.io/tx/{result.value}"
                )
                return
            except Exception as e:
                if "Blockhash" in str(e):
                    await asyncio.sleep(1)
                    continue
                raise
                
        await update.message.reply_text("❌ Transaction failed after 3 attempts")

    except Exception as e:
        logging.error(f"Sell error: {str(e)}")
        await update.message.reply_text(f"⚠️ Error: {str(e)}")



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
    user_id = str(query.from_user.id)  # Define here first
    
    # Existing button handling...
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
    elif query.data == "view_solscan":
        user_id = query.from_user.id
        if user_id in user_wallets:
            wallet_address = user_wallets[user_id]["address"]
            solscan_url = f"https://solscan.io/account/{wallet_address}"
            await query.message.reply_text(
                f"🔍 View your wallet on Solscan:\n{solscan_url}",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("Open Solscan", url=solscan_url)]]
                )
            )
        else:
            await query.message.reply_text("No wallet found. Use /start to create one.")   
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


def run_flask():  
    # Production (Railway) uses Gunicorn; locally use Waitress  
    if 'gunicorn' in sys.argv:  
        from gunicorn.app.base import BaseApplication  
        class FlaskApp(BaseApplication):  
            def __init__(self, app, options=None):  
                self.app = app  
                self.options = options or {}  
                super().__init__()  
            def load(self):  
                return self.app  
        FlaskApp(app, {'bind': '0.0.0.0:${PORT:-5000}'}).run()  
    else:  
        # Local development with Waitress  
        serve(app, host='0.0.0.0', port=5000)

async def run_telegram_bot():
    bot = Application.builder().token(TOKEN).build()

    # ✅ Register all command handlers
    bot.add_handler(CommandHandler("start", start))
    bot.add_handler(CommandHandler("withdraw", withdraw_phantom))
    bot.add_handler(CommandHandler("set_target", set_sell_target))
    bot.add_handler(CommandHandler("active_trades", active_trades))
    bot.add_handler(CommandHandler("sell_now", sell_now))
    bot.add_handler(CommandHandler("buy_now", buy_now))
    bot.add_handler(CommandHandler("help", help_command))
    bot.add_handler(CallbackQueryHandler(handle_button_click))

    logging.info("🤖 Telegram Bot is Running and Polling for Updates...")

    # ✅ Check if the event loop is already running
    try:
        await bot.run_polling(allowed_updates=Update.ALL_TYPES)
    except RuntimeError as e:
        logging.error(f"⚠️ Event loop error: {e}")


def run_bot_async_wrapper():  
    asyncio.run(run_telegram_bot())

if __name__ == "__main__":
    import nest_asyncio
    nest_asyncio.apply()  # ✅ Fixes "event loop already running" issue

    loop = asyncio.get_event_loop()
    loop.create_task(run_telegram_bot())  # ✅ Run bot without blocking event loop
    loop.run_forever()  # ✅ Keeps everything running