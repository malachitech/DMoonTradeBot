import os
import logging
import asyncio
import requests
import json
import sqlite3
import httpx
import base64
import nest_asyncio
import sys
import sqlite3
import time
import json
import asyncio
from collections import deque


from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackContext, CallbackQueryHandler, ConversationHandler, MessageHandler, filters
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.transaction import Transaction
from solders.system_program import TransferParams, transfer
from solana.rpc.async_api import AsyncClient
from flask import Flask, request, jsonify
from cryptography.fernet import Fernet
from filelock import FileLock
from waitress import serve  # ✅ Production server

# ✅ Apply async patch for nested loops
nest_asyncio.apply()

# ✅ Load environment variables
load_dotenv()

# ✅ Required env variables
REQUIRED_ENV_VARS = {
    "TELEGRAM_BOT_TOKEN",
    "SOLANA_RPC_URL",
    "BOT_WALLET_PRIVATE_KEY",
    "ENCRYPTION_KEY",
    "JUPITER_API",
    "TOKEN_MINT"
}

# ✅ Check for missing environment variables
missing_vars = REQUIRED_ENV_VARS - os.environ.keys()
if missing_vars:
    raise ValueError(f"🚨 Missing required environment variables: {', '.join(missing_vars)}")

# ✅ Load env variables
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
SOLANA_RPC_URL = os.getenv("SOLANA_RPC_URL")
BOT_WALLET_PRIVATE_KEY = os.getenv("BOT_WALLET_PRIVATE_KEY")
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")
JUPITER_API = os.getenv("JUPITER_API")
TOKEN_MINT = os.getenv("TOKEN_MINT")
TOKEN_DECIMALS = int(os.getenv("TOKEN_DECIMALS", 6))
ADMIN_WALLET = os.getenv("ADMIN_WALLET_ADDRESS")
DEX_PROGRAM_ID = os.getenv("DEX_PROGRAM_ID")  # ✅ Fixed!

# ✅ Secure encryption setup
if not ENCRYPTION_KEY:
    raise ValueError("🚨 ENCRYPTION_KEY is required for wallet security!")
cipher = Fernet(ENCRYPTION_KEY.encode())

# ✅ Secure bot wallet setup
try:
    bot_wallet = Keypair.from_base58_string(BOT_WALLET_PRIVATE_KEY)
    bot_wallet_pubkey = str(bot_wallet.pubkey())  # ✅ Convert to Base58 string
except Exception as e:
    raise ValueError(f"🚨 Failed to load bot wallet: {str(e)}")

# ✅ Wallet storage files
WALLETS_FILE = "user_wallets.json"
lock = FileLock(WALLETS_FILE + ".lock")

# ✅ Initialize Solana client
solana_client = AsyncClient(SOLANA_RPC_URL)

# ✅ User state tracking
user_wallets = {}
user_sell_targets = {}
user_sell_amounts = {}
user_entry_prices = {}
user_last_withdrawal = {}
user_active_trades = {}
user_buy_targets = {}
BUY_TARGET_INPUT = range(1)
# ✅ Define conversation state for input handling
TARGET_INPUT = range(1)

# ✅ Configure logging securely
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

@app.route("/keep-alive", methods=["GET"])
def keep_alive():
    """Prevent hosting platform from sleeping the bot"""
    return "Bot is running", 200

# @app.route("/phantom_webhook", methods=["POST"])
# def phantom_webhook():
#     """Essential for receiving transaction notifications"""
#     try:
#         data = request.json
#         transaction_id = data.get("transactionId")
#         if not transaction_id:
#             return jsonify({"error": "Missing transactionId"}), 400
            
#         loop = asyncio.new_event_loop()
#         asyncio.set_event_loop(loop)
#         result = loop.run_until_complete(check_transaction(transaction_id))
        
#         return jsonify({"status": "success" if result else "failed"}), 200
#     except Exception as e:
#         logger.error(f"Webhook error: {str(e)}")
#         return jsonify({"status": "error"}), 500


class RateLimiter:
    """Enhanced rate limiting with per-user tracking using deque."""
    def __init__(self, max_calls=5, period=60):
        self.max_calls = max_calls
        self.period = period
        self.users = {}

    def check(self, user_id: str) -> bool:
        now = time.time()

        # ✅ Initialize user rate limit if missing
        if user_id not in self.users:
            self.users[user_id] = deque(maxlen=self.max_calls)

        # ✅ Remove old timestamps
        while self.users[user_id] and now - self.users[user_id][0] > self.period:
            self.users[user_id].popleft()

        # ✅ Allow request if within limit
        if len(self.users[user_id]) < self.max_calls:
            self.users[user_id].append(now)
            return True

        return False

rate_limiter = RateLimiter(max_calls=5, period=60)

# ✅ Set up the database only once

