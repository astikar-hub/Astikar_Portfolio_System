import requests

BOT_TOKEN = "7980904485:AAGJx_cfhsEdwm6rA_utvX--MjusqTnEk4M"
CHAT_ID = 8144938221

message = "✅ Telegram Test Successful!"

url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
payload = {"chat_id": CHAT_ID, "text": message}

r = requests.post(url, data=payload)
print(r.json())
