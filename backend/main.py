from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import asyncio
import threading
import queue as tqueue
import json
import os
from dotenv import load_dotenv
from google import genai

load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Global vault to prevent amnesia
mission_vault = {}

app = FastAPI(title="Riva Agentic AI API (Iron Pathfinder Fixed)")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

@app.websocket("/ws/browse")
async def browse_websocket(websocket: WebSocket):
    await websocket.accept()
    frame_q:   tqueue.Queue[str] = tqueue.Queue(maxsize=8)
    thought_q: tqueue.Queue[dict]  = tqueue.Queue()
    stop_event = threading.Event()

    try:
        init_data = await websocket.receive_json()
        url: str = init_data.get("url", "")
        if not url.startswith("http"): url = "https://" + url
        
        session_id = url
        if session_id not in mission_vault:
            mission_vault[session_id] = {"pricing": False, "docs": False}
        objectives = mission_vault[session_id]

        def run_browser():
            from playwright.sync_api import sync_playwright

            def thought(text: str, state: str = "info"):
                print(f"[{state.upper()}] {text}")
                thought_q.put({"text": text, "state": state})

            def find_element(page, keywords):
                for k in keywords:
                    try:
                        for role in ["link", "button"]:
                            loc = page.get_by_role(role, name=k, exact=False)
                            if loc.first.is_visible(timeout=300): return loc.first, k
                    except: continue
                return None, None

            def ask_brain(page_content: str, current_url: str):
                if not GEMINI_API_KEY: return {"goal": "ERROR", "thought": "Missing API Key"}
                p_label = "DONE" if objectives['pricing'] else "NOT STARTED"
                d_label = "DONE" if objectives['docs'] else "NOT STARTED"
                try:
                    client = genai.Client(api_key=GEMINI_API_KEY)
                    prompt = f"Riva Pathfinder. Mission: {current_url}\nMilestones: Pricing:{p_label}, Docs:{d_label}\nGoal: Find both. If one is DONE, ignore links to it.\nRespond JSON: {{\"thought\": \"..\", \"goal\": \"CLICK\"|\"HOVER\"|\"SURVEY\"|\"FINISH\", \"target\": \"..\"}}\nContent: {page_content[:6000]}"
                    res = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
                    txt = res.text.strip()
                    if "```json" in txt: txt = txt.split("```json")[1].split("```")[0].strip()
                    return json.loads(txt)
                except: return {"goal": "SURVEY", "thought": "Analyzing..."}

            try:
                with sync_playwright() as p:
                    browser = p.chromium.launch(headless=False)
                    page = browser.new_page(viewport={"width": 1280, "height": 720})
                    client = page.context.new_cdp_session(page)
                    
                    # FIXED: Added Ack to prevent freezing
                    def on_frame(params):
                        if stop_event.is_set(): return
                        try: client.send("Page.screencastFrameAck", {"sessionId": params["sessionId"]})
                        except: pass
                        if not frame_q.full(): frame_q.put_nowait(params["data"])

                    client.on("Page.screencastFrame", on_frame)
                    client.send("Page.startScreencast", {"format": "jpeg", "quality": 60, "everyNthFrame": 2})

                    thought(f"Starting Mission: {url}", "navigating")
                    page.goto(url, wait_until="domcontentloaded")
                    
                    for step in range(30):
                        if stop_event.is_set(): break
                        
                        curr_url = page.url.lower()
                        if not objectives["pricing"] and any(k in curr_url for k in ["pricing", "plans", "tier"]):
                            thought("✓ Milestone 1: Pricing Captured.", "found")
                            for _ in range(2): page.evaluate("window.scrollBy(0, 400)"); page.wait_for_timeout(800)
                            objectives["pricing"] = True; page.goto(url); continue

                        if not objectives["docs"] and any(k in curr_url for k in ["docs", "documentation", "api", "guide"]):
                            if page.url != url:
                                thought("✓ Milestone 2: Documentation Located.", "found")
                                objectives["docs"] = True

                        content = page.evaluate("() => document.body.innerText")
                        decision = ask_brain(content, page.url)
                        goal, label = decision.get("goal"), decision.get("target")
                        thought(decision.get("thought", "Moving..."), "scanning")

                        if goal in ["CLICK", "HOVER"]:
                            el, matched = find_element(page, [label]) if label else (None, None)
                            if not el:
                                keys = ["Pricing", "Plans"] if not objectives["pricing"] else ["Developers", "Docs", "Library"]
                                el, matched = find_element(page, keys)

                            if el:
                                thought(f"Locking on: {matched}", "clicking")
                                el.evaluate("el => { el.scrollIntoView({behavior:'smooth', block:'center'}); el.style.outline='8px solid cyan'; }")
                                page.wait_for_timeout(1000)
                                el.hover(force=True)
                                if goal == "CLICK":
                                    el.evaluate("el => el.setAttribute('target', '_self')")
                                    try: el.click(force=True, timeout=3000)
                                    except: el.evaluate("el => el.click()")
                                page.wait_for_timeout(2000)
                            else:
                                page.evaluate("window.scrollBy(0, 500)")

                        elif goal == "SURVEY":
                            page.evaluate("window.scrollBy({top: 500, behavior: 'smooth'})")
                            page.wait_for_timeout(1000)

                        elif goal == "FINISH" and objectives["pricing"] and objectives["docs"]:
                            thought("mission objective complete.", "complete"); break

                    thought("Standing by.", "complete")
                    while not stop_event.is_set(): page.wait_for_timeout(500)
                    browser.close()
            except Exception as e: thought(f"System Error: {e}", "error")
            finally: thought_q.put({"__sentinel__": True})

        thread = threading.Thread(target=run_browser, daemon=True); thread.start()

        async def drain():
            while not stop_event.is_set():
                while not thought_q.empty():
                    msg = thought_q.get_nowait()
                    if msg.get("__sentinel__"): return
                    await websocket.send_text(json.dumps({"type": "thought", **msg}))
                while not frame_q.empty():
                    await websocket.send_text(json.dumps({"type": "frame", "data": frame_q.get_nowait()}))
                await asyncio.sleep(0.02)

        await asyncio.gather(drain())
    except Exception: pass
    finally: stop_event.set()
