import asyncio
import json
import logging
import os
import sys
import time
from configparser import ConfigParser
from typing import Any, Dict, List, Optional

import websockets

from .geminicli_runner import GeminiCLIRunner


logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(name)s: %(message)s")

logger = logging.getLogger("MenZ-GeminiCLI")


def load_config(path: str) -> ConfigParser:
    config = ConfigParser()
    with open(path, "r", encoding="utf-8") as f:
        config.read_file(f)
    return config


def build_runner(config: ConfigParser) -> GeminiCLIRunner:
    model_name = config.get("gemini", "model_name", fallback="gemini-1.5-flash")
    timeout_seconds = config.getint("gemini", "timeout_seconds", fallback=60)
    max_chars = config.getint("gemini", "max_output_chars", fallback=120)
    prompt_template = config.get("prompt", "template", fallback=None)
    system_prompt = config.get("prompt", "system_prompt", fallback=None)
    return GeminiCLIRunner(
        model_name=model_name,
        timeout_seconds=timeout_seconds,
        max_output_chars=max_chars,
        prompt_template=prompt_template,
        system_prompt=system_prompt,
    )


async def handle_connection(
    uri: str,
    runner: GeminiCLIRunner,
    lines_per_inference: int,
    idle_flush_seconds: int,
    speaker_name: str,
    shutdown_event: Optional[asyncio.Event] = None,
) -> None:
    ws = await websockets.connect(uri)
    try:
        logger.info("connected to %s", uri)
        # 話者ごとのバッファとアイドルタスク
        speaker_buffers: Dict[str, List[str]] = {}
        buffer_lock = asyncio.Lock()
        idle_tasks: Dict[str, asyncio.Task] = {}

        def _speaker_key(s: Optional[str]) -> str:
            return s or ""

        def cancel_idle_task(speaker: Optional[str]) -> None:
            key = _speaker_key(speaker)
            task = idle_tasks.get(key)
            if task and not task.done():
                task.cancel()
            idle_tasks.pop(key, None)

        def cancel_all_idle_tasks() -> None:
            for k, task in list(idle_tasks.items()):
                if task and not task.done():
                    task.cancel()
                idle_tasks.pop(k, None)

        async def flush_buffer(speaker: Optional[str]) -> None:
            key = _speaker_key(speaker)
            async with buffer_lock:
                buf = speaker_buffers.get(key, [])
                if not buf:
                    return
                batched_text = "\n".join(buf)
                speaker_buffers[key] = []
            try:
                comment = await runner.generate_comment_async(subtitle_text=batched_text, speaker=speaker)
                logger.info("comment: %s", comment)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.exception("GeminiCLI error: %s", e)
                comment = "いいね！"

            outgoing = {
                "jsonrpc": "2.0",
                "method": "notifications/subtitle",
                "params": {
                    "text": comment,
                    "speaker": speaker_name,
                    "type": "comment",
                    "language": "ja",
                },
            }
            logger.debug("sending: %s", outgoing)
            await ws.send(json.dumps(outgoing, ensure_ascii=False))

        async def idle_wait_and_flush(speaker: Optional[str]) -> None:
            try:
                await asyncio.sleep(max(0, idle_flush_seconds))
                await flush_buffer(speaker)
            except asyncio.CancelledError:
                return

        def schedule_idle_flush(speaker: Optional[str]) -> None:
            if idle_flush_seconds <= 0:
                return
            cancel_idle_task(speaker)
            key = _speaker_key(speaker)
            idle_tasks[key] = asyncio.create_task(idle_wait_and_flush(speaker))

        async def process_messages() -> None:
            """Process WebSocket messages."""
            nonlocal speaker_buffers
            
            try:
                while True:
                    # Check for shutdown
                    if shutdown_event and shutdown_event.is_set():
                        logger.info("shutdown requested, exiting message loop")
                        break
                    
                    # Receive with timeout to allow cancellation
                    try:
                        message = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    except asyncio.TimeoutError:
                        # No message, loop again (allows cancellation check)
                        continue
                    except websockets.exceptions.ConnectionClosed:
                        logger.info("websocket connection closed")
                        break
                    
                    logger.debug("received raw: %s", message)
                    try:
                        payload: Dict[str, Any] = json.loads(message)
                    except Exception:
                        logger.warning("received non-JSON message: %s", message)
                        continue

                    # MCP フォーマット対応: params から値を取得
                    if payload.get("jsonrpc") == "2.0" and "params" in payload:
                        params = payload.get("params", {})
                        msg_type = params.get("type")
                        text = params.get("text", "")
                        speaker = params.get("speaker")
                    else:
                        # レガシー形式対応
                        msg_type = payload.get("type")
                        text = payload.get("text", "")
                        speaker = payload.get("speaker")

                    if msg_type == "comment":
                        if not text:
                            logger.info("chat: (empty) (speaker=%s)", speaker)
                            continue
                        logger.info("chat: %s (speaker=%s)", text, speaker)
                        # immediate processing for chat comments
                        try:
                            comment = await runner.generate_comment_async(subtitle_text=text, speaker=speaker)
                            logger.info("comment: %s", comment)
                        except asyncio.CancelledError:
                            # Don't catch CancelledError - let it propagate
                            raise
                        except Exception as e:
                            logger.exception("GeminiCLI error: %s", e)
                            comment = "いいね！"

                        outgoing = {
                            "jsonrpc": "2.0",
                            "method": "notifications/subtitle",
                            "params": {
                                "text": comment,
                                "speaker": speaker_name,
                                "type": "comment",
                                "language": "ja",
                            },
                        }
                        logger.debug("sending: %s", outgoing)
                        await ws.send(json.dumps(outgoing, ensure_ascii=False))
                        continue
                    if msg_type != "subtitle":
                        logger.debug("skip message type=%s", msg_type)
                        continue

                    if not text:
                        logger.info("subtitle: (empty) (speaker=%s)", speaker)
                        continue

                    logger.info("subtitle: %s (speaker=%s)", text, speaker)
                    key = _speaker_key(speaker)
                    async with buffer_lock:
                        buf = speaker_buffers.setdefault(key, [])
                        buf.append(text)

                    async with buffer_lock:
                        current_len = len(speaker_buffers.get(key, []))
                    if current_len >= max(1, lines_per_inference):
                        await flush_buffer(speaker)
                        cancel_idle_task(speaker)
                    else:
                        schedule_idle_flush(speaker)
            except asyncio.CancelledError:
                logger.info("message processing cancelled")
                raise
        
        # Create message processing task
        msg_task = asyncio.create_task(process_messages())
        
        try:
            await msg_task
        except asyncio.CancelledError:
            logger.info("connection cancelled, closing websocket")
            await ws.close()
            msg_task.cancel()
            try:
                await msg_task
            except asyncio.CancelledError:
                pass
            raise
        finally:
            # flush remaining buffers for all speakers before closing the websocket
            try:
                keys = list(speaker_buffers.keys())
                for k in keys:
                    try:
                        await flush_buffer(k or None)
                    except Exception:
                        pass
            except Exception:
                pass
            cancel_all_idle_tasks()
    finally:
        if not ws.closed:
            await ws.close()