def setup_database():
    """Ensures the transactions table is created on startup."""
    conn = sqlite3.connect("trading_bot.db")
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            amount_sold REAL NOT NULL,
            target_price REAL NOT NULL,
            transaction_id TEXT UNIQUE NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()
    logging.info("✅ Database setup complete.")

# ✅ Function to securely log transactions

def log_transaction(user_id, sell_amount, target_price, txid):
    """Logs transactions securely in SQLite3."""
    try:
        conn = sqlite3.connect("trading_bot.db")
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO transactions (user_id, amount_sold, target_price, transaction_id)
            VALUES (?, ?, ?, ?)
        """, (user_id, sell_amount, target_price, txid))

        conn.commit()
        logging.info(f"✅ Transaction logged: {sell_amount} tokens sold at {target_price} SOL")

    except sqlite3.Error as e:
        logging.error(f"🚨 Database Error: {str(e)}")

    finally:
        conn.close()




# ✅ Load wallets securely
def load_wallets():
    """Load and upgrade wallet format if needed, handling corrupted files."""
    global user_wallets
    try:
        with lock:
            if os.path.exists(WALLETS_FILE):
                with open(WALLETS_FILE, "rb") as f:
                    encrypted = f.read()

                decrypted = cipher.decrypt(encrypted)

                try:
                    raw_wallets = json.loads(decrypted)
                except json.JSONDecodeError:
                    logging.error("🚨 Wallet data corrupted. Resetting to empty wallets.")
                    raw_wallets = {}

                # ✅ Validate wallet structure
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
                    else:
                        logging.warning(f"⚠️ Wallet for {user_id} is missing fields and was skipped.")

                user_wallets = valid_wallets
                logging.info(f"✅ Loaded wallets: {len(user_wallets)} users")

    except Exception as e:
        logging.error(f"🚨 Wallet load failed: {str(e)}")

# ✅ Securely save wallets
def save_wallets():
    try:
        with lock:
            temp_file = WALLETS_FILE + ".tmp"
            encrypted = cipher.encrypt(json.dumps(user_wallets).encode())

            with open(temp_file, "wb") as f:
                f.write(encrypted)

            os.replace(temp_file, WALLETS_FILE)
    except Exception as e:
        logging.error(f"🚨 Wallet save failed: {str(e)}")


async def get_sol_balance(wallet_address: str) -> float:
    """Fetch SOL balance securely with error handling and retries."""
    load_wallets()
    
    for attempt in range(3):  # Retry up to 3 times
        try:
            response = await solana_client.get_balance(Pubkey.from_string(wallet_address))
            
            # ✅ Ensure response is valid before processing
            if response and isinstance(response.value, int):
                return response.value / 1e9  # Convert lamports to SOL

            logger.warning(f"⚠️ Unexpected balance response: {response}")

        except Exception as e:
            logger.error(f"⚠️ Attempt {attempt + 1}: Balance check failed for {wallet_address} - {str(e)}")
            await asyncio.sleep(1)  # Short delay before retrying

    logger.warning(f"❌ Failed to fetch balance for {wallet_address} after 3 attempts.")
    return 0.0  # Return 0 SOL if all retries fail

# # ✅ Fetch SOL balance securely with retries
# async def get_sol_balance(wallet_address: str) -> float:
#     """Fetch SOL balance securely with error handling and retries."""
#     load_wallets()
#     for _ in range(3):  # Retry logic for robustness
#         try:
#             response = await solana_client.get_balance(Pubkey.from_string(wallet_address))
            
#             # ✅ Handle response structure correctly
#             if response and response.value:
#                 return response.value / 1e9  # Convert lamports to SOL

#         except Exception as e:
#             logger.error(f"⚠️ Balance check failed: {str(e)}")
#             await asyncio.sleep(1)  # Retry after a short delay

#     return 0.0  # Return 0 SOL if all retries fail


# ✅ Fetch token balance securely
async def get_token_balance(wallet_address: str) -> float:
    """Fetch token balance securely with error handling."""
    try:
        wallet_pubkey = Pubkey.from_string(wallet_address)
        response = await solana_client.get_token_accounts_by_owner(wallet_pubkey, encoding="jsonParsed")

        # ✅ Validate API response before parsing
        if response and "result" in response and "value" in response["result"]:
            accounts = response["result"]["value"]

            if accounts:
                token_amount = accounts[0]["account"]["data"]["parsed"]["info"]["tokenAmount"]["uiAmount"]
                return float(token_amount)

        logging.warning(f"⚠️ No token balance found for {wallet_address}")
        return 0.0

    except Exception as e:
        logging.error(f"🚨 Token balance retrieval error: {str(e)}")
        return 0.0

# ✅ Update wallet balances efficiently
async def update_wallet_balances(user_id: str):
    """Update and cache balances securely."""
    load_wallets()
    if user_id not in user_wallets:
        return

    wallet = user_wallets[user_id]
    try:
        wallet["sol_balance"] = await get_sol_balance(wallet["address"])
        wallet["token_balance"] = await get_token_balance(wallet["address"])
        save_wallets()
    except Exception as e:
        logger.error(f"⚠️ Balance update failed: {str(e)}")





async def execute_swap(user_id: str, is_buy: bool, amount: float) -> dict:
    """Execute DEX swap using Jupiter API with error handling"""
    try:
        wallet = user_wallets.get(user_id)
        if not wallet:
            return {"status": "error", "message": "Wallet not found"}

        params = {
            "inputMint": TOKEN_MINT if is_buy else "So11111111111111111111111111111111111111112",
            "outputMint": "So11111111111111111111111111111111111111112" if is_buy else TOKEN_MINT,
            "amount": int(amount * (10**9 if is_buy else 10**TOKEN_DECIMALS)),
            "slippageBps": 100  # 1% slippage
        }

        headers = {"Authorization": f"Bearer {os.getenv('JUPITER_API_KEY')}"} if os.getenv("JUPITER_API_KEY") else {}
        response = requests.get(JUPITER_API, params=params, headers=headers)
        response.raise_for_status()

        # ✅ Handle missing or incorrect API response
        try:
            quote = response.json()
            if "tx" not in quote:
                raise ValueError("Invalid API response: 'tx' field missing")
        except (ValueError, KeyError, json.JSONDecodeError) as e:
            logger.error(f"🚨 API response error: {str(e)}")
            return {"status": "error", "message": "Failed to get swap transaction"}

        transaction = Transaction.deserialize(base64.b64decode(quote["tx"]))
        keypair = Keypair.from_base58_string(cipher.decrypt(wallet["encrypted_key"].encode()).decode())
        transaction.sign(keypair)

        # ✅ Execute transaction with retries
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
        logger.error(f"🚨 Swap error: {str(e)}")
        return {"status": "error", "message": str(e)}


async def handle_sell_now(user_id):
    """Securely execute a sell transaction when target price is met."""
    if user_id not in user_wallets:
        logging.warning(f"User {user_id} does not have a wallet.")
        return

    wallet = user_wallets[user_id]
    user_balance = await get_token_balance(wallet["address"])

    if user_balance <= 0:
        logging.warning(f"User {user_id} has no tokens to sell.")
        return

    sell_amount = user_balance  # Selling full balance (adjustable)
    target_price = user_sell_targets.get(user_id, None)

    if not target_price:
        logging.warning(f"User {user_id} has no sell target set.")
        return

    await sell_now(user_id, sell_amount, target_price)


async def price_monitor():
    """Monitor prices with enhanced error handling"""
    while True:
        try:
            # Fetch current price once
            current_price = await get_token_price(TOKEN_MINT)

            if not current_price:
                logger.warning("Failed to fetch price, skipping this cycle.")
                await asyncio.sleep(300)  # Backoff on failure
                continue  # Skip iteration if price is invalid

            # Process price updates for all users
            for user_id, target in user_sell_targets.items():
                entry_price = user_entry_prices.get(user_id, current_price)
                
                # Check if the price target is met
                if current_price >= entry_price * target:
                    await handle_sell_now(user_id)  # Execute sell
            
            await asyncio.sleep(60)  # Check prices every 60 seconds

        except Exception as e:
            logger.error(f"Price monitor error: {str(e)}")
            await asyncio.sleep(300)  # Backoff on errors

async def get_token_price(token_address: str):
    """Fetches the token price from Jupiter API asynchronously."""
    try:
        params = {
            "inputMint": token_address,
            "outputMint": "So11111111111111111111111111111111111111112",
            "amount": 1_000_000  # 1 token assuming 6 decimals
        }

        async with httpx.AsyncClient() as client:
            response = await client.get(JUPITER_API, params=params, timeout=10)
            response.raise_for_status()  # ✅ Raise error if request fails

            return float(response.json()["outAmount"]) / 1_000_000
    except Exception as e:
        logging.error(f"🚨 Price check error: {e}")
        return 0  # ✅ Return 0 instead of crashing
        
# async def get_token_price(token_address: str):
#     try:
#         params = {
#             "inputMint": token_address,
#             "outputMint": "So11111111111111111111111111111111111111112",
#             "amount": 1_000_000  # 1 token assuming 6 decimals
#         }
        
#         response = requests.get(JUPITER_API, params=params)
#         return float(response.json()["outAmount"]) / 1_000_000
#     except Exception as e:
#         logging.error(f"Price check error: {e}")
#         return 0

async def start(update: Update, context: CallbackContext):
    """Handles wallet creation (only once) with atomic safety, encryption, and UI buttons."""
    try:
        user_id = str(update.effective_user.id)
        load_wallets()
        logging.info(f"✅ /start command received from user {user_id}")

        if user_id in user_wallets:
            # ✅ If the user has a wallet, do NOT replace or modify it
            wallet = user_wallets[user_id]
            await update_wallet_balances(user_id)

            message = (
                f"👋 **Welcome back!**\n"
                f"📌 **Your Permanent Wallet Address:** `{wallet['address']}`\n"
                f"💰 **SOL Balance:** {wallet['sol_balance']:.4f} SOL\n"
                f"🎯 **Token Balance:** {wallet['token_balance']:.2f} Tokens\n\n"
                "🔒 **Your wallet is permanently stored and cannot be replaced.**"
            )

        else:
            # ✅ Create a new wallet only if the user does NOT have one
            keypair = Keypair()
            encrypted_key = cipher.encrypt(keypair.to_bytes()).decode()
            # encrypted_key = cipher.encrypt(keypair.to_base58_string().encode()).decode()

            new_wallet = {
                "address": str(keypair.pubkey()),  # Store as string
                "encrypted_key": encrypted_key,   # Secure private key storage
                "sol_balance": 0.0,
                "token_balance": 0.0,
                "transactions": []
            }

            # ✅ Atomic update: Prevent overwriting existing wallets
            with lock:
                load_wallets()
                if user_id not in user_wallets:  # Double-check to prevent overwriting
                    user_wallets[user_id] = new_wallet
                    save_wallets()
                    message = (
                        "✅ **Wallet Created**\n"
                        f"📌 **Your Address:** `{new_wallet['address']}`\n"
                        "🔐 **Your private key is encrypted & stored securely.**\n\n"
                        "⚠️ **This wallet is PERMANENT and cannot be changed.**"
                    )
                else:
                    # Edge case: If another process created a wallet simultaneously
                    wallet = user_wallets[user_id]
                    message = (
                        "⚠️ **Wallet creation interrupted, but your wallet is safe!**\n"
                        f"📌 **Your Permanent Address:** `{wallet['address']}`"
                    )

        # ✅ Inline Keyboard Buttons for quick actions
        keyboard = [
            [InlineKeyboardButton("💼 Wallet Info", callback_data="wallet"),
            InlineKeyboardButton("💰 Deposit", callback_data="deposit")],
            [InlineKeyboardButton("🎯 Set Sell Target", callback_data="set_sell_target"),
            InlineKeyboardButton("📈 Set Buy Target", callback_data="set_buy_target")],
            [InlineKeyboardButton("🚀 Buy Now", callback_data="buy_now"),
            InlineKeyboardButton("📉 Sell Now", callback_data="sell_now")],
            [InlineKeyboardButton("📜 Transaction History", callback_data="transaction_history"),
            InlineKeyboardButton("🚫 Cancel Sell", callback_data="cancel_sell")],
            [InlineKeyboardButton("🔍 Solscan", callback_data="view_solscan"),
            InlineKeyboardButton("❓ Help", callback_data="help")]
        ]

        await update.message.reply_text(
            message,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    except Exception as e:
        logging.error(f"🚨 Start error: {str(e)}")
        await update.message.reply_text("🚨 **System error - contact support.**")


async def wallet_info(update: Update, context: CallbackContext):
    """Show user wallet details."""
    query = update.callback_query  # ✅ Get the query data
    user_id = str(query.from_user.id)
    load_wallets()

    if user_id not in user_wallets:
        await query.message.reply_text("❌ No wallet found. Use /start to create one.")
        return
    
    wallet_data = user_wallets[user_id]
    balance = await get_sol_balance(wallet_data["address"])

    message = (
        f"💰 **Wallet Info:**\n\n"
        f"📌 **Address:** {wallet_data['address']}\n"
        f"🔹 **SOL Balance:** {balance:.4f} SOL"
    )
    
    await query.message.reply_text(message)

def generate_moonpay_link(user_id: str) -> str:
    """Generates a MoonPay deposit link with the user's wallet address."""
    load_wallets()
    
    if user_id not in user_wallets:
        return "https://buy.moonpay.com/?apiKey=pk_live_tgPovrzh9urHG1HgjrxWGq5xgSCAAz&showWalletAddressForm=true&currencyCode=sol"
    
    wallet_address = user_wallets[user_id]["address"]
    
    # ✅ Dynamically create the deposit link
    moonpay_link = (
        f"https://buy.moonpay.com/?apiKey=pk_live_tgPovrzh9urHG1HgjrxWGq5xgSCAAz"
        f"&walletAddress={wallet_address}&showWalletAddressForm=true&currencyCode=sol"
    )
    return moonpay_link


