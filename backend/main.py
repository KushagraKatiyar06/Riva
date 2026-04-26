from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import asyncio
import threading
import queue as tqueue
import json
import os
import re
import sys
import uuid
import sqlite3
import base64
import hashlib
import shutil
import socket
import subprocess
import time
import requests
import concurrent.futures
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse
import ipaddress
from dotenv import load_dotenv
from google import genai

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
CF_ACCOUNT_ID  = os.getenv("CLOUDFLARE_ACCOUNT_ID")
CF_API_TOKEN   = os.getenv("CLOUDFLARE_API_TOKEN")
CF_INDEX_NAME  = "riva-intel"
CF_EMBED_MODEL = "@cf/baai/bge-base-en-v1.5"
CF_HEADERS = {
    "Authorization": f"Bearer {CF_API_TOKEN}",
    "Content-Type": "application/json",
}

EXTRACTS_DIR = Path(__file__).parent.parent / "testing" / "extracts"
REPORTS_DIR = Path(__file__).parent.parent / "testing" / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

# Import report generation modules from testing/
_TESTING_DIR = str(Path(__file__).parent.parent / "testing")
if _TESTING_DIR not in sys.path:
    sys.path.insert(0, _TESTING_DIR)

try:
    from report import generate_intel, render_html, scrape_brand_assets
    from pptx_report import (
        generate_gtm, new_prs, render_preview_html,
        slide_title, slide_exec_summary, slide_pricing,
        slide_differentiators, slide_gtm, slide_action_items,
    )
    _REPORTS_AVAILABLE = True
except ImportError as _import_err:
    _REPORTS_AVAILABLE = False
    print(f"Warning: report modules not available: {_import_err}")

# Vectorize helpers (inlined from testing/vectorize.py)
_CHUNK_SIZE    = 400
_CHUNK_OVERLAP = 60
_EMBED_BATCH   = 50



def _chunk_text(text: str) -> list[str]:
    chunks, start = [], 0
    while start < len(text):
        chunks.append(text[start:start + _CHUNK_SIZE].strip())
        start += _CHUNK_SIZE - _CHUNK_OVERLAP
    return [c for c in chunks if len(c) > 50]


def _embed_batch(texts: list[str], log_fn=None) -> list[list[float]]:
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/{CF_EMBED_MODEL}"
    for attempt in range(4):
        resp = requests.post(url, headers=CF_HEADERS, json={"text": texts}, timeout=30)
        if resp.status_code == 429:
            wait = 30 * (attempt + 1)  # 30s, 60s, 90s, 120s
            msg  = f"Workers AI rate limit — waiting {wait}s (attempt {attempt+1}/4)..."
            if log_fn:
                log_fn(msg, "error")
            else:
                print(msg)
            time.sleep(wait)
            continue
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success"):
            raise RuntimeError(f"Embedding failed: {data}")
        return data["result"]["data"]
    raise RuntimeError("Embedding failed after 4 retries — daily neuron limit may be reached.")