async def main_with_runner(config: ConfigParser, runner: GeminiCLIRunner) -> None:

    # Set log level from env or config
    env_level = os.getenv("LOG_LEVEL")
    cfg_level = config.get("client", "log_level", fallback=None)
    level_name = (env_level or cfg_level or "INFO").upper()
    try:
        logger.setLevel(getattr(logging, level_name))
    except Exception:
        logger.setLevel(logging.INFO)
    
    # Initialize Gemini session at startup
    logger.info("initializing Gemini session at startup...")
    try:
        await runner._initialize_async()
    except Exception as e:
        logger.error("failed to initialize Gemini session: %s", e)
        return

    host = config.get("client", "host", fallback="localhost")
    port = config.getint("client", "port", fallback=50001)
    uri = f"ws://{host}:{port}/"

    speaker_name = config.get("client", "speaker_name", fallback="wipe")

    reconnect_initial_ms = config.getint("client", "reconnect_initial_ms", fallback=500)
    reconnect_max_ms = config.getint("client", "reconnect_max_ms", fallback=5000)

    lines_per_inference = config.getint("processing", "lines_per_inference", fallback=5)
    idle_flush_seconds = config.getint("processing", "idle_flush_seconds", fallback=0)

    # Setup signal handler in asyncio event loop
    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()
    
    def signal_handler():
        logger.info("received signal, shutting down...")
        if not shutdown_event.is_set():
            shutdown_event.set()
            # Force immediate exit if not shutting down within 2 seconds
            def force_exit():
                logger.debug("force exit timer started (2 seconds)")
                time.sleep(2)
                logger.warning("forced shutdown - killing process")
                if runner:
                    try:
                        runner.close()
                    except Exception as e:
                        logger.error("error during forced close: %s", e)
                os._exit(1)
            import threading
            threading.Thread(target=force_exit, daemon=True).start()
    
    import signal as signal_module
    loop.add_signal_handler(signal_module.SIGINT, signal_handler)
    loop.add_signal_handler(signal_module.SIGTERM, signal_handler)
    
    try:
        backoff_ms = max(0, reconnect_initial_ms)
        while not shutdown_event.is_set():
            try:
                # Create connection task
                connection_task = asyncio.create_task(handle_connection(
                    uri=uri,
                    runner=runner,
                    lines_per_inference=lines_per_inference,
                    idle_flush_seconds=idle_flush_seconds,
                    speaker_name=speaker_name,
                    shutdown_event=shutdown_event,
                ))
                
                # Create shutdown wait task
                shutdown_task = asyncio.create_task(shutdown_event.wait())
                
                # Wait for either to complete
                done, pending = await asyncio.wait(
                    {connection_task, shutdown_task},
                    return_when=asyncio.FIRST_COMPLETED
                )
                
                # If shutdown was requested, cancel connection
                if shutdown_event.is_set():
                    connection_task.cancel()
                    try:
                        await connection_task
                    except asyncio.CancelledError:
                        pass
                    break
                
                # Otherwise, connection finished
                await connection_task
                backoff_ms = reconnect_initial_ms  # reset after a clean session
                
            except (asyncio.CancelledError, KeyboardInterrupt):
                raise
            except Exception as e:
                if shutdown_event.is_set():
                    break
                logger.warning("connection error: %s", e)
                sleep_ms = min(backoff_ms, reconnect_max_ms)
                logger.info("reconnecting in %d ms...", sleep_ms)
                await asyncio.sleep(sleep_ms / 1000.0)
                backoff_ms = min(backoff_ms * 2, reconnect_max_ms)
    finally:
        # Remove signal handlers
        loop.remove_signal_handler(signal_module.SIGINT)
        loop.remove_signal_handler(signal_module.SIGTERM)


if __name__ == "__main__":
    runner = None
    
    try:
        config_path = "config.ini"
        try:
            config = load_config(config_path)
        except FileNotFoundError:
            logger.error("config.ini が見つかりません。プロジェクトルートに配置してください。")
            sys.exit(1)

        runner = build_runner(config)
        
        # Run main without creating runner inside
        asyncio.run(main_with_runner(config, runner))
    except KeyboardInterrupt:
        logger.info("shutting down...")
    finally:
        if runner:
            logger.debug("closing gemini session...")
            runner.close()
            logger.debug("gemini session closed")

