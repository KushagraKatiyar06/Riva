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
        init_query = init_data.get("query", "").strip()
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
            user_query: str = init_query  # set from WS init; non-empty means query already known
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

            def _parse_json(txt: str) -> dict:
                """Extract JSON from Gemini output robustly."""
                txt = txt.strip()
                # Strip markdown fences
                if "```" in txt:
                    txt = txt.split("```")[1].lstrip("json").strip()
                    txt = txt.split("```")[0].strip()
                try:
                    return json.loads(txt)
                except json.JSONDecodeError:
                    pass
                # Find first balanced {...} block (handles leading thinking text)
                depth = 0
                start = None
                for i, ch in enumerate(txt):
                    if ch == "{":
                        if start is None:
                            start = i
                        depth += 1
                    elif ch == "}" and start is not None:
                        depth -= 1
                        if depth == 0:
                            try:
                                return json.loads(txt[start:i + 1])
                            except json.JSONDecodeError:
                                break
                raise ValueError(f"No valid JSON in response: {txt[:120]}")

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

                vectorized_note = "NOTE: This domain is already in our knowledge base.\n" if already_vectorized else ""
                query_note = f"USER QUERY (for docs phase): {user_query}\n" if user_query else ""

                prompt = (
                    f"Riva Pathfinder Mission — current page: {current_url}\n"
                    f"MILESTONES : {p_text} | {d_text}\n"
                    f"RECENT URLS: {recent}\n"
                    f"{tried_note}"
                    f"{vectorized_note}{query_note}"
                    f"PAGE HEADINGS & NAV & FOOTER: {headings[:1200]}\n\n"
                    "RULES:\n"
                    "- Current mission: find the PRICING or PLANS page and navigate to it.\n"
                    "- HOVER nav items like 'Developers' or 'Products' to reveal sub-menus.\n"
                    "- Check footer links — many sites put Pricing in the footer.\n"
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
                    return _parse_json(res.text)

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
                    return _parse_json(res.text)
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
                thought_q.put({"type": "page_saved"})

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
                        thought_q.put({"type": "page_saved"})
                    except Exception as e:
                        thought(f"Docs crawl skip: {e}", "info")

                thought(f"Docs crawl complete — {count} pages saved.", "found")
                return count

            def check_relevance(content: str) -> bool:
                # Ask Gemini if this page meaningfully helps answer the user's query
                try:
                    prompt = (
                        f"A user is researching: '{user_query}'\n\n"
                        f"Page content excerpt:\n{content[:3000]}\n\n"
                        "Does this page contain meaningful information that helps answer the user's question? "
                        "It does NOT need to be exclusively about the topic — a dedicated section is enough. "
                        "Only say NO if the page is entirely unrelated (e.g. a general overview, marketing copy, "
                        "release notes, or login page with no relevant technical content).\n"
                        "Reply with only YES or NO."
                    )
                    res = gemini_client.models.generate_content(
                        model="gemini-2.5-flash",
                        contents=prompt,
                        config=genai_types.GenerateContentConfig(
                            http_options=genai_types.HttpOptions(timeout=8000),
                        ),
                    )
                    return "yes" in res.text.strip().lower()
                except Exception:
                    return False  # on error, skip the page


            def navigate_to_docs_landing() -> bool:
                """
                After pricing is collected, navigate from wherever we are to the
                site's documentation landing page before starting the query search.
                Returns True if we ended up on a docs-like page, False if we timed out.
                """
                _DOCS_URL     = ["docs", "documentation", "api", "guide", "developer",
                                  "reference", "tutorial", "quickstart", "getting-started", "sdk"]
                _DOCS_CONTENT = ["documentation", "getting started", "api reference",
                                  "developer guide", "quickstart", "sdk reference"]

                thought("Navigating to documentation section...", "navigating")
                nav_tried: dict[str, set] = {}
                nav_frustration = 0

                for _step in range(20):
                    if stop_event.is_set():
                        return False

                    # Respect pause — user can take over navigation manually
                    was_paused = not pause_event.is_set()
                    while not pause_event.is_set():
                        handle_gestures(page)
                        if stop_event.is_set():
                            return False
                        page.wait_for_timeout(100)
                    if stop_event.is_set():
                        return False
                    if was_paused:
                        # Let the page the user navigated to settle
                        try:
                            page.wait_for_load_state("domcontentloaded", timeout=5000)
                        except Exception:
                            pass
                        page.wait_for_timeout(800)

                    handle_gestures(page)

                    curr_url   = page.url
                    curr_lower = curr_url.lower()

                    # Already on a docs-like page?
                    try:
                        _t = page.title().lower()
                        _h = page.evaluate(
                            "() => (document.querySelector('h1') || {innerText:''}).innerText"
                        ).lower()
                    except Exception:
                        _t = _h = ""

                    if (
                        any(k in curr_lower for k in _DOCS_URL)
                        or any(k in _t for k in _DOCS_CONTENT)
                        or any(k in _h for k in _DOCS_CONTENT)
                    ):
                        thought("Documentation landing found.", "found")
                        return True

                    # Ask Gemini to navigate toward docs
                    try:
                        content  = page.evaluate("() => document.body.innerText")
                        headings = page.evaluate(
                            "() => Array.from(document.querySelectorAll("
                            "'h1,h2,h3,nav a,header a,footer a,[role=navigation] a'))"
                            ".map(e => e.innerText.trim()).filter(Boolean).join(' | ')"
                        )
                        try:
                            shot = page.screenshot(type="jpeg", quality=60)
                        except Exception:
                            shot = None

                        curr_tried = nav_tried.setdefault(
                            curr_url.split("#")[0].rstrip("/"), set()
                        )
                        tried_note = (
                            f"ALREADY TRIED: {', '.join(sorted(curr_tried))}\n"
                            if curr_tried else ""
                        )

                        prompt = (
                            f"Mission: reach the DOCUMENTATION or API REFERENCE section of this site.\n"
                            f"Current URL: {curr_url}\n"
                            f"Recent URLs: {visited_urls[-5:]}\n"
                            f"{tried_note}"
                            f"PAGE HEADINGS & NAV & FOOTER: {headings[:1200]}\n\n"
                            "RULES:\n"
                            "- Look for nav links: 'Docs', 'Documentation', 'API', 'Developer', 'Reference', 'Guide'.\n"
                            "- HOVER items like 'Developers' or 'Products' to reveal sub-menus.\n"
                            "- Check footer — many sites put Documentation there.\n"
                            "- Do NOT target elements in ALREADY TRIED.\n"
                            "- SCROLL only if no docs link is visible yet.\n\n"
                            'Reply ONLY with JSON (no markdown):\n'
                            '{"thought":"..","goal":"CLICK"|"HOVER"|"SCROLL","target":"element label"}\n\n'
                            f"Page text:\n{content[:5000]}"
                        )

                        def _call():
                            _contents = (
                                [
                                    genai_types.Part.from_bytes(data=shot, mime_type="image/jpeg"),
                                    genai_types.Part.from_text(text=prompt),
                                ]
                                if shot else prompt
                            )
                            res = gemini_client.models.generate_content(
                                model="gemini-2.5-flash",
                                contents=_contents,
                                config=genai_types.GenerateContentConfig(
                                    http_options=genai_types.HttpOptions(timeout=12000),
                                ),
                            )
                            return _parse_json(res.text)

                        try:
                            decision = _call()
                        except Exception as e:
                            thought(f"Docs nav error: {e}", "info")
                            page.evaluate("window.scrollBy(0, 600)")
                            page.wait_for_timeout(500)
                            continue

                        goal  = decision.get("goal")
                        label = decision.get("target", "")
                        thought(decision.get("thought", "Looking for docs..."), "navigating")

                        if goal in ["CLICK", "HOVER"]:
                            el, matched = find_element(page, [label]) if label else (None, None)
                            if not el:
                                el, matched = find_element(
                                    page,
                                    ["Docs", "Documentation", "Developer", "API", "Reference", "Guide"],
                                )
                            if el:
                                thought(f"Targeting: {matched}", "clicking")
                                if matched:
                                    curr_tried.add(matched.lower())
                                try:
                                    el.evaluate(
                                        "el => { el.scrollIntoView({behavior:'smooth',block:'center'});"
                                        " el.style.outline='4px solid cyan'; }"
                                    )
                                    page.wait_for_timeout(600)
                                    el.hover(force=True)
                                    if goal == "HOVER":
                                        # wait for dropdown to paint then grab any docs link that appeared
                                        page.wait_for_timeout(900)
                                        try:
                                            dropdown_href = page.evaluate("""() => {
                                                const kw = ['docs','documentation','api','developer','reference','guide'];
                                                for (const a of document.querySelectorAll('a[href]')) {
                                                    const t = a.innerText.trim().toLowerCase();
                                                    const h = a.href.toLowerCase();
                                                    if (!kw.some(k => t.includes(k) || h.includes(k))) continue;
                                                    const r = a.getBoundingClientRect();
                                                    if (r.width > 0 && r.height > 0) return a.href;
                                                }
                                                return null;
                                            }""")
                                            if dropdown_href and validate_url(dropdown_href):
                                                thought(f"Docs link revealed in dropdown — navigating.", "navigating")
                                                page.goto(dropdown_href, wait_until="domcontentloaded", timeout=10000)
                                                page.wait_for_timeout(800)
                                                nav_frustration = 0
                                        except Exception:
                                            pass
                                    else:
                                        url_before = page.url
                                        el.evaluate("el => el.setAttribute('target','_self')")
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
                                                if href and href.startswith("http") and href != url_before:
                                                    page.goto(href, wait_until="domcontentloaded", timeout=10000)
                                            except Exception:
                                                pass
                                    page.wait_for_timeout(1000)
                                    nav_frustration = 0
                                except Exception as e:
                                    nav_frustration += 1
                                    thought(f"Click failed ({nav_frustration}): {e}", "info")
                            else:
                                nav_frustration += 1
                                thought(f"Docs link not found ({nav_frustration})", "info")
                                page.evaluate("window.scrollBy(0, 600)")
                                page.wait_for_timeout(500)
                                if nav_frustration >= 4:
                                    break
                        elif goal == "SCROLL":
                            page.evaluate("window.scrollBy(0, 600)")
                            page.wait_for_timeout(500)

                    except Exception as e:
                        thought(f"Docs nav step error: {e}", "info")

                    page.wait_for_timeout(400)

                # Try common docs paths before giving up or asking the user
                _parsed = urlparse(page.url)
                _root   = f"{_parsed.scheme}://{_parsed.netloc}"
                for _path in ["/docs", "/documentation", "/api", "/developers", "/developer", "/reference"]:
                    if stop_event.is_set():
                        return False
                    try:
                        thought(f"Trying {_path}...", "navigating")
                        page.goto(_root + _path, wait_until="domcontentloaded", timeout=8000)
                        page.wait_for_timeout(600)
                        _u = page.url.lower()
                        _t = page.title().lower()
                        _h = page.evaluate(
                            "() => (document.querySelector('h1') || {innerText:''}).innerText"
                        ).lower()
                        if (any(k in _u for k in _DOCS_URL)
                                or any(k in _t for k in _DOCS_CONTENT)
                                or any(k in _h for k in _DOCS_CONTENT)):
                            thought("Documentation landing found.", "found")
                            return True
                    except Exception:
                        pass

                # Still nothing — ask the user
                thought(
                    "⚠ Can't find the docs section automatically. "
                    "Paste the docs URL above and press Enter — I'll take over from there.",
                    "hitl",
                )
                thought_q.put({"type": "stuck_guidance", "milestone": "docs"})
                thought_q.put({"type": "auto_pause"})
                pause_event.clear()

                for _ in range(120):
                    if stop_event.is_set():
                        return False
                    while not pause_event.is_set():
                        handle_gestures(page)
                        if stop_event.is_set():
                            return False
                        page.wait_for_timeout(100)
                    _u = page.url.lower()
                    _t = page.title().lower()
                    try:
                        _h = page.evaluate(
                            "() => (document.querySelector('h1') || {innerText:''}).innerText"
                        ).lower()
                    except Exception:
                        _h = ""
                    if (any(k in _u for k in _DOCS_URL)
                            or any(k in _t for k in _DOCS_CONTENT)
                            or any(k in _h for k in _DOCS_CONTENT)):
                        thought("Documentation landing found.", "found")
                        return True
                    page.wait_for_timeout(500)

                return False

            def _do_save(url: str, text: str, relevant_docs_list: list):
                save_extract(finishing_domain, "docs", url, text)
                save_intel("docs", url, text)
                thought(f"Section saved ({len(text):,} chars).", "found")
                thought_q.put({"type": "relevant_doc_saved"})
                relevant_docs_list.append(url)
                if len(relevant_docs_list) == 5 and session and session in _sessions:
                    _sessions[session].signal_early(finishing_domain)

            def _save_planned_page(url: str, content: str,
                                    relevant_docs_list: list) -> bool:
                if len(content.strip()) < 300:
                    return False
                _do_save(url, content, relevant_docs_list)
                return True

            def _save_relevant_page(url: str, content: str,
                                     relevant_docs_list: list) -> bool:
                if not check_relevance(content):
                    return False
                _do_save(url, content, relevant_docs_list)
                return True

            def _pause_check() -> bool:
                """Wait while paused. Returns False if stop_event fired."""
                was_paused = not pause_event.is_set()
                while not pause_event.is_set():
                    handle_gestures(page)
                    if stop_event.is_set():
                        return False
                    page.wait_for_timeout(100)
                if was_paused:
                    thought("Resumed — waiting for page to settle...", "info")
                    page.wait_for_load_state("domcontentloaded")
                    page.wait_for_timeout(3000)
                handle_gestures(page)
                return not stop_event.is_set()

            def query_guided_docs_search():
                # Phase 1: scan the docs landing page and build an ordered target list.
                # Phase 2: navigate each target directly by URL, check relevance.
                # Phase 3: HITL if the user wants additional sections.
                docs_visited: set[str] = set()
                relevant_docs_list: list[str] = []

                # load previously indexed doc URLs so we skip them in this run
                if already_vectorized:
                    existing_dir = EXTRACTS_DIR / finishing_domain
                    for _fp in existing_dir.glob("docs_*.txt"):
                        try:
                            with open(_fp, "r", encoding="utf-8") as _fh:
                                for _line in _fh:
                                    if _line.startswith("URL"):
                                        _url = _line.split(":", 1)[1].strip()
                                        if _url:
                                            docs_visited.add(_url)
                                        break
                        except Exception:
                            pass
                    if docs_visited:
                        thought(f"{len(docs_visited)} pages already indexed — scanning for gaps.", "scanning")

                thought("Scanning docs structure to build search plan...", "scanning")
                planned_urls: list[str] = []

                try:
                    parsed_docs = urlparse(page.url)
                    path_parts  = [p for p in parsed_docs.path.split('/') if p]
                    _DOCS_ROOTS = {"docs", "documentation", "api", "guide",
                                   "developer", "reference", "sdk"}
                    root_idx = next(
                        (i for i, p in enumerate(path_parts) if p.lower() in _DOCS_ROOTS),
                        None,
                    )
                    if root_idx is not None and len(path_parts) > root_idx + 1:
                        docs_root = (
                            f"{parsed_docs.scheme}://{parsed_docs.netloc}/"
                            + "/".join(path_parts[: root_idx + 1]) + "/"
                        )
                        thought(f"Navigating to docs root: {docs_root}", "info")
                        page.goto(docs_root, wait_until="domcontentloaded", timeout=12000)
                except Exception:
                    pass

                try:
                    page.wait_for_load_state("networkidle", timeout=5000)
                except Exception:
                    page.wait_for_timeout(2000)
                try:
                    page.evaluate("window.scrollBy(0, 600)")
                    page.wait_for_timeout(600)
                    page.evaluate("window.scrollTo(0, 0)")
                except Exception:
                    pass

                # extract hierarchical docs structure
                link_data: list[dict] = []
                try:
                    link_data = page.evaluate("""() => {
                        const seen = new Set();
                        const items = [];

                        const namedRoots = Array.from(document.querySelectorAll(
                            'nav, aside, [role=navigation], [class*="sidebar"], [class*="Sidebar"],' +
                            '[class*="sidenav"], [id*="sidebar"], [data-sidebar],' +
                            '[aria-label*="navigation"], [aria-label*="sidebar"]'
                        )).filter(el => el.querySelectorAll('a[href]').length > 2);

                        const roots = namedRoots.length > 0 ? namedRoots : [document.body];

                        roots.forEach(root => {
                            root.querySelectorAll('a[href]').forEach(a => {
                                const text = a.innerText.trim();
                                if (!text || text.length < 2 || text.length > 120) return;
                                if (seen.has(a.href)) return;
                                seen.add(a.href);

                                let depth = 0, el = a.parentElement;
                                while (el && el !== root) {
                                    if (['UL','OL','LI','DETAILS'].includes(el.tagName)) depth++;
                                    el = el.parentElement;
                                }

                                let section = '';
                                let cur = a.parentElement;
                                while (cur && cur !== root && !section) {
                                    let sib = cur.previousElementSibling;
                                    while (sib && !section) {
                                        const t = sib.innerText?.trim();
                                        if (t && t.length < 60 && /^(H[1-4]|BUTTON|SUMMARY|SPAN|P)$/.test(sib.tagName))
                                            section = t;
                                        sib = sib.previousElementSibling;
                                    }
                                    cur = cur.parentElement;
                                }

                                items.push({ text, href: a.href, depth: Math.min(depth, 5), section });
                            });
                        });

                        if (items.length < 5) {
                            const host = window.location.hostname;
                            const prefix = '/' + (window.location.pathname.split('/').filter(Boolean)[0] || '');
                            document.querySelectorAll('a[href]').forEach(a => {
                                const text = a.innerText.trim();
                                if (!text || text.length < 2 || text.length > 120) return;
                                if (seen.has(a.href)) return;
                                try {
                                    const u = new URL(a.href);
                                    if (u.hostname !== host) return;
                                    if (prefix !== '/' && !u.pathname.startsWith(prefix)) return;
                                    if (u.hash && u.hash !== '#') return;
                                } catch { return; }
                                seen.add(a.href);
                                items.push({ text, href: a.href, depth: 0, section: '' });
                            });
                        }

                        return items.slice(0, 120);
                    }""")
                except Exception as e:
                    thought(f"Link extraction error: {e}", "info")

                thought(f"Found {len(link_data)} doc links to analyse.", "scanning")

                # keyword fallback if Gemini fails
                def _keyword_fallback(links: list[dict], query: str, n: int = 10) -> list[str]:
                    _SKIP = {"pricing", "blog", "changelog", "status", "login",
                             "signup", "sign-up", "twitter", "github", "discord", "careers"}
                    words = set(w.lower() for w in re.split(r'\W+', query) if len(w) > 2)
                    scored = []
                    for x in links:
                        txt  = x["text"].lower()
                        href = x["href"].lower()
                        if any(s in href for s in _SKIP):
                            continue
                        score = sum(1 for w in words if w in txt or w in href)
                        scored.append((score, x["href"]))
                    scored.sort(key=lambda t: -t[0])
                    return [href for _, href in scored[:n] if href]

                def _nav_and_extract(target_url: str) -> tuple[str, str]:
                    page.goto(target_url, wait_until="domcontentloaded", timeout=14000)
                    try:
                        page.wait_for_load_state("networkidle", timeout=1500)
                    except Exception:
                        page.wait_for_timeout(800)
                    return page.evaluate("""() => {
                        document.querySelectorAll('details:not([open])').forEach(d => d.setAttribute('open', ''));
                        const text = document.body.innerText;
                        const headings = Array.from(document.querySelectorAll('h1,h2,h3'))
                            .map(h => h.innerText.trim()).filter(Boolean).join(' | ');
                        return [text, headings];
                    }""")

                def _collect_article_subpages(host: str, docs_prefix: str) -> list[str]:
                    _SKIP_SLUGS = {"blog", "changelog", "status", "login", "signup",
                                   "sign-up", "pricing", "careers", "twitter", "github", "discord"}
                    try:
                        return page.evaluate(
                            """(host, prefix, skipSlugs) => {
                                const seen = new Set();
                                const content = document.querySelector(
                                    'main, article, [role="main"], [class*="content"], [class*="prose"], ' +
                                    '[class*="markdown"], [id="main-content"], [id="content"]'
                                ) || document.body;
                                return Array.from(content.querySelectorAll('a[href]'))
                                    .map(a => a.href)
                                    .filter(href => {
                                        try {
                                            const u = new URL(href);
                                            if (u.hostname !== host) return false;
                                            if (!u.pathname.startsWith(prefix)) return false;
                                            if (u.hash) return false;
                                            const slug = u.pathname.split('/').pop() || '';
                                            if (skipSlugs.includes(slug)) return false;
                                            if (seen.has(href)) return false;
                                            seen.add(href);
                                            return true;
                                        } catch { return false; }
                                    });
                            }""",
                            host,
                            docs_prefix,
                            list(_SKIP_SLUGS),
                        )
                    except Exception:
                        return []

                page_headings_map: dict[str, str] = {}

                def _visit_planned(target_url: str) -> bool:
                    nonlocal docs_visited
                    if target_url in docs_visited or not validate_url(target_url):
                        return False
                    short = target_url.split('/')[-1] or target_url.split('/')[-2] or target_url
                    thought(f"Reading: {short}", "navigating")
                    try:
                        content, headings = _nav_and_extract(target_url)
                        curr_url = page.url
                        docs_visited.add(curr_url)
                        docs_visited.add(target_url)
                        visited_urls.append(curr_url)
                        if _save_planned_page(curr_url, content, relevant_docs_list):
                            page_headings_map[curr_url] = headings
                            return True
                        else:
                            thought("Page empty — skipping.", "info")
                    except Exception as e:
                        thought(f"Nav error: {e}", "info")
                    return False

                planned_urls: list[str] = []

                if link_data:
                    def _fmt_outline(links: list[dict]) -> str:
                        lines = []
                        for i, x in enumerate(links, 1):
                            indent = "  " * min(x.get("depth", 0), 3)
                            section = f"[{x['section']}] " if x.get("section") else ""
                            lines.append(f"{indent}{i}. {section}{x['text']} → {x['href']}")
                        return "\n".join(lines)

                    outline = _fmt_outline(link_data)

                    _HARD_SKIP = {"blog", "changelog", "status", "login", "signup",
                                  "sign-up", "pricing", "careers", "release-notes",
                                  "twitter", "github", "discord", "community", "support"}

                    def _should_skip(href: str) -> bool:
                        parts = href.lower().split("/")
                        return any(s in parts for s in _HARD_SKIP)

                    all_candidate_urls = [
                        x["href"] for x in link_data
                        if not _should_skip(x["href"]) and validate_url(x["href"])
                    ]

                    already_indexed_note = ""
                    if docs_visited:
                        already_indexed_note = (
                            f"ALREADY INDEXED ({len(docs_visited)} pages from a previous run):\n"
                            + "\n".join(f"  {u}" for u in sorted(docs_visited)[:40])
                            + "\n\nFocus on sections NOT covered by these pages. "
                            "EXCLUDE any outline entry whose URL matches an already-indexed page.\n\n"
                        )

                    rank_prompt = (
                        f"USER QUERY: \"{user_query}\"\n"
                        f"DOCS SITE: {page.url}\n\n"
                        f"{already_indexed_note}"
                        f"DOCUMENTATION OUTLINE (indentation = nesting depth):\n{outline[:4000]}\n\n"
                        "Your task:\n"
                        "1. RANK all documentation pages by relevance to the user's query (most relevant first).\n"
                        "2. EXCLUDE pages that have NO reasonable connection to the query — where visiting them "
                        "could not help answer it in any way. Be conservative: only exclude if very confident.\n"
                        "   - ALWAYS exclude: Blog, Changelog, Release Notes, Status, Login, Pricing, Marketing.\n"
                        "   - ALWAYS exclude: pages about unrelated product features (e.g. if user asks about "
                        "hosting a static site, exclude AI/ML features, cron jobs, queues, bot management, etc.).\n"
                        "   - EXCLUDE: any page already in the ALREADY INDEXED list above.\n"
                        "   - KEEP borderline/tangential pages — missing a relevant page is worse than an extra visit.\n"
                        "   - KEEP: Getting Started, Core Concepts, Deployment, CLI, Domains, Configuration, Builds.\n"
                        "3. Reply ONLY with JSON — ranked indices FIRST:\n"
                        '{"ranked":[3,1,7,2,...],"excluded":[15,23,41],"thought":"brief explanation"}'
                    )

                    def _call_rank():
                        res = gemini_client.models.generate_content(
                            model="gemini-2.5-flash",
                            contents=rank_prompt,
                            config=genai_types.GenerateContentConfig(
                                http_options=genai_types.HttpOptions(timeout=30000),
                            ),
                        )
                        plan = _parse_json(res.text)
                        ranked_idxs = plan.get("ranked", [])
                        excluded_idxs = set(plan.get("excluded", []))
                        seen: set[int] = set()
                        ordered: list[str] = []
                        for i in ranked_idxs:
                            if isinstance(i, int) and 1 <= i <= len(link_data) and i not in excluded_idxs:
                                url = link_data[i - 1]["href"]
                                if not _should_skip(url) and validate_url(url) and i not in seen:
                                    seen.add(i)
                                    ordered.append(url)
                        for i, x in enumerate(link_data, 1):
                            if i not in seen and i not in excluded_idxs:
                                url = x["href"]
                                if not _should_skip(url) and validate_url(url):
                                    ordered.append(url)
                        return ordered, plan.get("thought", "")

                    for _attempt in range(2):
                        try:
                            planned_urls, plan_thought = _call_rank()
                            if planned_urls:
                                thought(
                                    f"Plan: {plan_thought[:100]} — {len(planned_urls)} sections queued.",
                                    "scanning",
                                )
                                break
                            if _attempt == 0:
                                thought("Gemini returned no ranking — retrying...", "info")
                            else:
                                thought("No Gemini ranking — using all links in order.", "info")
                                planned_urls = all_candidate_urls
                                thought(f"Visiting all {len(planned_urls)} sections.", "scanning")
                        except Exception as e:
                            if _attempt == 0:
                                thought(f"Rank error, retrying... ({e})", "info")
                            else:
                                thought("Rank failed — visiting all links in order.", "info")
                                planned_urls = all_candidate_urls
                                thought(f"Visiting all {len(planned_urls)} sections.", "scanning")
                else:
                    thought("No doc links found — nothing to visit.", "info")

                _parsed_root = urlparse(page.url)
                _docs_host   = _parsed_root.netloc
                _docs_prefix = "/" + (_parsed_root.path.split("/")[1] if _parsed_root.path.count("/") >= 1 else "")

                _PAGE_CAP = 50
                visit_queue: list[str] = list(dict.fromkeys(planned_urls))[:_PAGE_CAP]
                visit_queue_set: set[str] = set(visit_queue)
                _done = 0

                while visit_queue:
                    target_url = visit_queue.pop(0)
                    if stop_event.is_set():
                        return
                    if not _pause_check():
                        return
                    if target_url in docs_visited:
                        continue
                    if _done >= _PAGE_CAP:
                        break
                    _done += 1
                    _total = min(_done + len(visit_queue), _PAGE_CAP)
                    thought_q.put({"type": "docs_progress", "current": _done, "total": _total})
                    saved = _visit_planned(target_url)
                    if saved:
                        sub_links = _collect_article_subpages(_docs_host, _docs_prefix)
                        for sl in sub_links:
                            if sl not in docs_visited and sl not in visit_queue_set and _done + len(visit_queue) < _PAGE_CAP:
                                visit_queue.append(sl)
                                visit_queue_set.add(sl)

                thought_q.put({"type": "docs_progress", "current": 0, "total": 0})  # clear bar

                # final pass: check if there are important topics we missed
                if relevant_docs_list and not stop_event.is_set() and link_data:
                    unvisited = [
                        x for x in link_data
                        if x["href"] not in docs_visited
                        and validate_url(x["href"])
                    ]
                    if unvisited:
                        visited_summary = "\n".join(
                            f"  • {url.split('/')[-1] or url}: {hdgs[:120]}"
                            for url, hdgs in page_headings_map.items()
                        )
                        remaining_fmt = "\n".join(
                            f"  {i+1}. {x['text']} → {x['href']}"
                            for i, x in enumerate(unvisited[:40])
                        )
                        gap_prompt = (
                            f"USER QUERY: \"{user_query}\"\n\n"
                            f"PAGES ALREADY VISITED (with headings found):\n{visited_summary}\n\n"
                            f"REMAINING UNVISITED SECTIONS:\n{remaining_fmt}\n\n"
                            "Based on the user's query, are there important topics NOT yet covered "
                            "by the visited pages? If so, which remaining sections should we visit next?\n"
                            "If the visited pages fully cover the query, set sufficient=true.\n\n"
                            'Reply ONLY with JSON:\n'
                            '{"sufficient":false,"gaps":["missing topic"],"visit_next":[3,7],"thought":".."}'
                        )
                        try:
                            gap_res = gemini_client.models.generate_content(
                                model="gemini-2.5-flash",
                                contents=gap_prompt,
                                config=genai_types.GenerateContentConfig(
                                    http_options=genai_types.HttpOptions(timeout=20000),
                                ),
                            )
                            gap_plan = _parse_json(gap_res.text)
                            if not gap_plan.get("sufficient", False):
                                gap_idxs = gap_plan.get("visit_next", [])
                                gap_urls = [
                                    unvisited[i - 1]["href"]
                                    for i in gap_idxs
                                    if isinstance(i, int) and 1 <= i <= len(unvisited)
                                ]
                                gaps = gap_plan.get("gaps", [])
                                if gaps:
                                    thought(f"Coverage gaps: {', '.join(gaps[:3])} — visiting {len(gap_urls)} more pages.", "scanning")
                                for gurl in gap_urls[:6]:
                                    if stop_event.is_set():
                                        break
                                    if not _pause_check():
                                        break
                                    _visit_planned(gurl)
                            else:
                                thought("Coverage check: query fully covered.", "found")
                        except Exception as e:
                            thought(f"Coverage check skipped: {e}", "info")

                if stop_event.is_set():
                    return

                thought(
                    f"Docs search complete — {len(relevant_docs_list)} page(s) saved.",
                    "complete" if relevant_docs_list else "info",
                )

                # Auto-finish if we collected relevant docs — no HITL needed
                if relevant_docs_list:
                    return

                # HITL only if we found nothing — let the user point us somewhere
                thought_q.put({
                    "type": "docs_hitl",
                    "prompt": (
                        f"Finished planned sections but found no relevant pages for your query. "
                        f"Type a section heading or paste a URL to search manually, or click 'finish'."
                    ),
                })
                thought_q.put({"type": "auto_pause"})
                pause_event.clear()

                for _extra in range(30):
                    if stop_event.is_set():
                        break
                    if not _pause_check():
                        break

                    hint = None
                    pending = []
                    while not action_q.empty():
                        pending.append(action_q.get_nowait())
                    for act in pending:
                        if act.get("type") == "docs_hint":
                            hint = act.get("value", "").strip()
                        elif act.get("type") == "goto":
                            hint = act.get("url", "")
                        else:
                            action_q.put(act)

                    if not hint:
                        page.wait_for_timeout(300)
                        continue

                    def _extract_after_nav(wait_ms: int = 1500) -> str:
                        try:
                            page.wait_for_load_state("networkidle", timeout=1500)
                        except Exception:
                            page.wait_for_timeout(wait_ms)
                        return page.evaluate("""() => {
                            document.querySelectorAll('details:not([open])').forEach(d => d.setAttribute('open', ''));
                            return document.body.innerText;
                        }""")

                    # User gave a hint — navigate and survey
                    if hint.startswith("http"):
                        if validate_url(hint):
                            thought(f"Navigating to: {hint}", "navigating")
                            try:
                                page.goto(hint, wait_until="domcontentloaded", timeout=12000)
                                curr_url = page.url
                                docs_visited.add(curr_url)
                                visited_urls.append(curr_url)
                                content = _extract_after_nav()
                                if not _save_relevant_page(curr_url, content, relevant_docs_list):
                                    thought("Page not relevant to query.", "info")
                            except Exception as e:
                                thought(f"Nav error: {e}", "info")
                        else:
                            thought("Blocked: invalid URL.", "error")
                    else:
                        thought(f"Looking for section: '{hint}'", "scanning")
                        el, matched = find_element(page, [hint])
                        if el:
                            thought(f"Found '{matched}' — navigating", "clicking")
                            try:
                                href = el.evaluate("el => el.href || el.getAttribute('href') || ''")
                                if href and href.startswith("http"):
                                    page.goto(href, wait_until="domcontentloaded", timeout=12000)
                                else:
                                    el.evaluate("el => el.scrollIntoView({behavior:'smooth',block:'center'})")
                                    page.wait_for_timeout(400)
                                    el.click(force=True)
                                curr_url = page.url
                                docs_visited.add(curr_url)
                                visited_urls.append(curr_url)
                                content = _extract_after_nav(wait_ms=1500)
                                if not _save_relevant_page(curr_url, content, relevant_docs_list):
                                    thought("Page not relevant to query.", "info")
                            except Exception as e:
                                thought(f"Click error: {e}", "info")
                        else:
                            thought(f"Couldn't find '{hint}' on this page.", "info")
                    pause_event.set()  # re-pause after each hint to wait for next input

            # when the user pastes a URL while stuck, we auto-resume instead of waiting for an explicit resume click
            _stuck_pause = False

            def handle_gestures(page):
                nonlocal _stuck_pause
                deferred = []
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
                            # Auto-resume after any manual URL navigation — user
                            # pasting a URL is an implicit "continue from here"
                            if not pause_event.is_set():
                                _stuck_pause = False
                                thought("URL received — resuming agent.", "info")
                                pause_event.set()
                        else:
                            deferred.append(act)
                    except Exception as e:
                        print(f"Gesture error: {e}")
                for act in deferred:
                    action_q.put(act)

            finishing_domain = re.sub(
                r'^www\.', '', urlparse(target_url).hostname or "unknown"
            )

            already_vectorized = _is_domain_complete(EXTRACTS_DIR / finishing_domain)
            if already_vectorized:
                thought("Domain previously indexed — will search for query-specific content.", "info")

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
                                thought("Milestone: Pricing page found — extracting.", "found")
                                page.wait_for_timeout(800)
                                content = full_page_extract(page)
                                save_intel("pricing", curr_url, content)
                                save_extract(finishing_domain, "pricing", curr_url, content)
                                thought(f"Pricing saved ({len(content):,} chars).", "found")
                                objectives["pricing"] = True
                                progress = True

                            if progress:
                                frustration     = 0
                                dead_steps      = 0
                                suspicion_level = 0
                                if objectives["pricing"]:
                                    break  # exit to query wait phase
                                continue

                            dead_steps += 1
                            missing_milestone = "pricing"

                            # Trigger HITL if: 3 consecutive dead steps, OR 4 Gemini API failures total
                            if dead_steps >= 3 or gemini_fail_count >= 4:
                                suspicion_level += 1
                                _page_hint = "their Pricing or Plans page"
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
                                                milestone = "pricing"
                                                _page_hint = "their Pricing or Plans page"
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
                                            milestone = "pricing"
                                            _page_hint = "their Pricing or Plans page"
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

                        # Docs first — pricing is picked up from within docs if it's there
                        if not stop_event.is_set():
                            if not user_query:
                                thought_q.put({"type": "query_prompt"})
                                thought("Waiting for your research question...", "info")
                                while not stop_event.is_set():
                                    page.wait_for_timeout(300)
                                    pending = []
                                    while not action_q.empty():
                                        pending.append(action_q.get_nowait())
                                    found_query = False
                                    for act in pending:
                                        if act.get("type") == "query":
                                            user_query = act.get("text", "").strip()
                                            found_query = True
                                        else:
                                            action_q.put(act)
                                    if found_query:
                                        break
                            if user_query and not stop_event.is_set():
                                navigate_to_docs_landing()
                                thought(f"Searching for: {user_query}", "info")
                                query_guided_docs_search()
                                objectives["docs"] = True

                        # Pricing fallback — only if no pricing extract was saved yet
                        pricing_dir = EXTRACTS_DIR / finishing_domain
                        has_pricing = any(pricing_dir.glob("pricing_*.txt")) if pricing_dir.exists() else False
                        if not has_pricing and not stop_event.is_set():
                            thought("No pricing found in docs — browsing pricing page.", "navigating")
                            objectives["pricing"] = False
                            for step in range(40):
                                if stop_event.is_set() or objectives["pricing"]:
                                    break
                                was_paused = not pause_event.is_set()
                                while not pause_event.is_set():
                                    handle_gestures(page)
                                    if stop_event.is_set():
                                        break
                                    page.wait_for_timeout(100)
                                if stop_event.is_set():
                                    break
                                handle_gestures(page)
                                curr_url   = page.url
                                curr_lower = curr_url.lower()
                                _PRICING_URL     = ["pricing", "plans", "plan", "price"]
                                _PRICING_CONTENT = ["pricing", "plans", "per month", "per seat", "free tier", "enterprise"]
                                try:
                                    _page_title = page.title().lower()
                                    _page_h1    = page.evaluate(
                                        "() => (document.querySelector('h1') || {innerText:''}).innerText"
                                    ).lower()
                                except Exception:
                                    _page_title = _page_h1 = ""
                                if (
                                    any(k in curr_lower for k in _PRICING_URL)
                                    or any(k in _page_title for k in _PRICING_CONTENT)
                                    or any(k in _page_h1 for k in _PRICING_CONTENT)
                                ):
                                    thought("Pricing page found — extracting.", "found")
                                    page.wait_for_timeout(800)
                                    content = full_page_extract(page)
                                    save_intel("pricing", curr_url, content)
                                    save_extract(finishing_domain, "pricing", curr_url, content)
                                    thought(f"Pricing saved ({len(content):,} chars).", "found")
                                    objectives["pricing"] = True
                                    break
                                try:
                                    content  = page.evaluate("() => document.body.innerText")
                                    headings = page.evaluate(
                                        "() => Array.from(document.querySelectorAll('h1,h2,h3,nav a,header a,footer a,[role=navigation] a'))"
                                        ".map(e => e.innerText.trim()).filter(Boolean).join(' | ')"
                                    )
                                    try:
                                        shot = page.screenshot(type="jpeg", quality=60)
                                    except Exception:
                                        shot = None
                                    decision = ask_brain(curr_url, content, headings, shot, {})
                                    goal  = decision.get("goal")
                                    label = decision.get("target", "")
                                    thought(decision.get("thought", ""), "navigating")
                                    if goal in ["CLICK", "HOVER"] and label:
                                        el, matched = find_element(page, [label])
                                        if el:
                                            thought(f"Targeting: {matched}", "clicking")
                                            el.hover(force=True)
                                            if goal == "CLICK":
                                                el.evaluate("el => el.setAttribute('target','_self')")
                                                try:
                                                    el.click(force=True, timeout=3000)
                                                except Exception:
                                                    el.evaluate("el => el.click()")
                                            page.wait_for_timeout(1500)
                                    elif goal == "SCROLL":
                                        page.evaluate("window.scrollBy(0, 600)")
                                        page.wait_for_timeout(500)
                                except Exception as e:
                                    thought(f"Pricing nav error: {e}", "info")
                                page.wait_for_timeout(400)

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
                    elif dtype == "finish":
                        thought_q.put({"text": "Finishing early — wrapping up with pages collected so far.", "state": "complete"})
                        stop_event.set()
                        pause_event.set()
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