async def deposit_info(query):
    load_wallets()
    user_id = str(query.from_user.id)
    with lock:
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
    user_id = str(update.effective_user.id)
    load_wallets()
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

async def execute_buy(user_id, buy_amount, current_price, context: CallbackContext):
    """Executes a buy transaction securely"""
    if user_id not in user_wallets:
        logging.warning(f"User {user_id} has no wallet.")
        return

    user_wallet = user_wallets[user_id]
    user_address = user_wallet["address"]
    user_private_key = cipher.decrypt(user_wallet["encrypted_key"].encode()).decode()

    # Convert private key back to Keypair
    buyer_keypair = Keypair.from_base58_string(user_private_key)

    # Check user's SOL balance
    user_balance = await get_sol_balance(user_address)
    total_cost = buy_amount * current_price

    if total_cost > user_balance:
        logging.warning(f"User {user_id} has insufficient SOL balance.")
        await context.bot.send_message(chat_id=user_id, text="🚨 **Insufficient SOL balance!** Deposit more SOL to buy.")
        return

    # Construct transaction
    transaction = Transaction()
    
    params = TransferParams(
        from_pubkey=buyer_keypair.pubkey(),
        to_pubkey=Pubkey.from_string("TOKEN_LIQUIDITY_POOL_ADDRESS"),  # Token exchange address
        lamports=int(total_cost * 1e9),  # Convert SOL to lamports
    )
    
    transaction.add(transfer(params))

    # ✅ Sign the transaction securely
    signed_tx = transaction.sign([buyer_keypair])

    # ✅ Verify before broadcasting
    if not signed_tx.verify():
        logging.error("🚨 Buy transaction signature verification failed.")
        await context.bot.send_message(chat_id=user_id, text="🚨 **Transaction failed! Invalid signature.**")
        return

    # Send and confirm transaction
    try:
        response = await solana_client.send_transaction(signed_tx, buyer_keypair)
        log_transaction(user_id, buy_amount, current_price, response)  # ✅ Log to DB

        await context.bot.send_message(
            chat_id=user_id,
            text=f"✅ **Auto-Buy Order Executed**\n"
                 f"🔔 Bought {buy_amount} tokens at {current_price:.4f} SOL\n"
                 f"📄 Transaction ID: {response}"
        )
        logging.info(f"✅ User {user_id} bought {buy_amount} tokens at {current_price} SOL")

    except Exception as e:
        logging.error(f"Buy transaction failed: {e}")
        await context.bot.send_message(chat_id=user_id, text="🚨 **Buy Order Failed**. Please check your wallet.")

