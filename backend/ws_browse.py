# WebSocket handler for the browser automation agent. Drives Chrome with
# Playwright, uses Gemini to decide what to click, detects when it's stuck
# and either asks the user for help or gives up if the site looks invalid.

import asyncio
import json
import re
import threading
import queue as tqueue
import uuid
import time
import ipaddress
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from google import genai
from google.genai import types as genai_types

from .config import GEMINI_API_KEY, EXTRACTS_DIR
from .db import _db_lock, _db
from .vectorize import _chunk_text
from .browser import _sessions, _Session, _launch_bare_chrome, _wait_for_debug_port, save_extract
from .vectorize import _is_domain_complete

router = APIRouter()


@router.websocket("/ws/browse")
async def browse_websocket(
    websocket: WebSocket,
    session: str = None,
    role:    str = "riva",
):
    await websocket.accept()

    db_session_id = str(uuid.uuid4())
    frame_q:   tqueue.Queue[str]  = tqueue.Queue(maxsize=8)
    thought_q: tqueue.Queue[dict] = tqueue.Queue()
    action_q:  tqueue.Queue[dict] = tqueue.Queue()
    stop_event  = threading.Event()
    pause_event = threading.Event()
    pause_event.set()

    try:
        init_data  = await websocket.receive_json()
        target_url = init_data.get("url", "").strip()
        if not target_url:
            await websocket.close(code=1008, reason="No URL provided")
            return
        if not target_url.startswith("http"):
            target_url = "https://" + target_url

        with _db_lock, _db() as conn:
            conn.execute(
                "INSERT INTO sessions (id, target_url, started_at) VALUES (?, ?, ?)",
                (db_session_id, target_url, datetime.now(timezone.utc).isoformat()),
            )

        def run_browser():
            from playwright.sync_api import sync_playwright
            import os
            import shutil
            import socket

            objectives    = {"pricing": False, "docs": False}
            visited_urls: list[str] = []
            gemini_client = genai.Client(api_key=GEMINI_API_KEY)

            def thought(text: str, state: str = "info"):
                print(f"[{state.upper()}] {text}")
                thought_q.put({"text": text, "state": state})

            def save_intel(intel_type: str, url: str, content: str):
                with _db_lock, _db() as conn:
                    conn.execute(
                        "INSERT INTO intel (session_id, type, url, content, captured_at)"
                        " VALUES (?, ?, ?, ?, ?)",
                        (db_session_id, intel_type, url, content,
                         datetime.now(timezone.utc).isoformat()),
                    )

            def finish_session(status: str):
                with _db_lock, _db() as conn:
                    conn.execute(
                        "UPDATE sessions SET status=?, completed_at=?,"
                        " pricing_found=?, docs_found=? WHERE id=?",
                        (status, datetime.now(timezone.utc).isoformat(),
                         int(objectives["pricing"]), int(objectives["docs"]),
                         db_session_id),
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
                        pass
                    return bool(host)
                except Exception:
                    return False

            def find_element(page, keywords):
                def _try_scroll_and_return(loc, k):
                    try:
                        el = loc.first
                        # scroll into view first so off-screen elements become reachable
                        try:
                            el.scroll_into_view_if_needed(timeout=500)
                        except Exception:
                            pass
                        if el.is_visible(timeout=500):
                            return el, k
                        # element exists but hidden (e.g. opacity:0), click it anyway
                        if el.count() > 0:
                            return el, k
                    except Exception:
                        pass
                    return None, None

                for k in keywords:
                    for role_name in ["link", "button", "menuitem"]:
                        try:
                            loc = page.get_by_role(role_name, name=k, exact=False)
                            el, matched = _try_scroll_and_return(loc, k)
                            if el:
                                return el, matched
                        except Exception:
                            pass
                    try:
                        loc = page.locator(
                            f'a:has-text("{k}"), button:has-text("{k}")'
                        )
                        el, matched = _try_scroll_and_return(loc, k)
                        if el:
                            return el, matched
                    except Exception:
                        pass
                    try:
                        loc = page.get_by_text(k, exact=False)
                        el, matched = _try_scroll_and_return(loc, k)
                        if el:
                            return el, matched
                    except Exception:
                        pass
                return None, None

            def ask_brain(
                page_content: str,
                current_url: str,
                headings: str,
                screenshot_bytes: bytes | None = None,
                already_tried: set | None = None,
            ) -> dict:
                nonlocal gemini_fail_count
                if not GEMINI_API_KEY:
                    return {"goal": "ERROR", "thought": "Missing GEMINI_API_KEY"}

                p_text = "PRICING: COMPLETED" if objectives["pricing"] else "PRICING: NOT_STARTED"
                d_text = "DOCS: COMPLETED"    if objectives["docs"]    else "DOCS: NOT_STARTED"
                recent = visited_urls[-5:]

                tried_note = ""
                if already_tried:
                    tried_note = f"ALREADY TRIED on this page: {', '.join(sorted(already_tried))}\n"

                prompt = (
                    f"Riva Pathfinder Mission — current page: {current_url}\n"
                    f"MILESTONES : {p_text} | {d_text}\n"
                    f"RECENT URLS: {recent}\n"
                    f"{tried_note}"
                    f"PAGE HEADINGS & NAV & FOOTER: {headings[:1200]}\n\n"
                    "RULES:\n"
                    "- Only pursue NOT_STARTED milestones.\n"
                    "- If Pricing is COMPLETED focus entirely on Documentation.\n"
                    "- HOVER nav items like 'Developers' or 'Products' to reveal sub-menus.\n"
                    "- Check footer links — many sites put Docs/Documentation in the footer.\n"
                    "- If a URL appears twice in RECENT URLS, try a different element.\n"
                    "- Do NOT target elements listed in ALREADY TRIED — pick a different one.\n"
                    "- Use SCROLL to reveal more content when no useful links are visible yet.\n"
                    "- Use TYPE to search for content (set target to the search input label, text to the query).\n\n"
                    'Reply ONLY with JSON (no markdown):\n'
                    '{"thought":"..","goal":"CLICK"|"HOVER"|"SCROLL"|"TYPE"|"SURVEY"|"FINISH","target":"..","text":"(only for TYPE)"}\n\n'
                    f"Page text:\n{page_content[:7000]}"
                )

                def _call():
                    if screenshot_bytes:
                        contents = [
                            genai_types.Part.from_bytes(
                                data=screenshot_bytes, mime_type="image/jpeg"
                            ),
                            genai_types.Part.from_text(text=prompt),
                        ]
                    else:
                        contents = prompt
                    res = gemini_client.models.generate_content(
                        model="gemini-2.5-flash",
                        contents=contents,
                        config=genai_types.GenerateContentConfig(
                            http_options=genai_types.HttpOptions(timeout=12000),
                        ),
                    )
                    txt = res.text.strip()
                    if "```" in txt:
                        txt = txt.split("```")[1].lstrip("json").strip()
                        txt = txt.split("```")[0].strip()
                    return json.loads(txt)

                try:
                    return _call()
                except Exception as e:
                    thought(f"Brain error: {e}", "info")
                    gemini_fail_count += 1
                    return {"goal": "SURVEY", "thought": "Brain error", "__api_fail__": True}

            def validate_initial_page(page) -> dict:
                """
                Gemini looks at the landing page (screenshot + text) and decides:
                - Is this a real software/SaaS product site?
                - Is it plausible that a Pricing page and Docs page exist here?
                Returns {"valid": bool, "reason": str, "pricing_likely": bool, "docs_likely": bool}
                """
                try:
                    content = page.evaluate("() => document.body.innerText")[:3000]
                    title   = page.title()
                    try:
                        shot = page.screenshot(type="jpeg", quality=60)
                    except Exception:
                        shot = None

                    prompt = (
                        f"URL: {page.url}\nPage title: {title}\n\n"
                        "Determine if this page belongs to a SOFTWARE PRODUCT or SaaS COMPANY "
                        "that would realistically have a Pricing page AND a Documentation/API page.\n\n"
                        "CLEARLY INVALID: parked domains, 404 errors, personal blogs, news/media sites, "
                        "social media profiles, e-commerce stores selling physical goods, government sites, "
                        "Wikipedia articles, entertainment sites, search engines.\n\n"
                        "VALID: developer tools, API services, SaaS platforms, cloud services, "
                        "productivity software, B2B tools, developer platforms, fintech products.\n\n"
                        f"Page text:\n{content}\n\n"
                        'Reply ONLY with JSON (no markdown):\n'
                        '{"valid": true/false, "reason": "one sentence explanation", '
                        '"pricing_likely": true/false, "docs_likely": true/false}'
                    )

                    if shot:
                        contents = [
                            genai_types.Part.from_bytes(data=shot, mime_type="image/jpeg"),
                            genai_types.Part.from_text(text=prompt),
                        ]
                    else:
                        contents = prompt

                    res = gemini_client.models.generate_content(
                        model="gemini-2.5-flash",
                        contents=contents,
                        config=genai_types.GenerateContentConfig(
                            http_options=genai_types.HttpOptions(timeout=12000),
                        ),
                    )
                    txt = res.text.strip()
                    if "```" in txt:
                        txt = txt.split("```")[1].lstrip("json").strip()
                        txt = txt.split("```")[0].strip()
                    return json.loads(txt)
                except Exception as e:
                    # If validation itself fails, let browsing proceed rather than blocking
                    return {"valid": True, "reason": f"Validation check error ({e})", "pricing_likely": True, "docs_likely": True}

            def full_page_extract(page) -> str:
                page.evaluate("window.scrollTo(0, 0)")
                page.wait_for_timeout(400)
                prev_height = -1
                for _ in range(40):
                    page.evaluate("window.scrollBy(0, 800)")
                    page.wait_for_timeout(350)
                    new_height = page.evaluate(
                        "document.documentElement.scrollHeight"
                    )
                    if new_height == prev_height:
                        break
                    prev_height = new_height
                return page.evaluate("() => document.body.innerText")

            def crawl_docs(page, landing_url: str, domain: str) -> int:
                text = full_page_extract(page)
                save_extract(domain, "docs", landing_url, text)
                save_intel("docs", landing_url, text)
                thought(f"Docs page 1 saved ({len(text):,} chars)", "found")

                links: list[str] = page.evaluate("""() => {
                    const host = window.location.hostname;
                    const basePath = window.location.pathname.split('/').slice(0, 3).join('/');
                    return [...new Set(
                        Array.from(document.querySelectorAll('a[href]'))
                            .map(a => a.href)
                            .filter(href => {
                                try {
                                    const u = new URL(href);
                                    return u.hostname === host
                                        && u.pathname !== window.location.pathname
                                        && !u.hash
                                        && (u.pathname.startsWith(basePath) || basePath === '');
                                } catch { return false; }
                            })
                    )];
                }""")

                count   = 1
                visited = {landing_url}
                for link in links:
                    if stop_event.is_set():
                        break
                    if link in visited:
                        continue
                    try:
                        thought(f"Docs crawl {count+1}: {link}", "navigating")
                        page.goto(link, wait_until="domcontentloaded", timeout=12000)
                        page.wait_for_timeout(600)
                        text = full_page_extract(page)
                        save_extract(domain, "docs", link, text)
                        save_intel("docs", link, text)
                        visited.add(link)
                        count += 1
                    except Exception as e:
                        thought(f"Docs crawl skip: {e}", "info")

                thought(f"Docs crawl complete — {count} pages saved.", "found")
                return count

            # when the user pastes a URL while stuck, we auto-resume instead of waiting for an explicit resume click
            _stuck_pause = False

            def handle_gestures(page):
                nonlocal _stuck_pause
                while not action_q.empty():
                    act  = action_q.get_nowait()
                    try:
                        atype = act.get("type")
                        if atype == "click":
                            x = float(act.get("x", 0)) * 1280
                            y = float(act.get("y", 0)) * 720
                            page.mouse.click(x, y)
                            thought("Manual click", "user")
                        elif atype == "scroll":
                            delta_y = float(act.get("delta_y", 400))
                            page.evaluate(f"window.scrollBy(0, {delta_y})")
                            thought("Manual scroll", "user")
                        elif atype == "type":
                            text = act.get("text", "")
                            if text:
                                page.keyboard.type(text)
                                thought("Manual type", "user")
                        elif atype == "goto":
                            t_url = act.get("url", "").strip()
                            if not t_url.startswith("http"):
                                t_url = "https://" + t_url
                            if not validate_url(t_url):
                                thought("Blocked: invalid or private URL.", "error")
                                continue
                            thought(f"Navigating to: {t_url}", "navigating")
                            page.goto(
                                t_url, wait_until="domcontentloaded", timeout=10000
                            )
                            # If we were stuck waiting for a URL from the user, auto-resume
                            if _stuck_pause:
                                _stuck_pause = False
                                thought("URL received — resuming agent.", "info")
                                pause_event.set()
                    except Exception as e:
                        print(f"Gesture error: {e}")

            finishing_domain = re.sub(
                r'^www\.', '', urlparse(target_url).hostname or "unknown"
            )

            # skip browser entirely if this domain is already in the vector index
            if _is_domain_complete(EXTRACTS_DIR / finishing_domain):
                thought("Domain already fully vectorized — skipping browser.", "complete")
                finish_session("complete")
                thought_q.put({"type": "browse_complete"})
                if session and session in _sessions:
                    _sessions[session].mark_complete(finishing_domain)
                thought_q.put({"__sentinel__": True})
                return

            # persistent profile per role means cookies/auth survive across runs
            profile_dir = Path(__file__).parent / ".browser_profiles" / (role or "default")
            profile_dir.mkdir(parents=True, exist_ok=True)

            _LAUNCH_ARGS = [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ]
            _UA = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )

            _chrome_proc = None
            try:
                with sync_playwright() as p:
                    for _attempt in range(2):
                        ctx = None
                        browser = None
                        _retry_with_cdp = False

                        if _attempt == 0:
                            # Normal Playwright launch with persistent profile
                            try:
                                ctx = p.chromium.launch_persistent_context(
                                    str(profile_dir),
                                    headless=False,
                                    channel="chrome",
                                    args=_LAUNCH_ARGS,
                                    user_agent=_UA,
                                    locale="en-US",
                                    timezone_id="America/New_York",
                                    viewport={"width": 1280, "height": 720},
                                )
                                thought("Launched Chrome with persistent profile.", "info")
                            except Exception as launch_err:
                                thought(f"Chrome unavailable ({launch_err}) — using Chromium.", "info")
                                browser = p.chromium.launch(headless=False, args=_LAUNCH_ARGS)
                                ctx = browser.new_context(
                                    viewport={"width": 1280, "height": 720},
                                    user_agent=_UA,
                                    locale="en-US",
                                    timezone_id="America/New_York",
                                )
                        else:
                            thought(
                                "Challenge loop detected — launching Chrome without automation flags.",
                                "error",
                            )
                            debug_port = 9222
                            cdp_profile = str(profile_dir) + "_cdp"
                            try:
                                _chrome_proc = _launch_bare_chrome(
                                    target_url, debug_port, cdp_profile
                                )
                                if not _wait_for_debug_port(debug_port):
                                    thought("CDP port did not open in time — aborting.", "error")
                                    break
                                thought("Connecting to Chrome over CDP...", "info")
                                cdp_browser = p.chromium.connect_over_cdp(
                                    f"http://localhost:{debug_port}"
                                )
                                ctx = (
                                    cdp_browser.contexts[0]
                                    if cdp_browser.contexts
                                    else cdp_browser.new_context(
                                        viewport={"width": 1280, "height": 720},
                                        user_agent=_UA,
                                    )
                                )
                                browser = cdp_browser
                            except Exception as cdp_err:
                                thought(f"CDP launch failed: {cdp_err}", "error")
                                break

                        page = ctx.pages[0] if ctx.pages else ctx.new_page()

                        _domain_dir = EXTRACTS_DIR / finishing_domain
                        _folder_existed = _domain_dir.exists()
                        _domain_dir.mkdir(parents=True, exist_ok=True)
                        if _folder_existed:
                            existing = len(list(_domain_dir.glob("*.txt")))
                            thought(f"Extract folder already exists: extracts/{finishing_domain}/ ({existing} files)", "info")
                        else:
                            thought(f"Extract folder created: extracts/{finishing_domain}/", "info")

                        try:
                            from playwright_stealth import stealth_sync
                            stealth_sync(page)
                        except ImportError:
                            pass

                        cdp_session = ctx.new_cdp_session(page)

                        def on_frame(params):
                            if stop_event.is_set():
                                return
                            try:
                                cdp_session.send(
                                    "Page.screencastFrameAck",
                                    {"sessionId": int(params["sessionId"])},
                                )
                            except Exception:
                                pass
                            if not frame_q.full():
                                frame_q.put_nowait(params["data"])

                        cdp_session.on("Page.screencastFrame", on_frame)
                        cdp_session.send("Page.startScreencast", {
                            "format": "jpeg", "quality": 60, "everyNthFrame": 2,
                        })

                        thought(f"Mission started: {target_url}", "navigating")
                        if not validate_url(target_url):
                            thought(f"Blocked: invalid or private URL — {target_url}", "error")
                            finish_session("error")
                            thought_q.put({"type": "browse_complete"})
                            return
                        if _attempt == 0 or page.url.rstrip("/") != target_url.rstrip("/"):
                            page.goto(target_url, wait_until="domcontentloaded")
                        visited_urls.append(target_url)

                        if _attempt == 0:
                            thought("Checking if this site looks like a valid product...", "info")
                            page.wait_for_timeout(1000)
                            _v = validate_initial_page(page)
                            if not _v.get("valid", True):
                                thought(
                                    f"\u26a0 Invalid URL — {_v.get('reason', 'unrecognised site')}. "
                                    "This doesn't look like a software product with pricing and documentation. "
                                    "Please start a new session with a valid product URL (e.g. stripe.com, vercel.com).",
                                    "error",
                                )
                                thought_q.put({"type": "session_invalid"})
                                finish_session("error")
                                if session and session in _sessions:
                                    _sessions[session].cancel()
                                thought_q.put({"__sentinel__": True})
                                stop_event.set()
                                return
                            # Valid but note low confidence on specific pages
                            if not _v.get("pricing_likely", True):
                                thought(f"Note: Pricing page may be hard to find — {_v.get('reason', '')}", "info")
                            if not _v.get("docs_likely", True):
                                thought(f"Note: Documentation may be hard to find — {_v.get('reason', '')}", "info")

                        frustration         = 0
                        challenge_count     = 0   # total challenge detections this attempt
                        challenge_cooldown  = 0
                        was_paused          = False
                        dead_steps        = 0  # consecutive steps with no milestone progress
                        gemini_fail_count = 0  # total Gemini API failures this attempt
                        tried_on_page: dict[str, set] = {}  # tracks elements tried per URL to avoid repeats
                        suspicion_level   = 0  # increments on each HITL trigger, session ends at 3

                        def _is_challenge_page():
                            try:
                                title = page.title().lower()
                                return (
                                    "just a moment" in title
                                    or "are you human" in title
                                    or "verify you are human" in title
                                    or "enable javascript" in title
                                    or "cdn-cgi" in curr_lower
                                    or "cf-challenge" in curr_lower
                                )
                            except Exception:
                                return False

                        for step in range(80):
                            if stop_event.is_set():
                                break

                            was_paused = not pause_event.is_set()
                            while not pause_event.is_set():
                                handle_gestures(page)
                                if stop_event.is_set():
                                    break
                                page.wait_for_timeout(100)
                            if stop_event.is_set():
                                break

                            if was_paused:
                                thought("Resumed — waiting for page to settle...", "info")
                                page.wait_for_load_state("domcontentloaded")
                                page.wait_for_timeout(5000)

                            handle_gestures(page)

                            curr_url = page.url
                            if not visited_urls or visited_urls[-1] != curr_url:
                                visited_urls.append(curr_url)

                            curr_lower = curr_url.lower()
                            progress   = False
                            domain     = urlparse(curr_url).hostname or "unknown"

                            if challenge_cooldown > 0:
                                challenge_cooldown -= 1
                                # Even during cooldown: if another challenge appears,
                                # count it and decide whether to escalate to CDP fallback.
                                if _is_challenge_page():
                                    challenge_count += 1
                                    if challenge_count >= 2 and _attempt == 0:
                                        thought(
                                            "Challenge loop detected — will retry with native Chrome.",
                                            "error",
                                        )
                                        _retry_with_cdp = True
                                        break
                                    thought("Challenge page (in cooldown) — pausing for manual solve.", "error")
                                    thought_q.put({"type": "auto_pause"})
                                    pause_event.clear()
                                    challenge_cooldown = 15
                                    page.wait_for_timeout(500)
                                    continue
                            else:
                                if _is_challenge_page():
                                    challenge_count += 1
                                    if challenge_count >= 2 and _attempt == 0:
                                        thought(
                                            "Challenge loop detected — will retry with native Chrome.",
                                            "error",
                                        )
                                        _retry_with_cdp = True
                                        break
                                    thought(
                                        "Bot protection detected — solve the challenge in "
                                        "the browser, wait for the page to fully load, "
                                        "then click Resume.",
                                        "error",
                                    )
                                    thought_q.put({"type": "auto_pause"})
                                    pause_event.clear()
                                    challenge_cooldown = 15
                                    page.wait_for_timeout(500)
                                    continue

                            _page_title = ""
                            _page_h1 = ""
                            try:
                                _page_title = page.title().lower()
                                _page_h1 = page.evaluate(
                                    "() => (document.querySelector('h1') || {innerText:''}).innerText"
                                ).lower()
                            except Exception:
                                pass

                            _PRICING_URL = ["pricing", "plans", "tier", "cost", "subscribe", "billing", "upgrade"]
                            _PRICING_CONTENT = ["pricing", "plans", "cost", "billing", "tier"]
                            _DOCS_URL = ["docs", "documentation", "api", "guide", "developer", "reference", "tutorial", "quickstart", "getting-started", "sdk"]
                            _DOCS_CONTENT = ["documentation", "getting started", "api reference", "developer guide", "quickstart"]

                            if not objectives["pricing"] and (
                                any(k in curr_lower for k in _PRICING_URL)
                                or any(k in _page_title for k in _PRICING_CONTENT)
                                or any(k in _page_h1 for k in _PRICING_CONTENT)
                            ):
                                thought(
                                    "Milestone: Pricing page found — extracting.", "found"
                                )
                                page.wait_for_timeout(800)
                                content = full_page_extract(page)
                                save_intel("pricing", curr_url, content)
                                save_extract(finishing_domain, "pricing", curr_url, content)
                                thought(f"Pricing saved ({len(content):,} chars).", "found")
                                objectives["pricing"] = True
                                progress = True
                                page.goto(target_url, wait_until="domcontentloaded")

                            if not objectives["docs"] and curr_url != target_url and (
                                any(k in curr_lower for k in _DOCS_URL)
                                or any(k in _page_title for k in _DOCS_CONTENT)
                                or any(k in _page_h1 for k in _DOCS_CONTENT)
                            ):
                                thought("Milestone: Docs found — crawling.", "found")
                                page.wait_for_timeout(800)
                                crawl_docs(page, curr_url, finishing_domain)
                                objectives["docs"] = True
                                progress = True

                            if progress:
                                frustration     = 0
                                dead_steps      = 0
                                suspicion_level = 0  # real progress clears suspicion
                                if objectives["pricing"] and objectives["docs"]:
                                    thought("All milestones complete.", "complete")
                                    break
                                continue

                            dead_steps += 1
                            missing_milestone = "docs" if objectives["pricing"] else "pricing"

                            # Trigger HITL if: 3 consecutive dead steps, OR 4 Gemini API failures total
                            if dead_steps >= 3 or gemini_fail_count >= 4:
                                suspicion_level += 1
                                _page_hint = (
                                    "their Pricing or Plans page"
                                    if missing_milestone == "pricing"
                                    else "their Documentation, Docs, or API Reference page"
                                )
                                # If suspicion is high enough, give up entirely rather than asking again
                                if suspicion_level >= 3:
                                    thought(
                                        f"\u26a0 After extensive searching I still can't find {_page_hint} on {finishing_domain}. "
                                        "This site may not have accessible pricing or documentation pages. "
                                        "Please start a new session with a different URL.",
                                        "error",
                                    )
                                    thought_q.put({"type": "session_invalid"})
                                    finish_session("error")
                                    if session and session in _sessions:
                                        _sessions[session].cancel()
                                    stop_event.set()
                                    break
                                thought(
                                    f"\u26a0 I've tried {dead_steps} times and can't find {_page_hint} automatically. "
                                    f"Here's what to do: open {finishing_domain} in your own browser tab, "
                                    f"navigate to {_page_hint} yourself, then copy the full URL from your "
                                    "browser's address bar, paste it into the search bar above, and press Enter. "
                                    "I'll take over as soon as you do.",
                                    "hitl",
                                )
                                thought_q.put({"type": "stuck_guidance", "milestone": missing_milestone})
                                thought_q.put({"type": "auto_pause"})
                                pause_event.clear()
                                _stuck_pause = True
                                dead_steps = 0
                                gemini_fail_count = 0

                            try:
                                content  = page.evaluate("() => document.body.innerText")
                                headings = page.evaluate(
                                    "() => Array.from(document.querySelectorAll('h1,h2,h3,nav a,header a,footer a,[role=navigation] a'))"
                                    ".map(e => e.innerText.trim()).filter(Boolean).join(' | ')"
                                )
                                try:
                                    screenshot_bytes = page.screenshot(type="jpeg", quality=60)
                                except Exception:
                                    screenshot_bytes = None
                                curr_tried = tried_on_page.setdefault(
                                    page.url.split("#")[0].rstrip("/"), set()
                                )
                                decision = ask_brain(content, page.url, headings, screenshot_bytes, curr_tried)
                                goal     = decision.get("goal")
                                label    = decision.get("target", "")
                                thought(decision.get("thought", "Analyzing..."), "scanning")

                                if goal in ["CLICK", "HOVER"]:
                                    el, matched = (
                                        find_element(page, [label]) if label else (None, None)
                                    )
                                    if not el:
                                        fallback = (
                                            ["Pricing", "Plans", "Price"]
                                            if not objectives["pricing"]
                                            else [
                                                "Docs", "Documentation", "Developers",
                                                "API", "Guide", "Reference",
                                            ]
                                        )
                                        el, matched = find_element(page, fallback)

                                    if el:
                                        thought(f"Targeting: {matched}", "clicking")
                                        if matched:
                                            curr_tried.add(matched.lower())
                                        try:
                                            el.evaluate(
                                                "el => { el.scrollIntoView({behavior:'smooth',block:'center'});"
                                                " el.style.outline='4px solid cyan'; }"
                                            )
                                            page.wait_for_timeout(800)
                                            el.hover(force=True)
                                            if goal == "CLICK":
                                                url_before = page.url
                                                el.evaluate(
                                                    "el => el.setAttribute('target', '_self')"
                                                )
                                                try:
                                                    el.click(force=True, timeout=3000)
                                                except Exception:
                                                    el.evaluate("el => el.click()")
                                                page.wait_for_timeout(2000)
                                                if page.url == url_before:
                                                    try:
                                                        href = el.evaluate(
                                                            "el => el.href || el.getAttribute('href') || ''"
                                                        )
                                                        if (
                                                            href
                                                            and href.startswith("http")
                                                            and href != url_before
                                                        ):
                                                            thought(
                                                                "Click didn't navigate — going via href",
                                                                "navigating",
                                                            )
                                                            page.goto(
                                                                href,
                                                                wait_until="domcontentloaded",
                                                                timeout=10000,
                                                            )
                                                        else:
                                                            thought(
                                                                "Click had no effect — scrolling",
                                                                "info",
                                                            )
                                                            page.evaluate("window.scrollBy(0, 400)")
                                                    except Exception:
                                                        pass
                                            page.wait_for_timeout(1000)
                                            frustration = 0
                                        except Exception as action_err:
                                            frustration += 1
                                            thought(
                                                f"Action failed on '{matched}' ({frustration}/3): {action_err}",
                                                "info",
                                            )
                                            if frustration >= 3:
                                                suspicion_level += 1
                                                milestone = (
                                                    "docs" if objectives["pricing"] else "pricing"
                                                )
                                                _page_hint = (
                                                    "their Pricing or Plans page"
                                                    if milestone == "pricing"
                                                    else "their Documentation, Docs, or API Reference page"
                                                )
                                                thought(
                                                    f"\u26a0 I found what looks like {_page_hint} but can't click into it. "
                                                    f"Go there yourself in your own browser tab, copy the URL from the address bar, "
                                                    "paste it into the search bar above, and press Enter — I'll take over from there.",
                                                    "hitl",
                                                )
                                                thought_q.put({
                                                    "type": "stuck_guidance",
                                                    "milestone": milestone,
                                                })
                                                thought_q.put({"type": "auto_pause"})
                                                pause_event.clear()
                                                _stuck_pause = True
                                                frustration = 0
                                            else:
                                                page.evaluate("window.scrollBy(0, 600)")
                                                page.wait_for_timeout(500)
                                    else:
                                        frustration += 1
                                        thought(
                                            f"Element not found (frustration {frustration}/3)", "info"
                                        )
                                        if frustration >= 3:
                                            suspicion_level += 1
                                            milestone = (
                                                "docs" if objectives["pricing"] else "pricing"
                                            )
                                            _page_hint = (
                                                "their Pricing or Plans page"
                                                if milestone == "pricing"
                                                else "their Documentation, Docs, or API Reference page"
                                            )
                                            thought(
                                                f"\u26a0 I can't find {_page_hint} on my own. "
                                                f"Open {finishing_domain} in your own browser tab, "
                                                f"navigate to {_page_hint} yourself, copy the full URL "
                                                "from the address bar, paste it into the search bar above, "
                                                "and press Enter — I'll jump in from there.",
                                                "hitl",
                                            )
                                            thought_q.put({
                                                "type": "stuck_guidance",
                                                "milestone": milestone,
                                            })
                                            thought_q.put({"type": "auto_pause"})
                                            pause_event.clear()
                                            _stuck_pause = True
                                            frustration = 0
                                        else:
                                            page.evaluate("window.scrollBy(0, 600)")
                                            page.wait_for_timeout(500)

                                elif goal == "SCROLL":
                                    page.evaluate("window.scrollBy(0, 600)")
                                    page.wait_for_timeout(500)

                                elif goal == "TYPE":
                                    text_to_type = decision.get("text", "")
                                    if text_to_type and label:
                                        el, _ = find_element(page, [label])
                                        if el:
                                            try:
                                                el.click()
                                                el.type(text_to_type)
                                                page.keyboard.press("Enter")
                                                page.wait_for_timeout(1500)
                                                thought(f"Typed '{text_to_type}' into {label}", "info")
                                            except Exception as type_err:
                                                thought(f"Type failed: {type_err}", "info")

                                elif goal == "FINISH":
                                    if objectives["pricing"] and objectives["docs"]:
                                        thought("Mission complete.", "complete")
                                        break
                                    else:
                                        thought(
                                            "FINISH requested but milestones incomplete.", "info"
                                        )

                            except Exception as e:
                                thought(f"Step error: {e}", "info")

                            page.wait_for_timeout(500)

                        # If loop exhausted and milestones incomplete, trigger HITL before closing
                        if not (objectives["pricing"] and objectives["docs"]) and not stop_event.is_set():
                            missing_milestone = "docs" if objectives["pricing"] else "pricing"
                            _page_hint = (
                                "their Pricing or Plans page"
                                if missing_milestone == "pricing"
                                else "their Documentation, Docs, or API Reference page"
                            )
                            thought(
                                f"\u26a0 I've run out of steps and still couldn't find {_page_hint}. "
                                f"Open {finishing_domain} in your own browser tab, go to {_page_hint} yourself, "
                                "copy the full URL from your browser's address bar, paste it into the "
                                "search bar above, and press Enter — I'll take over from there.",
                                "hitl",
                            )
                            thought_q.put({"type": "stuck_guidance", "milestone": missing_milestone})
                            thought_q.put({"type": "auto_pause"})
                            pause_event.clear()
                            _stuck_pause = True
                            # Wait for user to paste URL before closing browser
                            while not stop_event.is_set():
                                page.wait_for_timeout(500)

                        try:
                            ctx.close()
                        except Exception:
                            pass
                        if browser:
                            try:
                                browser.close()
                            except Exception:
                                pass

                        if _retry_with_cdp:
                            pause_event.set()
                            continue  # go to attempt 1

                        finish_session("complete")
                        thought("Scraping complete — returning to homepage.", "complete")
                        thought_q.put({"type": "browse_complete"})

                        if session and session in _sessions:
                            _sessions[session].mark_complete(finishing_domain)

                        thought("Closing browser window.", "info")
                        stop_event.set()
                        break

            except Exception as e:
                thought(f"Fatal error: {e}", "error")
                finish_session("error")
                thought_q.put({"type": "browse_complete"})
                if session and session in _sessions:
                    _sessions[session].mark_complete(finishing_domain)
            finally:
                if _chrome_proc:
                    try:
                        _chrome_proc.terminate()
                    except Exception:
                        pass
                thought_q.put({"__sentinel__": True})

        thread = threading.Thread(target=run_browser, daemon=True)
        thread.start()

        async def drain():
            while True:
                while not thought_q.empty():
                    msg = thought_q.get_nowait()
                    if msg.get("__sentinel__"):
                        while not frame_q.empty():
                            try:
                                await websocket.send_text(
                                    json.dumps({"type": "frame", "data": frame_q.get_nowait()})
                                )
                            except Exception:
                                pass
                        try:
                            await websocket.close(code=1000)
                        except Exception:
                            pass
                        return
                    await websocket.send_text(
                        json.dumps(
                            msg if "type" in msg else {"type": "thought", **msg}
                        )
                    )
                while not frame_q.empty():
                    await websocket.send_text(
                        json.dumps({"type": "frame", "data": frame_q.get_nowait()})
                    )
                if stop_event.is_set() and thought_q.empty():
                    break
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
                    elif dtype == "skip":
                        thought_q.put({"text": "Skipping browse — using cached extracts.", "state": "complete"})
                        stop_event.set()
                        pause_event.set()
                        # run_browser will call mark_complete and emit browse_complete when it exits
                    else:
                        action_q.put(data)
                except Exception:
                    break

        await asyncio.gather(drain(), receive())

    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"Browse WebSocket error: {e}")
    finally:
        stop_event.set()
