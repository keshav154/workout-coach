from bot import flask_app as app
from threading import Thread
from bot import run_discord

# Start Discord bot in background when gunicorn loads this module
Thread(target=run_discord, daemon=True).start()
