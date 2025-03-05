web: gunicorn --preload -k uvicorn.workers.UvicornWorker -w 4 -b 0.0.0.0:$PORT bot:app
worker: python -c "from bot import run_telegram_bot; run_telegram_bot()"