async def cancel_buy(update: Update, context: CallbackContext):
    """Allows users to cancel a pending buy order"""
    user_id = str(update.effective_user.id)

    if user_id not in user_buy_targets:
        await update.message.reply_text("❌ You don't have any active buy orders.")
        return

    del user_buy_targets[user_id]
    
    await update.message.reply_text("✅ Your buy order has been canceled.")
    logging.info(f"User {user_id} canceled their buy order.")

async def monitor_bot_wallet():
    load_wallets()
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


async def monitor_market():
    """Periodically checks if the price has hit any buy targets."""
    while True:
        try:
            for user_id, buy_order in list(user_buy_targets.items()):
                target_price = buy_order["price"]
                buy_amount = buy_order["amount"]
                
                token_address = "TOKEN_ADDRESS_YOU_TRADE"
                current_price = await get_token_price(token_address)

                if current_price <= target_price:
                    logging.info(f"🔔 Market Dip Detected! Buying for {user_id} at {current_price:.4f} SOL")
                    await execute_buy(user_id, buy_amount, current_price)
                    
                    # Remove buy order after execution
                    del user_buy_targets[user_id]

            await asyncio.sleep(30)  # Check prices every 30 seconds

        except Exception as e:
            logging.error(f"Market monitoring error: {e}")
            await asyncio.sleep(10)  # Retry after 10s if error occurs




