from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import asyncio
import threading
import queue as tqueue
import json
import os
import re
from dotenv import load_dotenv
from google import genai

load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

mission_vault = {}

app = FastAPI(title="Riva Strategic Pathfinder (Resilient Iron Mission)")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

@app.websocket("/ws/browse")
async def browse_websocket(websocket: WebSocket):
    await websocket.accept()

    frame_q:   tqueue.Queue[str] = tqueue.Queue(maxsize=8)
    thought_q: tqueue.Queue[dict]  = tqueue.Queue()
    action_q:  tqueue.Queue[dict]  = tqueue.Queue()
    
    stop_event = threading.Event()
    pause_event = threading.Event()
    pause_event.set()

    try:
        init_data = await websocket.receive_json()
        url: str = init_data.get("url", "")
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
                        for role in ["link", "button", "menuitem"]:
                            loc = page.get_by_role(role, name=k, exact=False)
                            if loc.first.is_visible(timeout=300): return loc.first, k
                    except: continue
                return None, None

            def ask_brain(page_content: str, current_url: str):
                if not GEMINI_API_KEY: return {"goal": "ERROR", "thought": "Missing API Key"}
                p_text = "PRICING: COMPLETED (DO NOT VISIT)" if objectives['pricing'] else "PRICING: NOT_STARTED (NEED TO FIND)"
                d_text = "DOCS: COMPLETED (DO NOT VISIT)" if objectives['docs'] else "DOCS: NOT_STARTED (NEED TO FIND)"
                try:
                    client = genai.Client(api_key=GEMINI_API_KEY)
                    prompt = f"""
                    Riva Pathfinder Mission: {current_url}
                    MILESTONES: {p_label if (p_label:=p_text) else ''} | {d_label if (d_label:=d_text) else ''}
                    
                    STRATEGY:
                    - Only find NOT_STARTED milestones.
                    - If Pricing is COMPLETED, focus 100% on Documentation.
                    - Use HOVER on 'Developers' to find sub-links.
                    
                    Respond ONLY JSON: {{"thought": "..", "goal": "CLICK"|"HOVER"|"SURVEY"|"FINISH", "target": ".."}}
                    Content: {page_content[:6000]}
                    """
                    res = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
                    txt = res.text.strip()
                    if "```json" in txt: txt = txt.split("```json")[1].split("```")[0].strip()
                    return json.loads(txt)
                except: return {"goal": "SURVEY", "thought": "Thinking..."}

            def handle_gestures(page):
                while not action_q.empty():
                    act = action_q.get_nowait()
                    try:
                        x, y = float(act.get("x", 0)) * 1280, float(act.get("y", 0)) * 720
                        atype = act["type"]
                        if atype == "click": page.mouse.click(x, y); thought("Manual Interaction", "user")
                        elif atype == "mousemove": page.mouse.move(x, y)
                        elif atype == "scroll": page.mouse.wheel(0, act.get("deltaY", 0))
                        elif atype == "goto": 
                            t_url = act["url"].strip()
                            if len(t_url) < 5 or "." not in t_url: 
                                thought("Invalid URL entered.", "error")
                                continue
                            if not t_url.startswith("http"): t_url = "https://" + t_url
                            thought(f"Teleporting to: {t_url}", "navigating")
                            page.goto(t_url, wait_until="domcontentloaded", timeout=10000)
                    except Exception as e: print(f"Manual Action Fail: {e}")

            try:
                with sync_playwright() as p:
                    browser = p.chromium.launch(headless=False)
                    page = browser.new_page(viewport={"width": 1280, "height": 720})
                    client = page.context.new_cdp_session(page)
                    def on_frame(params):
                        if stop_event.is_set(): return
                        try:
                            # Acknowledge every frame using its unique sessionId
                            client.send("Page.screencastFrameAck", {"sessionId": int(params["sessionId"])})
                        except: pass
                        if not frame_q.full(): frame_q.put_nowait(params["data"])

                    client.on("Page.screencastFrame", on_frame)
                    client.send("Page.startScreencast", {"format": "jpeg", "quality": 60, "everyNthFrame": 2})

                    thought(f"Mission Initiated: {url}", "navigating")
                    page.goto(url, wait_until="domcontentloaded")
                    
                    frustration = 0

                    for step in range(80):
                        if stop_event.is_set(): break
                        
                        # --- COLLABORATIVE BLOCK ---
                        while not pause_event.is_set():
                            handle_gestures(page)
                            if stop_event.is_set(): break
                            page.wait_for_timeout(100)
                        
                        # Just resumed? Clear frustration
                        handle_gestures(page)

                        # --- MILESTONE SCAN ---
                        curr_url = page.url.lower()
                        progress = False
                        if not objectives["pricing"] and any(k in curr_url for k in ["pricing", "plans", "tier"]):
                            thought("✓ Milestone 1: Pricing Captured.", "found")
                            for _ in range(2): page.evaluate("window.scrollBy(0, 400)"); page.wait_for_timeout(600)
                            objectives["pricing"] = True; progress = True; page.goto(url)
                        
                        if not objectives["docs"] and any(k in curr_url for k in ["docs", "documentation", "api", "guide", "developer"]):
                            if page.url != url:
                                thought("✓ Milestone 2: Documentation Located.", "found")
                                for _ in range(2): page.evaluate("window.scrollBy(0, 400)"); page.wait_for_timeout(600)
                                objectives["docs"] = True; progress = True

                        if progress: frustration = 0; continue

                        # --- STRATEGIC BRAIN ---
                        try:
                            content = page.evaluate("() => document.body.innerText")
                            decision = ask_brain(content, page.url)
                            goal, label = decision.get("goal"), decision.get("target")
                            thought(decision.get("thought", "Analyzing path..."), "scanning")

                            if goal in ["CLICK", "HOVER"]:
                                el, matched = find_element(page, [label]) if label else (None, None)
                                if not el:
                                    keys = ["Pricing", "Plans"] if not objectives["pricing"] else ["Docs", "Developers", "Library"]
                                    el, matched = find_element(page, keys)
                                
                                if el:
                                    frustration = 0
                                    thought(f"Locking on: {matched}", "clicking")
                                    el.evaluate("el => { el.scrollIntoView({behavior:'smooth', block:'center'}); el.style.outline='10px solid cyan'; }")
                                    page.wait_for_timeout(1000)
                                    el.hover(force=True)
                                    if goal == "CLICK":
                                        el.evaluate("el => el.setAttribute('target', '_self')")
                                        try: el.click(force=True, timeout=3000)
                                        except: el.evaluate("el => el.click()")
                                    page.wait_for_timeout(1500)
                                else:
                                    frustration += 1
                                    if frustration >= 2:
                                        thought("🆘 AGENT FRUSTRATED: Please guide me manually!", "error")
                                        pause_event.clear()
                                        thought_q.put({"type": "auto_pause"})
                                    else:
                                        page.evaluate("window.scrollBy(0, 600)")
                            elif goal == "FINISH" and objectives["pricing"] and objectives["docs"]:
                                thought("Mission successful.", "complete"); break
                        except Exception as e:
                            thought(f"Strategic Hiccup: {e}", "info")
                        
                        page.wait_for_timeout(500)

                    thought("Mission Sequence Complete.", "complete")
                    while not stop_event.is_set():
                        handle_gestures(page)
                        page.wait_for_timeout(200)
                    browser.close()
            except Exception as e: thought(f"Fatal System Error: {e}", "error")
            finally: thought_q.put({"__sentinel__": True})

        thread = threading.Thread(target=run_browser, daemon=True); thread.start()

        async def drain():
            while not stop_event.is_set():
                while not thought_q.empty():
                    msg = thought_q.get_nowait()
                    if msg.get("__sentinel__"): return
                    await websocket.send_text(json.dumps(msg if "type" in msg else {"type": "thought", **msg}))
                while not frame_q.empty():
                    await websocket.send_text(json.dumps({"type": "frame", "data": frame_q.get_nowait()}))
                await asyncio.sleep(0.02)

        async def receive():
            while True:
                try:
                    data = await websocket.receive_json()
                    dtype = data.get("type")
                    if dtype == "pause": 
                        pause_event.clear()
                        thought_q.put({"text": "⏸ AGENT PAUSED: User is in control.", "state": "info"})
                    elif dtype == "resume": 
                        pause_event.set()
                        thought_q.put({"text": "▶ RESUMING: Agent re-scanning page state...", "state": "info"})
                    else: action_q.put(data)
                except: break

        await asyncio.gather(drain(), receive())
    except Exception: pass
    finally: stop_event.set()
