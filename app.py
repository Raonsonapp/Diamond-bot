"""Hugging Face Space entrypoint for the FF MARGEDAR Telegram bot.

Gradio serves the Space UI in a background thread. The Telegram bot runs
on the main thread because aiogram installs OS signal handlers there.
"""
import asyncio
import logging
import os
import threading

os.environ["PUBLIC_URL"] = ""
os.environ["PORT"] = "7860"

import gradio as gr  # noqa: E402
import spaces  # noqa: E402
import main as bot_main  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ff_margedar.hf")

@spaces.GPU
def zero_gpu_runtime_check():
    """Small ZeroGPU callback required by the selected Space hardware."""
    return "ZeroGPU runtime is available. Telegram bot is running separately."

with gr.Blocks(title="FF MARGEDAR BOT") as demo:
    gr.Markdown("""
    # 💎 FF MARGEDAR BOT

    Telegram bot is started by this Space. Open Telegram and send /start.

    This page is only the Space status page; bot commands are handled in Telegram.
    """)
    status = gr.Textbox(label="Space runtime check", interactive=False)
    check = gr.Button("Check ZeroGPU runtime")
    check.click(fn=zero_gpu_runtime_check, outputs=status)

def _serve_gradio():
    demo.launch(server_name="0.0.0.0", server_port=7860, quiet=True)

if __name__ == "__main__":
    # Gradio runs separately so the main thread remains available to aiogram.
    web_thread = threading.Thread(target=_serve_gradio, name="gradio-web", daemon=True)
    web_thread.start()

    # aiogram needs the main interpreter thread for signal.set_wakeup_fd().
    asyncio.run(bot_main.main())