async def set_buy_target(update: Update, context: CallbackContext):
    """Ask the user for a buy target."""
    user_id = str(update.effective_user.id)
    load_wallets()

    if user_id not in user_wallets:
        await update.message.reply_text("❌ You need to create a wallet first using /start.")
        return ConversationHandler.END

    await update.message.reply_text("📉 Enter the buy target price in SOL (e.g., 0.005).")
    return BUY_TARGET_INPUT  # Move to next step

async def receive_buy_target(update: Update, context: CallbackContext):
    """Process the user's buy target input."""
    user_id = str(update.effective_user.id)
    load_wallets()

    try:
        target_price = float(update.message.text)

        if target_price <= 0:
            await update.message.reply_text("❌ Invalid value. Enter a positive number.")
            return BUY_TARGET_INPUT  # Ask again

        # Store buy order with default amount (user can update later)
        user_buy_targets[user_id] = {"price": target_price, "amount": 1000}  # Default amount

        await update.message.reply_text(
            f"✅ **Auto-Buy Order Set!**\n"
            f"🔹 Buy **1000 tokens** when price drops to **{target_price:.4f} SOL**.\n"
            "🔄 You can modify this amount anytime."
        )
        logging.info(f"User {user_id} set buy order at {target_price} SOL")

        return ConversationHandler.END  # End conversation

    except ValueError:
        await update.message.reply_text("❌ Invalid input. Enter a valid number.")
        return BUY_TARGET_INPUT  # Ask again

SELL_TARGET_INPUT = range(1)

