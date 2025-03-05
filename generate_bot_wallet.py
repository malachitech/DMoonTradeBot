# import json
# import base58  # Add this import
# from cryptography.fernet import Fernet
# from solders.keypair import Keypair

# # Generate new wallet
# keypair = Keypair()

# # Get private key as base58 string (CORRECT WAY)
# private_key_bytes = keypair.to_bytes()
# private_key = base58.b58encode(private_key_bytes).decode("utf-8")  # Convert bytes to base58

# public_key = str(keypair.pubkey())

# # Generate encryption key
# encryption_key = Fernet.generate_key()
# cipher = Fernet(encryption_key)
# encrypted_private = cipher.encrypt(private_key.encode()).decode()

# # Create wallet file
# wallet_data = {
#     "bot_wallet": {
#         "public_key": public_key,
#         "encrypted_private_key": encrypted_private,
#         "version": 1
#     }
# }

# with open("bot-wallet.json", "w") as f:
#     json.dump(wallet_data, f, indent=2)

# print(f"🔑 New Encryption Key (Add to .env):")
# print(f"ENCRYPTION_KEY={encryption_key.decode()}")
# print("✅ Generated bot-wallet.json")

import base58
import json

# ✅ Load private key from JSON
with open("bot-wallet.json", "r") as f:
    keypair_data = json.load(f)

# ✅ Convert JSON keypair to Base58
private_key_bytes = bytes(keypair_data)
private_key_base58 = base58.b58encode(private_key_bytes).decode()

print(f"Base58 Private Key: {private_key_base58}")
