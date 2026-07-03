# WebSocket handler for the vectorization pipeline. Waits for browser agents
# to finish, vectorizes their output domain by domain, then generates the PDF
# report once the user submits a focus prompt.

import asyncio
import json
import re
import sys
import threading
import queue as tqueue
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .config import CF_ACCOUNT_ID, CF_API_TOKEN, EXTRACTS_DIR, REPORTS_DIR
from .db import _db_lock, _db
from .vectorize import (
    _process_file_pipeline, _load_vec_cache, _save_vec_cache,
    _read_file_url, _is_domain_complete, _mark_domain_complete,
)
from .browser import _sessions, _Session

# Import report generation modules from testing/
_TESTING_DIR = str(Path(__file__).parent.parent / "testing")
if _TESTING_DIR not in sys.path:
    sys.path.insert(0, _TESTING_DIR)

try:
    from report import generate_intel, render_html, scrape_brand_assets
    _REPORTS_AVAILABLE = True
except ImportError as _import_err:
    _REPORTS_AVAILABLE = False
    print(f"Warning: report modules not available: {_import_err}")

router = APIRouter()


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


@router.websocket("/ws/pipeline")
async def pipeline_websocket(
    websocket: WebSocket,
    session:  str,
    expected: int = 1,
):
    await websocket.accept()

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
            if not CF_ACCOUNT_ID or not CF_API_TOKEN:
                log("Cloudflare credentials missing — skipping vectorization.", "error")
                vectorized.add(domain)
                return
            domain_dir = EXTRACTS_DIR / domain
            if not domain_dir.exists():
                log(f"  No extracts directory found for {domain}.", "error")
                vectorized.add(domain)
                return

            if _is_domain_complete(domain_dir):
                log(f"  {domain} already fully vectorized — skipping.", "success")
                vectorized.add(domain)
                return

            all_files  = sorted(domain_dir.rglob("*.txt"))
            file_count = len(all_files)
            cache      = _load_vec_cache(domain_dir)
            log(f"  {file_count} extract file(s) found for {domain}", "info")

            # fast pre-filter: skip files whose URL is already in the cache
            to_process = []
            skipped    = 0
            for fp in all_files:
                url = _read_file_url(fp)
                if url and url in cache:
                    skipped += 1
                else:
                    to_process.append((fp, url))

            if skipped:
                log(f"  {skipped} file(s) already vectorized — skipping.", "info")

            total   = 0
            errored = 0
            hit_limit = False
            cache_lock = threading.Lock()

            def _process_one(fp, url, idx):
                log(f"  [{domain} {idx}/{file_count}] {fp.name}", "info")
                try:
                    # cache=None: we pre-filtered above, update cache ourselves below
                    n = _process_file_pipeline(fp, log_fn=log, cache=None)
                    return (url, max(n, 0))
                except Exception as fp_err:
                    log(f"  FAILED [{idx}/{file_count}] {fp.name}: {fp_err}", "error")
                    raise

            with ThreadPoolExecutor(max_workers=3) as ex:
                future_map = {
                    ex.submit(_process_one, fp, url, skipped + i): (fp, url)
                    for i, (fp, url) in enumerate(to_process, 1)
                }
                for fut in future_map:
                    if stop_evt.is_set():
                        log(f"  Stopped early.", "error")
                        break
                    try:
                        url, n = fut.result()
                        with cache_lock:
                            total += n
                            if url and n > 0:
                                cache.add(url)
                    except Exception as fp_err:
                        with cache_lock:
                            errored += 1
                        err_str = str(fp_err)
                        if "daily neuron limit" in err_str.lower() or "4 retries" in err_str:
                            hit_limit = True
                            break

            if hit_limit:
                log("  Daily Workers AI limit reached — stopping. Try again after midnight UTC.", "error")

            _save_vec_cache(domain_dir, cache)

            # only mark complete if every file succeeded - partial success means we should re-run
            if not errored and not hit_limit and not stop_evt.is_set():
                _mark_domain_complete(domain_dir)

            parts = [f"{total} new vectors"]
            if skipped:
                parts.append(f"{skipped} unchanged (skipped)")
            if errored:
                parts.append(f"{errored} FAILED")
            log(f"  {domain} complete — {', '.join(parts)}.", "error" if errored else "success")
            vectorized.add(domain)

        if getattr(sess, "cancelled", False):
            out_q.put({"__done__": True})
            return

        if expected == 0:
            # restore mode: expected=0 means we're reconnecting to an existing session
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
            out_q.put({"type": "status", "value": "vectorizing"})
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

        # wait for Cloudflare to propagate new vectors before the user can query
        if expected > 0 and vectorized:
            log("Waiting for search index to propagate...", "info")
            time.sleep(8)

        out_q.put({"type": "status", "value": "ready"})

        def _gen_pdf(focus: str | None):
            try:
                focus_note = f" focused on {focus}" if focus else ""
                log(f"Generating report{focus_note}...", "info")

                log("Step 1/5 — Scraping brand assets...", "info")
                images = {}
                with ThreadPoolExecutor(max_workers=len(all_domains)) as ex:
                    fut_map = {ex.submit(scrape_brand_assets, d): d for d in all_domains}
                    for fut in fut_map:
                        d = fut_map[fut]
                        images[d] = fut.result()
                        found = [k for k, v in images[d].items() if v]
                        log(f"  {d}: {found or 'no images'}", "info")

                log("Step 2/5 — Querying knowledge base...", "info")
                if focus:
                    log(f"  Focus: {focus}", "info")
                intel = generate_intel(all_domains, focus=focus)
                for d in all_domains:
                    tiers = len(intel["domains"].get(d, {}).get("pricing_tiers", []))
                    feats = len(intel["domains"].get(d, {}).get("top_features", []))
                    log(f"  {d}: {tiers} pricing tiers, {feats} features", "info")

                log("Step 3/5 — Rendering HTML...", "info")
                html = render_html(intel, images, focus=focus)

                log("Step 4/5 — Saving HTML...", "info")
                def _slug(d: str) -> str:
                    name = intel["domains"][d].get("display_name", "")
                    s = re.sub(r'[^a-z0-9-]', '', name.lower().replace(" ", "-"))
                    return s if s and s != "short-brand-name" else re.sub(r'[^a-z0-9-]', '', d.lower().replace(".", "-"))
                slug      = "_vs_".join(_slug(d) for d in all_domains)
                ts        = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
                html_path = REPORTS_DIR / f"riva_report_{slug}_{ts}.html"
                html_path.write_text(html, encoding="utf-8")

                log("Step 5/5 — Converting to PDF...", "info")
                pdf_path = _html_to_pdf(html_path, log_fn=log)
                log(f"Report ready: {pdf_path.name}", "success")
                out_q.put({
                    "type": "report_ready", "report_type": "pdf",
                    "url": f"/reports/{pdf_path.name}",
                    "preview_url": f"/reports/{html_path.name}",
                    "filename": pdf_path.name,
                })
            except Exception as e:
                log(f"Report error: {e}", "error")

        # Wait for user to submit their focus, then generate PDF once
        while not stop_evt.is_set():
            try:
                msg = in_q.get(timeout=1.0)
            except tqueue.Empty:
                continue
            if isinstance(msg, dict) and msg.get("type") == "focus":
                focus_text = msg.get("text", "").strip() or None
                if not _REPORTS_AVAILABLE:
                    log("Report modules unavailable — check server import errors.", "error")
                else:
                    _gen_pdf(focus_text)
                break  # one report per session

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
                if data.get("type") == "focus":
                    in_q.put({"type": "focus", "text": data.get("text", "")})
            except Exception:
                break

    try:
        await asyncio.gather(drain_pipeline(), receive_pipeline())
    except WebSocketDisconnect:
        pass
    finally:
        stop_evt.set()
        _sessions.pop(session, None)