async def set_sell_target(update: Update, context: CallbackContext):
    """Ask the user for a sell target."""
    user_id = str(update.effective_user.id)
    load_wallets()

    if user_id not in user_wallets:
        await update.message.reply_text("❌ You need to create a wallet first using /start.")
        return ConversationHandler.END

    await update.message.reply_text("🎯 Enter the sell target multiplier (e.g., 2.0).")
    return SELL_TARGET_INPUT  # Move to next step

async def receive_sell_target(update: Update, context: CallbackContext):
    """Process the user's sell target input."""
    user_id = str(update.effective_user.id)
    load_wallets()

    try:
        target_multiplier = float(update.message.text)

        if target_multiplier <= 1:
            await update.message.reply_text("❌ The target multiplier must be greater than 1.0.")
            return SELL_TARGET_INPUT  # Ask again

        # Store sell target multiplier
        user_sell_targets[user_id] = target_multiplier

        await update.message.reply_text(
            f"✅ **Sell Target Set!**\n"
            f"🔹 The bot will sell when the price reaches **{target_multiplier}x** of the entry price."
        )
        logging.info(f"User {user_id} set sell target at {target_multiplier}x")

        return ConversationHandler.END  # End conversation

    except ValueError:
        await update.message.reply_text("❌ Invalid input. Enter a valid number.")
        return SELL_TARGET_INPUT  # Ask again

# async def set_sell_target(update: Update, context: CallbackContext):
#     """Handles sell target setup from both button clicks and commands."""
    
#     # ✅ Support both commands & button clicks
#     query = update.callback_query
#     user_id = str(update.effective_user.id) if update.message else str(query.from_user.id)

#     load_wallets()
#     if user_id not in user_wallets:
#         if query:
#             await query.message.reply_text("❌ No wallet found. Use /start to create one.")
#         else:
#             await update.message.reply_text("❌ No wallet found. Use /start to create one.")
#         return ConversationHandler.END

#     # ✅ Reply properly based on how function is called
#     if query:
#         await query.message.reply_text("🎯 Please enter your sell target multiplier (e.g., 2.0)")
#     else:
#         await update.message.reply_text("🎯 Please enter your sell target multiplier (e.g., 2.0)")
    
#     return TARGET_INPUT  # Move to next step in conversation


# async def receive_target_input(update: Update, context: CallbackContext):
#     """Step 2: Store user input and set sell target."""
#     user_id = str(update.effective_user.id)
#     load_wallets()
#     try:
#         target = float(update.message.text)  # Get user input as float
#         user_sell_targets[user_id] = target

#         # Store entry price when target is set
#         token_address = "TOKEN_ADDRESS_YOU_TRADE"  # e.g., Bonk token address
#         current_price = await get_token_price(token_address)
#         user_entry_prices[user_id] = current_price

#         await update.message.reply_text(
#             f"✅ Sell target set to {target}x\n" 
#             f"📌 Current price: {current_price} SOL\n"
#             f"🔔 The bot will sell when the price reaches {target * current_price} SOL."
#         )
#         return ConversationHandler.END  # End conversation

#     except ValueError:
#         await update.message.reply_text("❌ Invalid input. Please enter a valid number.")
#         return TARGET_INPUT  # Ask again

# # ✅ Add conversation handler in bot setup
# conv_handler = ConversationHandler(
#     entry_points=[CommandHandler("set_target", set_sell_target)],
#     states={TARGET_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_target_input)]},
#     fallbacks=[]
# )


async def buy_now(update: Update, context: CallbackContext):
    user_id = str(update.effective_user.id)

    if user_id not in user_wallets:
        await context.bot.send_message(chat_id=user_id, text="❌ You need to create a wallet first using /start.")
        return

    if user_id not in user_buy_targets:
        await context.bot.send_message(chat_id=user_id, text="❌ No buy order found. Use /set_buy to create one.")
        return

    buy_order = user_buy_targets[user_id]
    await execute_buy(user_id, buy_order["amount"], buy_order["price"], context)



