from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import asyncio
import threading
import queue as tqueue
import json

app = FastAPI(title="Riva Agentic AI API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    return {"status": "online", "message": "Riva Backend API is running"}


@app.get("/api/health")
async def health_check():
    return {"status": "healthy", "version": "1.0.0"}


@app.websocket("/ws/browse")
async def browse_websocket(websocket: WebSocket):
    await websocket.accept()

    # Thread-safe queues bridging the sync Playwright thread ↔ async WebSocket
    frame_q:   tqueue.Queue[bytes] = tqueue.Queue(maxsize=8)
    thought_q: tqueue.Queue[dict]  = tqueue.Queue()
    click_q:   tqueue.Queue[dict]  = tqueue.Queue()
    stop_event = threading.Event()

    try:
        init_data = await websocket.receive_json()
        url: str = init_data.get("url", "")
        if not url.startswith("http"):
            url = "https://" + url

        # ── Playwright runs synchronously in a background thread ──────────
        def run_browser():
            from playwright.sync_api import sync_playwright

            def thought(text: str, state: str = "info"):
                thought_q.put({"text": text, "state": state})

            try:
                with sync_playwright() as p:
                    browser = p.chromium.launch(headless=True)
                    context = browser.new_context(viewport={"width": 1280, "height": 720})
                    page    = context.new_page()
                    client  = page.context.new_cdp_session(page)

                    # CDP Screencast → frame_q
                    # params["data"] is already base64-encoded JPEG — pass it straight through
                    def on_frame(params):
                        if stop_event.is_set():
                            return
                        try:
                            client.send("Page.screencastFrameAck", {"sessionId": params["sessionId"]})
                        except Exception:
                            pass
                        if not frame_q.full():
                            frame_q.put_nowait(params["data"])   # raw base64 string

                    client.on("Page.screencastFrame", on_frame)
                    client.send("Page.startScreencast", {
                        "format": "jpeg",
                        "quality": 70,
                        "maxWidth": 1280,
                        "maxHeight": 720,
                        "everyNthFrame": 2,
                    })

                    # ── Agent navigation test ─────────────────────────────
                    thought(f"Navigating to {url} ...", "navigating")
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    thought("Page loaded. Scanning for nav targets...", "scanning")
                    page.wait_for_timeout(2000)

                    candidates = [
                        ("Pricing", lambda: page.get_by_role("link",   name="Pricing", exact=False)),
                        ("Plans",   lambda: page.get_by_role("link",   name="Plans",   exact=False)),
                        ("Pricing", lambda: page.get_by_role("button", name="Pricing", exact=False)),
                    ]

                    clicked = False
                    for label, get_loc in candidates:
                        if stop_event.is_set():
                            break
                        try:
                            loc = get_loc()
                            if loc.first.is_visible(timeout=2000):
                                thought(f"Found '{label}' — highlighting", "found")
                                loc.first.evaluate(
                                    "(el) => { el.style.outline = '3px solid #00ffff';"
                                    " el.style.backgroundColor = 'rgba(0,255,255,0.15)'; }"
                                )
                                page.wait_for_timeout(1500)
                                thought(f"Hovering '{label}'...", "hovering")
                                loc.first.hover()
                                page.wait_for_timeout(800)
                                thought(f"Clicking '{label}'...", "clicking")
                                loc.first.click()
                                page.wait_for_timeout(3000)
                                thought(f"Landed on: {page.url}", "navigating")
                                clicked = True
                                break
                        except Exception:
                            continue

                    if not clicked:
                        thought("No pricing link found — scrolling to scan.", "scanning")

                    thought("Scrolling to survey content...", "scanning")
                    page.evaluate("window.scrollTo({ top: 600, behavior: 'smooth' })")
                    page.wait_for_timeout(2000)
                    page.evaluate("window.scrollTo({ top: 1200, behavior: 'smooth' })")
                    page.wait_for_timeout(2000)

                    thought("Navigation test complete. HITL mode active — click the preview to interact.", "complete")

                    # ── HITL hold loop ────────────────────────────────────
                    while not stop_event.is_set():
                        try:
                            click = click_q.get(timeout=0.1)
                            x = float(click["x"]) * 1280
                            y = float(click["y"]) * 720
                            thought(f"HITL click → ({int(x)}, {int(y)})", "hitl")
                            page.mouse.click(x, y)
                            page.wait_for_timeout(500)
                        except tqueue.Empty:
                            pass

                    try:
                        client.send("Page.stopScreencast")
                    except Exception:
                        pass
                    browser.close()

            except Exception as e:
                thought_q.put({"text": f"Error: {e}", "state": "error"})
            finally:
                thought_q.put({"__sentinel__": True})

        thread = threading.Thread(target=run_browser, daemon=True)
        thread.start()

        # ── Async side: drain queues → WebSocket, receive clicks ──────────
        done = False

        async def drain():
            nonlocal done
            while not done or not thought_q.empty() or not frame_q.empty():
                # Thoughts
                while not thought_q.empty():
                    msg = thought_q.get_nowait()
                    if msg.get("__sentinel__"):
                        done = True
                        return
                    try:
                        await websocket.send_text(json.dumps({"type": "thought", **msg}))
                    except Exception:
                        return
                # Frames — send as base64 JSON, no binary blobs needed
                while not frame_q.empty():
                    try:
                        b64 = frame_q.get_nowait()
                        await websocket.send_text(json.dumps({"type": "frame", "data": b64}))
                    except Exception:
                        return
                await asyncio.sleep(0.016)   # ~60 fps polling

        async def receive():
            while True:
                try:
                    data = await websocket.receive_json()
                    if data.get("type") == "click":
                        click_q.put(data)
                except WebSocketDisconnect:
                    break
                except Exception:
                    break

        await asyncio.gather(
            asyncio.create_task(drain()),
            asyncio.create_task(receive()),
            return_exceptions=True,
        )

    except WebSocketDisconnect:
        pass
    finally:
        stop_event.set()
