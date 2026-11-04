"""Hugging Face ZeroGPU-compatible Gradio entrypoint for FF MARGEDAR.

The Telegram bot runs in polling mode in a background thread. The small
ZeroGPU callback exists to satisfy the ZeroGPU Space runtime; the bot itself
does not need a GPU.
"""
import asyncio
import logging
import os
import threading

# Run the Telegram bot in polling mode; do not configure a webhook URL here.
os.environ["PUBLIC_URL"] = ""
os.environ["PORT"] = "7860"

import gradio as gr  # noqa: E402
import spaces  # noqa: E402
import main as bot_main  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ff_margedar.hf")

_bot_thread = None

@spaces.GPU
def zero_gpu_runtime_check():
    """Minimal decorated callback required by ZeroGPU Spaces."""
    return "ZeroGPU runtime is available. FF MARGEDAR Telegram bot runs on CPU."

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

    Telegram bot service starts in the background. Open Telegram and send
    /start to the bot to check it.

    This Space uses ZeroGPU only to satisfy the Space runtime requirement;
    the Telegram bot itself does not require GPU processing.

    > Free Spaces may pause after inactivity, so uninterrupted 24/7
    > availability is not guaranteed.
    """)
    gpu_status = gr.Textbox(label="Space runtime check", interactive=False)
    check_button = gr.Button("Check ZeroGPU runtime")
    check_button.click(fn=zero_gpu_runtime_check, outputs=gpu_status)

if __name__ == "__main__":
    demo.launch(server_name="0.0.0.0", server_port=7860)