async def sell_now(user_id, sell_amount, target_price, context: CallbackContext):
    """Securely execute a signed sell transaction in production."""
    
    if user_id not in user_wallets:
        logging.warning(f"🚨 User {user_id} does not have a wallet.")
        await context.bot.send_message(chat_id=user_id, text="❌ You need to create a wallet first using /start.")
        return

    if user_id not in user_sell_targets:
        await context.bot.send_message(chat_id=user_id, text="❌ No sell order found. Use /set_sell_target to create one.")
        return

    user_wallet = user_wallets[user_id]
    user_address = user_wallet["address"]
    
    try:
        user_private_key = cipher.decrypt(user_wallet["encrypted_key"].encode()).decode()
        seller_keypair = Keypair.from_base58_string(user_private_key)  # Convert private key back to Keypair
    except Exception as e:
        logging.error(f"🔒 Private key decryption failed for user {user_id}: {e}")
        await context.bot.send_message(chat_id=user_id, text="🚨 Error accessing wallet. Please contact support.")
        return

    # ✅ Check if user has enough tokens
    user_balance = await get_token_balance(user_address, "TOKEN_ADDRESS_YOU_TRADE")
    if sell_amount > user_balance:
        logging.warning(f"⚠️ User {user_id} has insufficient balance ({user_balance} tokens).")
        await context.bot.send_message(chat_id=user_id, text="🚨 **Insufficient Balance!** You do not have enough tokens to sell.")
        return

    # ✅ Create and sign the transaction securely
    try:
        recipient_address = ADMIN_WALLET  # Admin wallet receives the sold tokens
        transaction = Transaction()
        
        params = TransferParams(
            from_pubkey=seller_keypair.pubkey(),
            to_pubkey=Pubkey.from_string(recipient_address),
            lamports=int(sell_amount * 1e9),  # Convert tokens to lamports
        )
        
        transaction.add(transfer(params))

        # ✅ Securely fetch blockhash and sign transaction
        blockhash_resp = await solana_client.get_latest_blockhash()
        transaction.recent_blockhash = blockhash_resp.value.blockhash
        transaction.partial_sign([seller_keypair])  # Securely sign transaction

        # ✅ Send transaction and confirm success
        response = await solana_client.send_transaction(transaction, seller_keypair)
        confirmed = await solana_client.confirm_transaction(response.value)

        if confirmed.value.err is None:
            # ✅ Log transaction in database
            log_transaction(user_id, sell_amount, target_price, response.value)

            # ✅ Notify user of success
            await context.bot.send_message(
                chat_id=user_id,
                text=f"✅ **Sell Order Executed Successfully**\n"
                     f"🔔 Sold {sell_amount} tokens at {target_price:.4f} SOL\n"
                     f"🔗 [View Transaction](https://solscan.io/tx/{response.value})"
            )
            logging.info(f"✅ User {user_id} sold {sell_amount} tokens at {target_price} SOL")

        else:
            raise Exception(f"Transaction failed: {confirmed.value.err}")

    except Exception as e:
        logging.error(f"❌ Sell transaction failed for user {user_id}: {e}")
        await context.bot.send_message(chat_id=user_id, text="🚨 **Sell Order Failed**. Please check your wallet and try again.")



# async def set_buy_target(update: Update, context: CallbackContext):
#     """Allows users to set a buy target for auto-purchase"""
#     user_id = str(update.effective_user.id)

#     if user_id not in user_wallets:
#         await context.bot.send_message(chat_id=user_id, text="❌ You need to create a wallet first using /start.")
#         return

#     query = update.callback_query  # Handle button clicks
#     if query:
#         await query.answer()
#         message_func = query.message.reply_text
#     else:
#         message_func = update.message.reply_text

#     try:
#         if not context.args or len(context.args) != 2:
#             await message_func("❌ Usage: /set_buy <target_price> <amount>")
#             return

#         target_price = float(context.args[0])  # Buy price in SOL
#         buy_amount = float(context.args[1])  # Amount of tokens to buy

#         if target_price <= 0 or buy_amount <= 0:
#             await message_func("❌ Invalid values. Enter positive numbers.")
#             return

#         # Store buy order
#         user_buy_targets[user_id] = {"price": target_price, "amount": buy_amount}

#         await message_func(
#             f"✅ **Auto-Buy Order Set!**\n"
#             f"🔹 Buy **{buy_amount} tokens** when price drops to **{target_price:.4f} SOL**."
#         )
#         logging.info(f"User {user_id} set buy order: {buy_amount} tokens at {target_price} SOL")

#     except ValueError:
#         await message_func("❌ Invalid format. Use numbers like: `/set_buy 0.005 1000`.")
  


async def transaction_history(update: Update, context: CallbackContext):
    """Shows the user's last 5 transactions."""
    user_id = str(update.effective_user.id)
    
    conn = sqlite3.connect("transactions.db")
    cursor = conn.cursor()
    
    cursor.execute("SELECT amount, target_price, transaction_id, timestamp FROM transactions WHERE user_id = ? ORDER BY id DESC LIMIT 5", (user_id,))
    transactions = cursor.fetchall()
    
    conn.close()

    if not transactions:
        await update.message.reply_text("📜 No transactions found.")
        return

    message = "**📜 Last 5 Transactions:**\n"
    for amount, target_price, txn_id, timestamp in transactions:
        message += f"\n🔹 {amount} tokens @ {target_price:.4f} SOL\n🕒 {timestamp}\n📄 TxID: `{txn_id}`\n"

    await update.message.reply_text(message, parse_mode="Markdown")



async def cancel_sell(update: Update, context: CallbackContext):
    """Allows users to cancel their pending sell order"""
    user_id = str(update.effective_user.id)

    if user_id not in user_sell_targets:
        await update.message.reply_text("❌ You don't have any active sell orders.")
        return

    # Remove sell target & amount
    del user_sell_targets[user_id]
    del user_sell_amounts[user_id]
    
    await update.message.reply_text("✅ Your sell order has been canceled.")
    logging.info(f"User {user_id} canceled their sell order.")



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

