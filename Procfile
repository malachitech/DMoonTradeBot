web: gunicorn -k uvicorn.workers.UvicornWorker -w 4 -b 0.0.0.0:$PORT bot:bot
web: gunicorn --preload -k uvicorn.workers.UvicornWorker -w 4 -b 0.0.0.0:$PORT bot:bot
web: python bot.py --gunicorn
worker: python -c "from bot import run_bot; run_telegram_bot()"  