def _upsert_vectors(vectors: list[dict]) -> int:
    url    = (
        f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}"
        f"/vectorize/v2/indexes/{CF_INDEX_NAME}/upsert"
    )
    ndjson = "\n".join(json.dumps(v) for v in vectors)
    resp   = requests.post(
        url,
        headers={**CF_HEADERS, "Content-Type": "application/x-ndjson"},
        data=ndjson.encode("utf-8"),
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(f"Upsert failed: {data}")
    return data["result"].get("count", len(vectors))


def _vector_id(domain: str, intel_type: str, url: str, idx: int) -> str:
    return hashlib.md5(f"{domain}:{intel_type}:{url}:{idx}".encode()).hexdigest()


def _load_vec_cache(domain_dir: Path) -> set:
    """Load set of URLs already vectorized for this domain."""
    cache_path = domain_dir / ".vectorized_cache.json"
    if cache_path.exists():
        try:
            return set(json.loads(cache_path.read_text(encoding="utf-8")))
        except Exception:
            pass
    return set()


def _save_vec_cache(domain_dir: Path, cache: set):
    cache_path = domain_dir / ".vectorized_cache.json"
    try:
        cache_path.write_text(json.dumps(list(cache)), encoding="utf-8")
    except Exception:
        pass


def _process_file_pipeline(filepath: Path, log_fn=None, cache: set = None) -> int:
    """Chunk → embed → upsert a single extract file. Returns vector count, or -1 if skipped.
    Cache is keyed by URL — same URL is never re-vectorized even if content changed slightly."""
    try:
        raw = filepath.read_text(encoding="utf-8")
    except Exception as e:
        if log_fn:
            log_fn(f"Skip {filepath.name}: {e}", "error")
        return 0

    lines = raw.splitlines()
    meta, past_divider, content_lines = {}, False, []
    for line in lines:
        if not past_divider:
            if line.startswith("URL      :"):
                meta["url"] = line.split(":", 1)[1].strip()
            elif line.startswith("Type     :"):
                meta["type"] = line.split(":", 1)[1].strip()
            elif line.startswith("=" * 10):
                past_divider = True
        else:
            content_lines.append(line)

    url = meta.get("url", "")

    # URL-based cache — same URL is never re-vectorized even if content changed slightly
    if cache is not None and url and url in cache:
        if log_fn:
            log_fn(f"  (already vectorized, skipping)", "info")
        return -1  # sentinel: skipped

    content = "\n".join(content_lines).strip()
    if not content:
        return 0

    domain     = filepath.parent.name
    intel_type = meta.get("type", "unknown")
    chunks     = _chunk_text(content)
    if not chunks:
        return 0

    total = 0
    for batch_start in range(0, len(chunks), _EMBED_BATCH):
        batch = chunks[batch_start:batch_start + _EMBED_BATCH]
        try:
            embeddings = _embed_batch(batch, log_fn=log_fn)
            vectors = [
                {
                    "id":     _vector_id(domain, intel_type, url, batch_start + i),
                    "values": emb,
                    "metadata": {
                        "domain": domain,
                        "type":   intel_type,
                        "url":    url,
                        "chunk":  i,
                        "text":   chunk,
                    },
                }
                for i, (chunk, emb) in enumerate(zip(batch, embeddings))
            ]
            total += _upsert_vectors(vectors)
        except Exception as e:
            if log_fn:
                log_fn(f"Embed error ({filepath.name}): {e}", "error")

    # Add URL to cache on success so future runs skip it
    if total > 0 and cache is not None and url:
        cache.add(url)

    return total


# RAG helpers (inlined from testing/query.py)
def _embed_query(text: str) -> list[float]:
    url = f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}/ai/run/{CF_EMBED_MODEL}"
    for attempt in range(4):
        resp = requests.post(url, headers=CF_HEADERS, json={"text": [text]}, timeout=20)
        if resp.status_code == 429:
            wait = 30 * (attempt + 1)
            print(f"  _embed_query rate limit — waiting {wait}s (attempt {attempt+1}/4)...")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()["result"]["data"][0]
    raise RuntimeError("_embed_query failed after 4 retries.")