async def view_solscan(update: Update, context: CallbackContext):
    query = update.callback_query
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



# ✅ Check Solana Transaction Validity
async def check_transaction(transaction_id):
    load_wallets()
    async with AsyncClient(SOLANA_RPC_URL) as client:
        response = await client.get_confirmed_transaction(transaction_id)
        return bool(response and response.get("result"))




async def handle_button_click(update: Update, context: CallbackContext):
    """Handles all button interactions."""
    query = update.callback_query
    await query.answer()
    user_id = str(query.from_user.id)  # Define once

    load_wallets()

    if query.data == "deposit":
        # ✅ Generate the dynamic MoonPay link
        moonpay_link = generate_moonpay_link(user_id)

        # ✅ Send a message with a direct link
        await query.message.reply_text(
            "💰 **Deposit SOL**\n\n"
            "Click the button below to buy SOL and deposit directly into your wallet.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💳 Buy SOL Now", url=moonpay_link)]
            ])
        )
        return  # ✅ Exit to avoid calling unknown actions

    # ✅ Button actions mapped to functions
    button_actions = {
        "wallet": wallet_info,
        "deposit": deposit_info,
        "set_sell_target": set_sell_target,
        "set_buy_target": set_buy_target,
        "cancel_sell": cancel_sell,
        "transaction_history": transaction_history,
        "withdraw_sol": withdraw_phantom,
        "active_trades": active_trades,
        "help": help_command,
        "reset_wallet": confirm_reset_wallet,
        "cancel_reset": lambda q, c: q.message.reply_text("Wallet reset canceled."),
        "view_solscan": view_solscan,
        "buy_now": buy_now,
        "sell_now": sell_now,
    }

    if query.data in button_actions:
        await button_actions[query.data](update, context)
    else:
        await query.message.reply_text("❌ Unknown action. Please try again.")


def run_flask():  
    """Starts Flask with Gunicorn in production or Waitress in local dev."""
    PORT = int(os.getenv("PORT", 5000))  # ✅ Ensure correct port binding

    if os.getenv("RAILWAY_ENV"):  # ✅ Detect Railway environment correctly
        from gunicorn.app.base import BaseApplication  

        class FlaskApp(BaseApplication):  
            def __init__(self, app, options=None):  
                self.app = app  
                self.options = options or {}  
                super().__init__()  

            def load(self):  
                return self.app  

        logging.info(f"🚀 Running Flask with Gunicorn on port {PORT}")
        FlaskApp(app, {"bind": f"0.0.0.0:{PORT}"}).run()

    else:  
        logging.info(f"🌍 Running Flask Locally on port {PORT}")
        serve(app, host="0.0.0.0", port=PORT)


conv_handler_buy = ConversationHandler(
    entry_points=[CommandHandler("set_buy_target", set_buy_target)],
    states={BUY_TARGET_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_buy_target)]},
    fallbacks=[]
)

conv_handler_sell = ConversationHandler(
    entry_points=[CommandHandler("set_sell_target", set_sell_target)],
    states={SELL_TARGET_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_sell_target)]},
    fallbacks=[]
)


async def run_telegram_bot():
    """Starts the bot using polling"""
    bot = Application.builder().token(TOKEN).build()

    # ✅ Register command handlers
    bot.add_handler(CommandHandler("start", start))
    bot.add_handler(CommandHandler("wallet", wallet_info))
    bot.add_handler(CommandHandler("deposit", deposit_info))
    bot.add_handler(CommandHandler("set_sell_target", set_sell_target))
    bot.add_handler(CommandHandler("set_buy_target", set_buy_target))
    bot.add_handler(CommandHandler("cancel_sell", cancel_sell))
    bot.add_handler(CommandHandler("transaction_history", transaction_history))
    bot.add_handler(CommandHandler("withdraw_sol", withdraw_phantom))
    bot.add_handler(CommandHandler("active_trades", active_trades))
    bot.add_handler(CommandHandler("help", help_command))
    bot.add_handler(CommandHandler("view_solscan", view_solscan))

    # ✅ Register button click handlers
    bot.add_handler(CallbackQueryHandler(handle_button_click))

    logging.info("🤖 Telegram Bot is Running and Polling for Updates...")

    await bot.run_polling()  # ✅ Ensure polling is started


if __name__ == "__main__":
    setup_database()

    loop = asyncio.get_event_loop()

    try:
        loop.run_until_complete(
            asyncio.gather(
                price_monitor(),   # ✅ Background task for price monitoring
                run_telegram_bot()  # ✅ Run Telegram bot using polling
            )
        )
    except KeyboardInterrupt:
        logging.info("🛑 Bot shutting down...")
    except Exception as e:
        logging.error(f"🚨 Critical failure: {e}")
