# WebSocket handler for the chat interface. Delegates query answering to the
# Cloudflare Worker (RIVA_WORKER_URL/chat), which handles embedding, Vectorize
# search, and Gemini generation at the edge.

import asyncio
import json
import threading
import queue as tqueue

import requests
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .config import RIVA_WORKER_URL

router = APIRouter()


def _worker_chat(query: str, domains: list[str]) -> dict:
    """POST to the CF Worker /chat endpoint and return {answer, sources}."""
    url = f"{RIVA_WORKER_URL}/chat"
    payload: dict = {"query": query}
    if domains:
        payload["domains"] = domains
    resp = requests.post(url, json=payload, timeout=60)
    resp.raise_for_status()
    return resp.json()


def _handle_message(text: str, domains: list[str], out_q: tqueue.Queue):
    def log(msg: str, state: str = "info"):
        out_q.put({"type": "thought", "text": msg, "state": state})

    try:
        log("Querying knowledge base...", "navigating")
        result = _worker_chat(text, domains)

        if "error" in result:
            log(f"Worker error: {result['error']}", "error")
            out_q.put({"type": "answer", "text": result["error"], "sources": []})
            return

        answer = result.get("answer", "No answer returned.")
        sources = result.get("sources", [])

        if sources:
            log(f"Found {len(sources)} source(s).", "success")
        else:
            log("No relevant sources found in knowledge base.", "error")

        out_q.put({"type": "answer", "text": answer, "sources": sources})

    except requests.HTTPError as e:
        log(f"Worker request failed: {e}", "error")
        out_q.put({"type": "answer", "text": f"Worker error: {e}", "sources": []})
    except Exception as e:
        log(f"Error: {e}", "error")
        out_q.put({"type": "answer", "text": f"Something went wrong: {e}", "sources": []})


@router.websocket("/ws/chat")
async def chat_websocket(websocket: WebSocket, session: str = ""):
    await websocket.accept()

    if not RIVA_WORKER_URL:
        await websocket.send_text(json.dumps({
            "type": "answer",
            "text": "RIVA_WORKER_URL is not configured. Set it in your .env file.",
            "sources": [],
        }))
        await websocket.close()
        return

    out_q: tqueue.Queue = tqueue.Queue()
    closed = threading.Event()

    async def drain():
        while not closed.is_set():
            while not out_q.empty():
                msg = out_q.get_nowait()
                try:
                    await websocket.send_text(json.dumps(msg))
                except Exception:
                    return
            await asyncio.sleep(0.05)

    drain_task = asyncio.ensure_future(drain())
    try:
        while True:
            data = await websocket.receive_json()
            if data.get("type") == "message":
                msg_text = (data.get("text") or "").strip()
                domains = data.get("domains") or []
                if msg_text:
                    threading.Thread(
                        target=_handle_message,
                        args=(msg_text, domains, out_q),
                        daemon=True,
                    ).start()
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        closed.set()
        try:
            await drain_task
        except Exception:
            pass
