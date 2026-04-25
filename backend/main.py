from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import asyncio
import threading
import queue as tqueue
import json
import os
import uuid
import sqlite3
import concurrent.futures
from datetime import datetime
from urllib.parse import urlparse
import ipaddress
from dotenv import load_dotenv
from google import genai

load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
DB_PATH = os.path.join(os.path.dirname(__file__), "riva_intel.db")
_db_lock = threading.Lock()

def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with _db_lock, _db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id           TEXT PRIMARY KEY,
                target_url   TEXT NOT NULL,
                started_at   TEXT NOT NULL,
                completed_at TEXT,
                status       TEXT DEFAULT 'running',
                pricing_found INTEGER DEFAULT 0,
                docs_found    INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS intel (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id   TEXT NOT NULL,
                type         TEXT NOT NULL,
                url          TEXT NOT NULL,
                content      TEXT,
                captured_at  TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            );
        """)

init_db()

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(title="Riva Strategic Pathfinder")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"]
)

# ---------------------------------------------------------------------------
# REST — query stored intel
# ---------------------------------------------------------------------------
@app.get("/sessions")
def list_sessions():
    with _db_lock, _db() as conn:
        rows = conn.execute("SELECT * FROM sessions ORDER BY started_at DESC").fetchall()
        return [dict(r) for r in rows]

@app.get("/sessions/{session_id}")
def get_session(session_id: str):
    with _db_lock, _db() as conn:
        session = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        intel = conn.execute(
            "SELECT * FROM intel WHERE session_id = ? ORDER BY captured_at", (session_id,)
        ).fetchall()
        return {"session": dict(session), "intel": [dict(i) for i in intel]}

# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------
@app.websocket("/ws/browse")
async def browse_websocket(websocket: WebSocket):
    await websocket.accept()

    # All state scoped to this connection — no shared globals
    session_id  = str(uuid.uuid4())
    frame_q:   tqueue.Queue[str]  = tqueue.Queue(maxsize=8)
    thought_q: tqueue.Queue[dict] = tqueue.Queue()
    action_q:  tqueue.Queue[dict] = tqueue.Queue()
    stop_event  = threading.Event()
    pause_event = threading.Event()
    pause_event.set()

    try:
        init_data = await websocket.receive_json()
        target_url: str = init_data.get("url", "").strip()
        if not target_url:
            await websocket.close(code=1008, reason="No URL provided")
            return
        if not target_url.startswith("http"):
            target_url = "https://" + target_url

        with _db_lock, _db() as conn:
            conn.execute(
                "INSERT INTO sessions (id, target_url, started_at) VALUES (?, ?, ?)",
                (session_id, target_url, datetime.utcnow().isoformat())
            )

        # -----------------------------------------------------------------------
        # Browser thread
        # -----------------------------------------------------------------------
        def run_browser():
            from playwright.sync_api import sync_playwright

            objectives    = {"pricing": False, "docs": False}
            visited_urls: list[str] = []
            gemini_client = genai.Client(api_key=GEMINI_API_KEY)

            # --- helpers -------------------------------------------------------

            def thought(text: str, state: str = "info"):
                print(f"[{state.upper()}] {text}")
                thought_q.put({"text": text, "state": state})

            def save_intel(intel_type: str, url: str, content: str):
                with _db_lock, _db() as conn:
                    conn.execute(
                        "INSERT INTO intel (session_id, type, url, content, captured_at) VALUES (?, ?, ?, ?, ?)",
                        (session_id, intel_type, url, content, datetime.utcnow().isoformat())
                    )

            def finish_session(status: str):
                with _db_lock, _db() as conn:
                    conn.execute(
                        "UPDATE sessions SET status=?, completed_at=?, pricing_found=?, docs_found=? WHERE id=?",
                        (status, datetime.utcnow().isoformat(),
                         int(objectives["pricing"]), int(objectives["docs"]), session_id)
                    )

            def validate_url(url: str) -> bool:
                try:
                    parsed = urlparse(url)
                    if parsed.scheme not in ("http", "https"):
                        return False
                    host = parsed.hostname or ""
                    try:
                        ip = ipaddress.ip_address(host)
                        if ip.is_private or ip.is_loopback or ip.is_reserved:
                            return False
                    except ValueError:
                        pass  # it's a hostname, not a raw IP — fine
                    return bool(host)
                except Exception:
                    return False

            def find_element(page, keywords):
                for k in keywords:
                    # Strategy 1: accessibility role
                    for role in ["link", "button", "menuitem"]:
                        try:
                            loc = page.get_by_role(role, name=k, exact=False)
                            if loc.first.is_visible(timeout=300):
                                return loc.first, k
                        except Exception:
                            pass
                    # Strategy 2: visible text
                    try:
                        loc = page.get_by_text(k, exact=False)
                        if loc.first.is_visible(timeout=300):
                            return loc.first, k
                    except Exception:
                        pass
                    # Strategy 3: CSS selector
                    try:
                        loc = page.locator(f'a:has-text("{k}"), button:has-text("{k}")')
                        if loc.first.is_visible(timeout=300):
                            return loc.first, k
                    except Exception:
                        pass
                return None, None

            def ask_brain(page_content: str, current_url: str, headings: str) -> dict:
                if not GEMINI_API_KEY:
                    return {"goal": "ERROR", "thought": "Missing GEMINI_API_KEY"}

                p_text = "PRICING: COMPLETED" if objectives["pricing"] else "PRICING: NOT_STARTED"
                d_text = "DOCS: COMPLETED"    if objectives["docs"]    else "DOCS: NOT_STARTED"
                recent = visited_urls[-5:]

                prompt = f"""Riva Pathfinder Mission — current page: {current_url}