def _rag_search(vector: list[float], top_k: int = 15, domains: list[str] | None = None) -> list[dict]:
    url  = (
        f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}"
        f"/vectorize/v2/indexes/{CF_INDEX_NAME}/query"
    )
    # Fetch extra candidates for client-side domain filtering, capped at Vectorize v2 max of 20
    fetch_k = min(20, top_k * 4) if domains else top_k
    resp = requests.post(
        url, headers=CF_HEADERS,
        json={"vector": vector, "topK": fetch_k, "returnMetadata": "all"},
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(f"Query failed: {data}")
    matches = data["result"].get("matches", [])
    if domains:
        domain_set = set(domains)
        matches = [m for m in matches if m.get("metadata", {}).get("domain") in domain_set]
    return matches[:top_k]


def _build_rag_context(matches: list[dict]) -> str:
    grouped: dict[str, list[str]] = {}
    for m in matches:
        meta   = m.get("metadata", {})
        domain = meta.get("domain", "unknown")
        text   = meta.get("text", "")
        grouped.setdefault(domain, []).append(text)
    parts = []
    for domain, chunks in grouped.items():
        parts.append(f"### {domain}")
        parts.extend(chunks)
        parts.append("")
    return "\n".join(parts)


def _rag_answer(question: str, context: str) -> str:
    client = genai.Client(api_key=GEMINI_API_KEY)
    prompt = (
        "You are a competitive intelligence analyst. Use ONLY the context below to answer.\n"
        "Be specific. Where multiple domains are present, highlight differences clearly.\n\n"
        f"---\n{context}\n---\n\nQuestion: {question}\nAnswer:"
    )
    return client.models.generate_content(
        model="gemini-2.5-flash", contents=prompt
    ).text.strip()


# Session registry — coordinates browse agents with the pipeline WS
class _Session:
    def __init__(self, expected: int):
        self.expected   = expected
        self.completed  = 0
        self.domains:   list[str] = []
        self.ready_q    = tqueue.Queue()   # domains ready for vectorization (one per agent)
        self.done_event = threading.Event()
        self._lock      = threading.Lock()

    def mark_complete(self, domain: str):
        with self._lock:
            self.completed += 1
            if domain and domain not in self.domains:
                self.domains.append(domain)
                self.ready_q.put(domain)   # immediately signal vectorize pipeline
            if self.completed >= self.expected:
                self.done_event.set()


_sessions: dict[str, _Session] = {}


# Chrome CDP helpers
def _launch_bare_chrome(target_url: str, debug_port: int, user_data_dir: str):
    """Launch Chrome without Playwright automation flags (bypasses Turnstile detection)."""
    chrome_exe = shutil.which("chrome") or shutil.which("google-chrome")
    if not chrome_exe:
        for path in [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        ]:
            if os.path.exists(path):
                chrome_exe = path
                break
    if not chrome_exe:
        raise RuntimeError("Chrome executable not found")
    return subprocess.Popen([
        chrome_exe,
        f"--remote-debugging-port={debug_port}",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        target_url,
    ])


def _wait_for_debug_port(port: int, timeout: int = 15) -> bool:
    """Wait until Chrome's remote debugging port is accepting connections."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("localhost", port), timeout=1):
                return True
        except OSError:
            time.sleep(0.5)
    return False


# Extract saver
def save_extract(domain: str, intel_type: str, url: str, content: str) -> str:
    """Write extracted page content to testing/extracts/{domain}/{type}_{ts}.txt."""
    folder = EXTRACTS_DIR / domain
    folder.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r'[^a-zA-Z0-9_-]', '_', urlparse(url).path.strip('/'))[:60] or 'index'
    filename = f"{intel_type}_{slug}.txt"
    filepath = folder / filename
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(f"URL      : {url}\n")
        f.write(f"Type     : {intel_type}\n")
        f.write(f"Captured : {datetime.utcnow().isoformat()}\n")
        f.write("=" * 80 + "\n\n")
        f.write(content)
    return str(filepath)


# Database
DB_PATH  = os.path.join(os.path.dirname(__file__), "riva_intel.db")
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

# App
app = FastAPI(title="Riva Strategic Pathfinder")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)


# REST
@app.get("/sessions")
def list_sessions():
    with _db_lock, _db() as conn:
        rows = conn.execute("SELECT * FROM sessions ORDER BY started_at DESC").fetchall()
        return [dict(r) for r in rows]


@app.get("/sessions/{session_id}")
def get_session(session_id: str):
    with _db_lock, _db() as conn:
        session = conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        intel = conn.execute(
            "SELECT * FROM intel WHERE session_id = ? ORDER BY captured_at",
            (session_id,),
        ).fetchall()
        return {"session": dict(session), "intel": [dict(i) for i in intel]}


@app.get("/check-vectorized")
def check_vectorized(domain: str = ""):
    """Return whether a domain already has extracted/vectorized data."""
    if not domain:
        return {"vectorized": False}
    # Strip www. to match the normalized domain used during extraction
    domain = re.sub(r'^www\.', '', domain.lower().strip())
    domain_dir = EXTRACTS_DIR / domain
    if domain_dir.exists() and list(domain_dir.glob("*.txt")):
        return {"vectorized": True}
    return {"vectorized": False}


@app.get("/reports/{filename}")
def serve_report(filename: str):
    """Serve generated HTML/PPTX report files."""
    # Basic path traversal guard
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    path = REPORTS_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Report not found")
    return FileResponse(str(path))


# Browse WebSocket
@app.websocket("/ws/browse")
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
                (db_session_id, target_url, datetime.utcnow().isoformat()),
            )

        def run_browser():
            from playwright.sync_api import sync_playwright

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
                         datetime.utcnow().isoformat()),
                    )

            def finish_session(status: str):
                with _db_lock, _db() as conn:
                    conn.execute(
                        "UPDATE sessions SET status=?, completed_at=?,"
                        " pricing_found=?, docs_found=? WHERE id=?",
                        (status, datetime.utcnow().isoformat(),
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
                    """Scroll element into view; return it if it exists, even if off-screen."""
                    try:
                        el = loc.first
                        # Try scrolling into view — works for footer/off-screen elements
                        try:
                            el.scroll_into_view_if_needed(timeout=500)
                        except Exception:
                            pass
                        if el.is_visible(timeout=500):
                            return el, k
                        # Element exists but still not "visible" (e.g. opacity:0) — try anyway
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

            def ask_brain(page_content: str, current_url: str, headings: str) -> dict:
                if not GEMINI_API_KEY:
                    return {"goal": "ERROR", "thought": "Missing GEMINI_API_KEY"}

                p_text  = "PRICING: COMPLETED" if objectives["pricing"] else "PRICING: NOT_STARTED"
                d_text  = "DOCS: COMPLETED"    if objectives["docs"]    else "DOCS: NOT_STARTED"
                recent  = visited_urls[-5:]

                prompt = (
                    f"Riva Pathfinder Mission — current page: {current_url}\n"
                    f"MILESTONES : {p_text} | {d_text}\n"
                    f"RECENT URLS: {recent}\n"
                    f"PAGE HEADINGS & NAV & FOOTER: {headings[:1200]}\n\n"
                    "RULES:\n"
                    "- Only pursue NOT_STARTED milestones.\n"
                    "- If Pricing is COMPLETED focus entirely on Documentation.\n"
                    "- HOVER nav items like 'Developers' or 'Products' to reveal sub-menus.\n"
                    "- Check footer links — many sites put Docs/Documentation in the footer.\n"
                    "- If a URL appears twice in RECENT URLS, try a different element.\n\n"
                    'Reply ONLY with JSON (no markdown): {"thought": "..", "goal": "CLICK"|"HOVER"|"SURVEY"|"FINISH", "target": ".."}\n\n'
                    f"Page text:\n{page_content[:7000]}"
                )

                def _call():
                    res = gemini_client.models.generate_content(
                        model="gemini-2.5-flash", contents=prompt
                    )
                    txt = res.text.strip()
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
                    )].slice(0, 25);
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

            # Mutable flag: when True, a goto from the user auto-resumes the agent
            _stuck_pause = [False]

            def handle_gestures(page):
                while not action_q.empty():
                    act  = action_q.get_nowait()
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
                            page.goto(
                                t_url, wait_until="domcontentloaded", timeout=10000
                            )
                            # If we were stuck waiting for a URL from the user, auto-resume
                            if _stuck_pause[0]:
                                _stuck_pause[0] = False
                                thought("URL received — resuming agent.", "info")
                                pause_event.set()
                    except Exception as e:
                        print(f"Gesture error: {e}")

            # Normalize to root domain (strip www.)
            finishing_domain = re.sub(
                r'^www\.', '', urlparse(target_url).hostname or "unknown"
            )

            # Persistent profile per role — Cloudflare builds trust from cookies/history
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

            _chrome_proc = None  # bare Chrome subprocess for CDP fallback
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
                            # CDP fallback — launch bare Chrome (no automation flags)
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

                        # Pre-create the extract folder for this domain so it exists
                        # from the moment the browser opens, not on first save
                        _domain_dir = EXTRACTS_DIR / finishing_domain
                        _folder_existed = _domain_dir.exists()
                        _domain_dir.mkdir(parents=True, exist_ok=True)
                        if _folder_existed:
                            existing = len(list(_domain_dir.glob("*.txt")))
                            thought(f"Extract folder already exists: extracts/{finishing_domain}/ ({existing} files)", "info")
                        else:
                            thought(f"Extract folder created: extracts/{finishing_domain}/", "info")

                        # Apply stealth patches if available
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
                        # On CDP attempt, Chrome already opened target_url; only navigate if needed
                        if _attempt == 0 or page.url.rstrip("/") != target_url.rstrip("/"):
                            page.goto(target_url, wait_until="domcontentloaded")
                        visited_urls.append(target_url)

                        frustration     = 0
                        challenge_count = 0   # total challenge detections this attempt
                        challenge_cooldown = 0
                        was_paused      = False

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

                            # After resuming from a pause (e.g. challenge solved),
                            # wait for the page to fully settle before the agent acts
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

                            # Detect bot-protection challenge pages
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

                            if not objectives["pricing"] and any(
                                k in curr_lower for k in ["pricing", "plans", "tier"]
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

                            if not objectives["docs"] and any(
                                k in curr_lower
                                for k in ["docs", "documentation", "api", "guide", "developer"]
                            ):
                                if curr_url != target_url:
                                    thought("Milestone: Docs found — crawling.", "found")
                                    page.wait_for_timeout(800)
                                    crawl_docs(page, curr_url, finishing_domain)
                                    objectives["docs"] = True
                                    progress = True

                            if progress:
                                frustration = 0
                                if objectives["pricing"] and objectives["docs"]:
                                    thought("All milestones complete.", "complete")
                                    break
                                continue

                            try:
                                content  = page.evaluate("() => document.body.innerText")
                                headings = page.evaluate(
                                    "() => Array.from(document.querySelectorAll('h1,h2,h3,nav a,header a,footer a,[role=navigation] a'))"
                                    ".map(e => e.innerText.trim()).filter(Boolean).join(' | ')"
                                )
                                decision = ask_brain(content, page.url, headings)
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
                                        frustration = 0
                                        thought(f"Targeting: {matched}", "clicking")
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
                                    else:
                                        frustration += 1
                                        thought(
                                            f"Element not found (frustration {frustration}/3)", "info"
                                        )
                                        if frustration >= 3:
                                            milestone = (
                                                "docs" if objectives["pricing"] else "pricing"
                                            )
                                            thought(
                                                f"Couldn't find {milestone} after 3 attempts — "
                                                "pausing. Paste the URL in the bar above and "
                                                "I'll take it from there.",
                                                "error",
                                            )
                                            thought_q.put({
                                                "type": "stuck_guidance",
                                                "milestone": milestone,
                                            })
                                            thought_q.put({"type": "auto_pause"})
                                            pause_event.clear()
                                            _stuck_pause[0] = True
                                            frustration = 0
                                        else:
                                            page.evaluate("window.scrollBy(0, 600)")
                                            page.wait_for_timeout(500)

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

                        # Close this attempt's browser before retrying (or on success)
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
                            # Pause event may be set from previous run; ensure agent starts fresh
                            pause_event.set()
                            continue  # go to attempt 1

                        # Normal successful finish
                        finish_session("complete")
                        thought("Scraping complete — returning to homepage.", "complete")
                        thought_q.put({"type": "browse_complete"})

                        # Signal pipeline — triggers immediate vectorization for this domain
                        if session and session in _sessions:
                            _sessions[session].mark_complete(finishing_domain)

                        # Close WS stream
                        thought("Closing browser window.", "info")
                        stop_event.set()
                        break  # don't retry

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

        # -------------------------------------------------------------------
        # Async handlers
        # -------------------------------------------------------------------
        async def drain():
            while True:
                while not thought_q.empty():
                    msg = thought_q.get_nowait()
                    if msg.get("__sentinel__"):
                        # Flush any remaining frames then close the WS so receive() exits
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
                        skip_domain = re.sub(r'^www\.', '', urlparse(target_url).hostname or "unknown")
                        thought_q.put({"text": "Skipping browse — using cached extracts.", "state": "complete"})
                        stop_event.set()
                        pause_event.set()
                        if session and session in _sessions:
                            _sessions[session].mark_complete(skip_domain)
                        thought_q.put({"type": "browse_complete"})
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


# HTML → PDF converter (uses Playwright headless Chromium)
def _html_to_pdf(html_path: Path, log_fn=None) -> Path:
    from playwright.sync_api import sync_playwright
    pdf_path = html_path.with_suffix(".pdf")
    file_url = html_path.resolve().as_uri()
    if log_fn:
        log_fn("  Launching headless Chromium for PDF rendering...", "info")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1400, "height": 900})
            page.goto(file_url, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(1500)  # let fonts/images settle
            page.pdf(
                path=str(pdf_path),
                format="A4",
                print_background=True,
                margin={"top": "12mm", "bottom": "12mm", "left": "12mm", "right": "12mm"},
            )
        finally:
            browser.close()
    if log_fn:
        size_kb = pdf_path.stat().st_size // 1024
        log_fn(f"  PDF ready: {pdf_path.name} ({size_kb} KB)", "info")
    return pdf_path


# Pipeline WebSocket — vectorize → chat → report generation
@app.websocket("/ws/pipeline")
async def pipeline_websocket(
    websocket: WebSocket,
    session:  str,
    expected: int = 1,
):
    await websocket.accept()

    # Always create fresh session so expected count is accurate
    _sessions[session] = _Session(expected)
    sess = _sessions[session]

    out_q:   tqueue.Queue = tqueue.Queue()
    in_q:    tqueue.Queue = tqueue.Queue()
    stop_evt = threading.Event()

    def run_pipeline():
        def log(text: str, state: str = "info"):
            out_q.put({"type": "log", "text": text, "state": state})

        def bot(text: str):
            out_q.put({"type": "chat", "role": "assistant", "text": text})

        vectorized: set[str] = set()

        def _vectorize_domain(domain: str):
            """Vectorize all extract files for a domain, updating vectorized set."""
            if not CF_ACCOUNT_ID or not CF_API_TOKEN:
                log("Cloudflare credentials missing — skipping vectorization.", "error")
                vectorized.add(domain)
                return
            domain_dir = EXTRACTS_DIR / domain
            if not domain_dir.exists():
                log(f"  No extracts directory found for {domain}.", "error")
                vectorized.add(domain)
                return
            files = sorted(domain_dir.rglob("*.txt"))
            file_count = len(files)
            cache   = _load_vec_cache(domain_dir)
            skipped = 0
            errored = 0
            total   = 0
            log(f"  {file_count} extract file(s) found for {domain}", "info")
            for idx, fp in enumerate(files, 1):
                if stop_evt.is_set():
                    log(f"  Stopped early at {idx}/{file_count}.", "error")
                    break
                log(f"  [{domain} {idx}/{file_count}] {fp.name}", "info")
                try:
                    n = _process_file_pipeline(fp, log_fn=log, cache=cache)
                    if n == -1:
                        skipped += 1
                    else:
                        total += n
                except Exception as fp_err:
                    errored += 1
                    log(f"  FAILED [{idx}/{file_count}] {fp.name}: {fp_err}", "error")
                    if "daily neuron limit" in str(fp_err).lower() or "4 retries" in str(fp_err):
                        log("  Daily Workers AI limit reached — stopping. Try again after midnight UTC.", "error")
                        break
            _save_vec_cache(domain_dir, cache)
            parts = [f"{total} new vectors"]
            if skipped:
                parts.append(f"{skipped} unchanged (skipped)")
            if errored:
                parts.append(f"{errored} FAILED")
            log(f"  {domain} complete — {', '.join(parts)}.", "error" if errored else "success")
            vectorized.add(domain)

        if expected == 0:
            # Restore mode — data already in Vectorize, just discover existing domains
            log("Restoring session — checking existing extract data...", "info")
            if EXTRACTS_DIR.exists():
                for d_path in sorted(EXTRACTS_DIR.iterdir()):
                    if d_path.is_dir() and not d_path.name.startswith("."):
                        vectorized.add(d_path.name)
            if vectorized:
                log(f"  Found existing data for: {', '.join(sorted(vectorized))}", "success")
            else:
                log("  No existing extract data found.", "error")
        else:
            log("Pipeline active — will vectorize each domain as agents complete.", "info")
            deadline = time.time() + 600  # 10-min overall cap
            while time.time() < deadline and not stop_evt.is_set():
                try:
                    domain = sess.ready_q.get(timeout=2)
                except tqueue.Empty:
                    if sess.done_event.is_set():
                        break
                    continue
                if domain in vectorized:
                    continue
                log(f"Agent finished for {domain} — starting vectorization.", "success")
                _vectorize_domain(domain)

        all_domains = list(vectorized) or list(sess.domains)
        if not all_domains:
            log("No domain data found.", "error")
            bot("No data was extracted. Please run the browser agents first.")
            out_q.put({"__done__": True})
            return

        names_str = " and ".join(sorted(all_domains))
        if expected == 0:
            bot(
                f"Session restored for **{names_str}**.\n\n"
                "What would you like to do?\n"
                "• Type **html** for a one-pager HTML report\n"
                "• Type **pptx** for a PowerPoint deck\n"
                "• Or ask me any question about the competitive data"
            )
        else:
            bot(
                f"All data for **{names_str}** has been vectorized!\n\n"
                "What would you like to do?\n"
                "• Type **html** for a one-pager HTML report\n"
                "• Type **pptx** for a PowerPoint deck\n"
                "• Or ask me any question about the competitive data"
            )
        out_q.put({"type": "status", "value": "ready"})

        def _gen_html(focus: str | None):
            focus_note = f" (focused on: **{focus}**)" if focus else ""
            bot(f"On it! Generating one-pager{focus_note} — takes about 30 seconds.")
            try:
                log("Step 1/5 — Scraping brand assets (logos, OG images)...", "info")
                images = {}
                for d in all_domains:
                    log(f"  Fetching assets for {d}...", "info")
                    images[d] = scrape_brand_assets(d)
                    found = [k for k, v in images[d].items() if v]
                    log(f"  {d}: found {found or 'no images'}", "info")

                log("Step 2/5 — Querying Vectorize for pricing, features, positioning...", "info")
                if focus:
                    log(f"  Focus area: {focus}", "info")
                for d in all_domains:
                    log(f"  Pulling intel chunks for {d}...", "info")
                intel = generate_intel(all_domains, focus=focus)
                for d in all_domains:
                    tiers = len(intel["domains"].get(d, {}).get("pricing_tiers", []))
                    feats = len(intel["domains"].get(d, {}).get("top_features", []))
                    log(f"  {d}: {tiers} pricing tiers, {feats} features extracted", "info")

                log("Step 3/5 — Rendering HTML layout...", "info")
                html = render_html(intel, images)
                log(f"  HTML generated ({len(html):,} chars)", "info")

                log("Step 4/5 — Saving HTML to disk...", "info")
                slug = "_vs_".join(
                    intel["domains"][d]["display_name"].lower().replace(" ", "-")
                    for d in all_domains
                )
                ts        = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
                html_path = REPORTS_DIR / f"riva_report_{slug}_{ts}.html"
                html_path.write_text(html, encoding="utf-8")
                log(f"  HTML saved: {html_path.name}", "info")

                log("Step 5/5 — Converting to PDF...", "info")
                pdf_path = _html_to_pdf(html_path, log_fn=log)
                log(f"Report ready: {pdf_path.name}", "success")
                out_q.put({
                    "type": "report_ready", "report_type": "pdf",
                    "url": f"/reports/{pdf_path.name}",
                    "preview_url": f"/reports/{html_path.name}",
                    "filename": pdf_path.name,
                })
                bot("One-pager PDF is ready! Click **View Report** in the pipeline bar.")
            except Exception as e:
                log(f"Report error: {e}", "error")
                bot(f"Report generation failed: {e}")

        def _gen_pptx(focus: str | None):
            focus_note = f" (focused on: **{focus}**)" if focus else ""
            bot(f"On it! Building PowerPoint deck{focus_note} — takes about 45 seconds.")
            try:
                log("Step 1/5 — Scraping brand assets (logos, OG images)...", "info")
                images = {}
                for d in all_domains:
                    log(f"  Fetching assets for {d}...", "info")
                    images[d] = scrape_brand_assets(d)
                    found = [k for k, v in images[d].items() if v]
                    log(f"  {d}: found {found or 'no images'}", "info")

                log("Step 2/5 — Querying Vectorize for competitive intel...", "info")
                if focus:
                    log(f"  Focus area: {focus}", "info")
                for d in all_domains:
                    log(f"  Pulling intel chunks for {d}...", "info")
                intel = generate_intel(all_domains, focus=focus)
                for d in all_domains:
                    tiers = len(intel["domains"].get(d, {}).get("pricing_tiers", []))
                    log(f"  {d}: {tiers} pricing tiers structured", "info")

                log("Step 3/5 — Generating GTM strategy with Gemini...", "info")
                log("  Asking Gemini for key messages, objections, action items...", "info")
                gtm = generate_gtm(all_domains, intel, focus=focus)
                log(f"  GTM: {len(gtm.get('key_messages', []))} messages, "
                    f"{len(gtm.get('action_items', []))} action items", "info")

                log("Step 4/5 — Building slides...", "info")
                from pptx_report import _make_theme
                theme = _make_theme(images, all_domains)
                prs_obj = new_prs()
                for label, fn, args in [
                    ("Title",                    slide_title,          (prs_obj, intel, images, theme)),
                    ("Executive Summary",         slide_exec_summary,   (prs_obj, intel, images, theme)),
                    ("Pricing Comparison",        slide_pricing,        (prs_obj, intel, theme, images)),
                    ("Competitive Differentiators", slide_differentiators, (prs_obj, intel, theme, images)),
                    ("GTM Strategy",              slide_gtm,            (prs_obj, intel, gtm, theme, images)),
                    ("Action Items",              slide_action_items,   (prs_obj, gtm, theme, all_domains)),
                ]:
                    log(f"  Slide: {label}", "info")
                    fn(*args)

                log("Step 5/5 — Saving deck and preview to disk...", "info")
                names    = "_vs_".join(
                    intel["domains"][d]["display_name"].lower().replace(" ", "-")
                    for d in all_domains
                )
                ts       = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
                out_path = REPORTS_DIR / f"riva_deck_{names}_{ts}.pptx"
                prs_obj.save(str(out_path))
                log(f"Deck saved: {out_path.name} ({len(prs_obj.slides)} slides)", "success")
                preview_url_val = f"/reports/{out_path.name}"  # fallback: download link
                try:
                    preview_path = REPORTS_DIR / f"riva_deck_{names}_{ts}_preview.html"
                    preview_html = render_preview_html(intel, gtm, images, theme)
                    preview_path.write_text(preview_html, encoding="utf-8")
                    preview_url_val = f"/reports/{preview_path.name}"
                    log(f"Preview HTML saved: {preview_path.name}", "info")
                except Exception as prev_err:
                    log(f"Preview generation failed (non-fatal): {prev_err}", "error")
                out_q.put({
                    "type": "report_ready", "report_type": "pptx",
                    "url": f"/reports/{out_path.name}",
                    "preview_url": preview_url_val,
                    "filename": out_path.name,
                })
                bot("Deck is ready! Click **View Report** to download it.")
            except Exception as e:
                log(f"Deck error: {e}", "error")
                bot(f"Deck generation failed: {e}")

        _GENERAL_WORDS = {"general", "none", "no", "all", "everything", "full", "overview", "skip"}
        pending_report: str | None = None  # 'html' or 'pptx' — waiting for focus reply

        while not stop_evt.is_set():
            try:
                user_text = in_q.get(timeout=1.0)
            except tqueue.Empty:
                continue

            text_lower = user_text.lower().strip()

            # --- Pending focus reply ---
            if pending_report:
                # Any message now is treated as the focus (or "general" to skip)
                if text_lower in _GENERAL_WORDS:
                    focus = None
                else:
                    focus = user_text.strip().rstrip(".,!?")
                rtype = pending_report
                pending_report = None
                if not _REPORTS_AVAILABLE:
                    bot("Report modules unavailable — check server import errors.")
                    continue
                if rtype == "html":
                    _gen_html(focus)
                else:
                    _gen_pptx(focus)
                continue

            # Extract inline focus from any text after the report keyword.
            # Priority: "focus on X" / "about X" patterns first, then any remaining text.
            focus = None
            focus_match = re.search(r'\bfocus(?:ed)?\s+(?:on\s+)?(.+)', text_lower)
            if focus_match:
                focus = focus_match.group(1).strip().rstrip(".,!?")
            if not focus:
                about_match = re.search(
                    r'\b(?:html|pptx?|report|deck|slides?)\b.{0,20}\babout\s+(.+)',
                    text_lower,
                )
                if about_match:
                    focus = about_match.group(1).strip().rstrip(".,!?")
            # Fallback: strip the report keyword(s) — any meaningful remaining text is the focus
            if not focus:
                remaining = re.sub(
                    r'\*{0,2}\b(html|pptx?|powerpoint|report|deck|slides?|one.?pager|presentation)\b\*{0,2}',
                    '', user_text, flags=re.IGNORECASE,
                ).strip().strip('.,!?').strip()
                if len(remaining.split()) >= 2:
                    focus = remaining

            is_html = any(w in text_lower for w in
                          ["html", "report", "one-pager", "one pager", "pager"])
            is_pptx = any(w in text_lower for w in
                          ["pptx", "ppt", "powerpoint", "deck", "slides", "presentation"])

            # --- HTML report ---
            if is_html:
                if not _REPORTS_AVAILABLE:
                    bot("Report modules unavailable — check server import errors.")
                    continue
                if focus:
                    _gen_html(focus)
                else:
                    pending_report = "html"
                    bot(
                        "What should the one-pager focus on?\n\n"
                        "Describe the angle — e.g. *pricing comparison*, *developer features*, "
                        "*enterprise use cases* — or type **general** for a full overview."
                    )

            # --- PowerPoint deck ---
            elif is_pptx:
                if not _REPORTS_AVAILABLE:
                    bot("Report modules unavailable — check server import errors.")
                    continue
                if focus:
                    _gen_pptx(focus)
                else:
                    pending_report = "pptx"
                    bot(
                        "What should the deck focus on?\n\n"
                        "Describe the angle — e.g. *pricing comparison*, *GTM strategy*, "
                        "*enterprise pitch* — or type **general** for a full overview."
                    )

            # --- RAG query ---
            else:
                if not CF_ACCOUNT_ID or not CF_API_TOKEN:
                    bot("Cloudflare credentials missing — can't query the knowledge base.")
                    continue
                try:
                    log(f"RAG query: \"{user_text[:60]}\"", "info")
                    vec     = _embed_query(user_text)
                    matches = _rag_search(vec, top_k=15, domains=all_domains)
                    if matches:
                        domains_hit = list({
                            m.get("metadata", {}).get("domain") for m in matches
                        })
                        log(f"  {len(matches)} chunks from: {', '.join(domains_hit)}", "info")
                        context = _build_rag_context(matches)
                        log("  Synthesizing answer with Gemini...", "info")
                        answer  = _rag_answer(user_text, context)
                        bot(answer)
                    else:
                        bot("No relevant data found. Have the agents finished extracting?")
                except Exception as e:
                    bot(f"Query error: {e}")

        out_q.put({"__done__": True})

    thread = threading.Thread(target=run_pipeline, daemon=True)
    thread.start()

    async def drain_pipeline():
        while True:
            while not out_q.empty():
                msg = out_q.get_nowait()
                if msg.get("__done__"):
                    return
                await websocket.send_text(json.dumps(msg))
            await asyncio.sleep(0.05)

    async def receive_pipeline():
        while True:
            try:
                data = await websocket.receive_json()
                if data.get("type") == "chat":
                    in_q.put(data.get("text", ""))
            except Exception:
                break

    try:
        await asyncio.gather(drain_pipeline(), receive_pipeline())
    except WebSocketDisconnect:
        pass
    finally:
        stop_evt.set()
