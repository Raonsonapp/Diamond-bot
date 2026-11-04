"""Hugging Face Gradio entrypoint for FF MARGEDAR.

The Telegram bot runs in polling mode in a background thread. Gradio provides
the HTTP application required by Hugging Face Spaces on port 7860.
"""
import asyncio
import logging
import os
import threading

# A Space URL is not a stable always-on webhook target. Polling lets the bot
# receive Telegram updates whenever this Space process is running.
os.environ["PUBLIC_URL"] = ""
os.environ["PORT"] = "7860"

import gradio as gr  # noqa: E402
import main as bot_main  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ff_margedar.hf")

_bot_thread = None

def _run_bot() -> None:
    try:
        asyncio.run(bot_main.main())
    except Exception:
        logger.exception("Telegram bot stopped with an error")

def start_bot() -> None:
    global _bot_thread
    if _bot_thread is not None and _bot_thread.is_alive():
        return
    _bot_thread = threading.Thread(target=_run_bot, name="telegram-bot", daemon=True)
    _bot_thread.start()

start_bot()

with gr.Blocks(title="FF MARGEDAR BOT") as demo:
    gr.Markdown("""
    # 💎 FF MARGEDAR BOT
    **Telegram bot service is running in the background.**

    This page is the Hugging Face Space health interface. Open Telegram
    and send the /start command to your bot to check its availability.

    > Free Spaces may pause after inactivity. This does not guarantee
    > uninterrupted 24/7 bot availability.
    """)

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)