MILESTONES : {p_text} | {d_text}
RECENT URLS: {recent}
PAGE HEADINGS & NAV: {headings[:600]}

RULES:
- Only pursue NOT_STARTED milestones.
- If Pricing is COMPLETED focus entirely on Documentation.
- HOVER nav items like "Developers" or "Products" to reveal sub-menus.
- If a URL appears twice in RECENT URLS, try a different element.

Reply ONLY with JSON (no markdown): {{"thought": "..", "goal": "CLICK"|"HOVER"|"SURVEY"|"FINISH", "target": ".."}}

Page text:
{page_content[:5000]}"""

                def _call():
                    res = gemini_client.models.generate_content(
                        model="gemini-2.5-flash", contents=prompt
                    )
                    txt = res.text.strip()
                    # strip optional code fences
                    if "```" in txt:
                        txt = txt.split("```")[1].lstrip("json").strip()
                        txt = txt.split("```")[0].strip()
                    return json.loads(txt)

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                    future = ex.submit(_call)
                    try:
                        return future.result(timeout=12)
                    except concurrent.futures.TimeoutError:
                        thought("Gemini timed out — surveying.", "info")
                        return {"goal": "SURVEY", "thought": "API timeout"}
                    except Exception as e:
                        thought(f"Brain error: {e}", "info")
                        return {"goal": "SURVEY", "thought": "Brain error"}

            def handle_gestures(page):
                while not action_q.empty():
                    act = action_q.get_nowait()
                    try:
                        atype = act.get("type")
                        if atype == "click":
                            x = float(act.get("x", 0)) * 1280
                            y = float(act.get("y", 0)) * 720
                            page.mouse.click(x, y)
                            thought("Manual click", "user")
                        elif atype == "goto":
                            t_url = act.get("url", "").strip()
                            if not t_url.startswith("http"):
                                t_url = "https://" + t_url
                            if not validate_url(t_url):
                                thought("Blocked: invalid or private URL.", "error")
                                continue
                            thought(f"Navigating to: {t_url}", "navigating")
                            page.goto(t_url, wait_until="domcontentloaded", timeout=10000)
                    except Exception as e:
                        print(f"Gesture error: {e}")

            # --- main browser logic -------------------------------------------
            try:
                with sync_playwright() as p:
                    browser = p.chromium.launch(headless=False)
                    page    = browser.new_page(viewport={"width": 1280, "height": 720})
                    cdp     = page.context.new_cdp_session(page)

                    def on_frame(params):
                        if stop_event.is_set():
                            return
                        try:
                            cdp.send("Page.screencastFrameAck", {"sessionId": int(params["sessionId"])})
                        except Exception:
                            pass
                        if not frame_q.full():
                            frame_q.put_nowait(params["data"])

                    cdp.on("Page.screencastFrame", on_frame)
                    cdp.send("Page.startScreencast", {
                        "format": "jpeg", "quality": 60, "everyNthFrame": 2
                    })

                    thought(f"Mission started: {target_url}", "navigating")
                    page.goto(target_url, wait_until="domcontentloaded")
                    visited_urls.append(target_url)

                    frustration = 0

                    for step in range(80):
                        if stop_event.is_set():
                            break

                        # Collaborative pause — hand control to user
                        while not pause_event.is_set():
                            handle_gestures(page)
                            if stop_event.is_set():
                                break
                            page.wait_for_timeout(100)
                        if stop_event.is_set():
                            break

                        handle_gestures(page)

                        # Track URL changes
                        curr_url = page.url
                        if not visited_urls or visited_urls[-1] != curr_url:
                            visited_urls.append(curr_url)

                        # --- Milestone detection --------------------------------
                        curr_lower = curr_url.lower()
                        progress   = False

                        if not objectives["pricing"] and any(
                            k in curr_lower for k in ["pricing", "plans", "tier"]
                        ):
                            thought("Milestone: Pricing page captured.", "found")
                            page.wait_for_timeout(1200)
                            for _ in range(3):
                                page.evaluate("window.scrollBy(0, 400)")
                                page.wait_for_timeout(600)
                            content = page.evaluate("() => document.body.innerText")
                            save_intel("pricing", curr_url, content)
                            objectives["pricing"] = True
                            progress = True
                            page.goto(target_url, wait_until="domcontentloaded")

                        if not objectives["docs"] and any(
                            k in curr_lower for k in ["docs", "documentation", "api", "guide", "developer"]
                        ):
                            if curr_url != target_url:
                                thought("Milestone: Documentation located.", "found")
                                page.wait_for_timeout(1200)
                                for _ in range(3):
                                    page.evaluate("window.scrollBy(0, 400)")
                                    page.wait_for_timeout(600)
                                content = page.evaluate("() => document.body.innerText")
                                save_intel("docs", curr_url, content)
                                objectives["docs"] = True
                                progress = True

                        if progress:
                            frustration = 0
                            if objectives["pricing"] and objectives["docs"]:
                                thought("All milestones complete.", "complete")
                                break
                            continue

                        # --- Agent decision loop --------------------------------
                        try:
                            content  = page.evaluate("() => document.body.innerText")
                            headings = page.evaluate(
                                "() => Array.from(document.querySelectorAll('h1,h2,h3,nav a,header a'))"
                                ".map(e => e.innerText.trim()).filter(Boolean).join(' | ')"
                            )
                            decision = ask_brain(content, page.url, headings)
                            goal     = decision.get("goal")
                            label    = decision.get("target", "")
                            thought(decision.get("thought", "Analyzing..."), "scanning")

                            if goal in ["CLICK", "HOVER"]:
                                el, matched = find_element(page, [label]) if label else (None, None)
                                if not el:
                                    fallback = (
                                        ["Pricing", "Plans", "Price"]
                                        if not objectives["pricing"]
                                        else ["Docs", "Documentation", "Developers", "API", "Guide", "Reference"]
                                    )
                                    el, matched = find_element(page, fallback)

                                if el:
                                    frustration = 0
                                    thought(f"Targeting: {matched}", "clicking")
                                    el.evaluate(
                                        "el => { el.scrollIntoView({behavior:'smooth', block:'center'});"
                                        " el.style.outline='4px solid cyan'; }"
                                    )
                                    page.wait_for_timeout(800)
                                    el.hover(force=True)
                                    if goal == "CLICK":
                                        url_before = page.url
                                        el.evaluate("el => el.setAttribute('target', '_self')")
                                        try:
                                            el.click(force=True, timeout=3000)
                                        except Exception:
                                            el.evaluate("el => el.click()")
                                        page.wait_for_timeout(2000)
                                        # If click didn't navigate, extract href and go directly
                                        if page.url == url_before:
                                            try:
                                                href = el.evaluate("el => el.href || el.getAttribute('href') || ''")
                                                if href and href.startswith("http") and href != url_before:
                                                    thought(f"Click didn't navigate — going to href directly", "navigating")
                                                    page.goto(href, wait_until="domcontentloaded", timeout=10000)
                                                else:
                                                    thought("Click had no effect — scrolling to try another element", "info")
                                                    page.evaluate("window.scrollBy(0, 400)")
                                            except Exception:
                                                pass
                                    page.wait_for_timeout(1000)
                                else:
                                    frustration += 1
                                    thought(f"Element not found (frustration {frustration}/3)", "info")
                                    if frustration >= 3:
                                        thought("Agent stuck — pausing for manual guidance.", "error")
                                        thought_q.put({"type": "auto_pause"})
                                        pause_event.clear()
                                        frustration = 0
                                    else:
                                        page.evaluate("window.scrollBy(0, 600)")
                                        page.wait_for_timeout(500)

                            elif goal == "FINISH":
                                if objectives["pricing"] and objectives["docs"]:
                                    thought("Mission complete.", "complete")
                                    break
                                else:
                                    thought("FINISH requested but milestones incomplete — continuing.", "info")

                        except Exception as e:
                            thought(f"Step error: {e}", "info")

                        page.wait_for_timeout(500)

                    finish_session("complete")
                    thought("Session complete. Browser staying open for manual use.", "complete")

                    while not stop_event.is_set():
                        handle_gestures(page)
                        page.wait_for_timeout(200)

                    browser.close()

            except Exception as e:
                thought(f"Fatal error: {e}", "error")
                finish_session("error")
            finally:
                thought_q.put({"__sentinel__": True})

        thread = threading.Thread(target=run_browser, daemon=True)
        thread.start()

        # -----------------------------------------------------------------------
        # Async WebSocket handlers
        # -----------------------------------------------------------------------
        async def drain():
            while not stop_event.is_set():
                while not thought_q.empty():
                    msg = thought_q.get_nowait()
                    if msg.get("__sentinel__"):
                        return
                    await websocket.send_text(
                        json.dumps(msg if "type" in msg else {"type": "thought", **msg})
                    )
                while not frame_q.empty():
                    await websocket.send_text(
                        json.dumps({"type": "frame", "data": frame_q.get_nowait()})
                    )
                await asyncio.sleep(0.02)

        async def receive():
            while True:
                try:
                    data  = await websocket.receive_json()
                    dtype = data.get("type")
                    if dtype == "pause":
                        pause_event.clear()
                        thought_q.put({"text": "Agent paused.", "state": "info"})
                    elif dtype == "resume":
                        pause_event.set()
                        thought_q.put({"text": "Agent resuming...", "state": "info"})
                    else:
                        action_q.put(data)
                except Exception:
                    break

        await asyncio.gather(drain(), receive())

    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"WebSocket error: {e}")
    finally:
        stop_event.set()
