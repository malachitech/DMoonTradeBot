import json
import base58

# Load the raw private key
with open("bot-wallet.json", "r") as file:
    raw_key = json.load(file)

# Convert to Base58
base58_key = base58.b58encode(bytes(raw_key)).decode("utf-8")

# print("Base58 Private Key:", base58_key)
