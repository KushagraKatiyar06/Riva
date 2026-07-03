# Standard HTTP endpoints: sessions, reports, rate limiting, admin auth,
# and the clear-data/clear-preview pair for resetting all stored data.

import asyncio
import hmac
import json
import re
import shutil
import uuid
import requests
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from .config import (
    CF_ACCOUNT_ID,
    CF_API_TOKEN,
    CF_HEADERS,
    CF_INDEX_NAME,
    CLEAR_PASSWORD,
    DAILY_RUN_LIMIT,
    EXTRACTS_DIR,
    REPORTS_DIR,
)
from .db import _db_lock, _db
from .vectorize import _chunk_text, _is_domain_complete, _clear_domain_complete

router = APIRouter()


@router.get("/")
def root():
    return {"status": "ok", "service": "Riva backend"}


@router.get("/sessions")
def list_sessions():
    with _db_lock, _db() as conn:
        rows = conn.execute("SELECT * FROM sessions ORDER BY started_at DESC").fetchall()
        return [dict(r) for r in rows]


@router.get("/sessions/{session_id}")
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


@router.get("/check-vectorized")
def check_vectorized(domain: str = ""):
    """Check whether a domain has been fully vectorized (atomic completion marker)."""
    if not domain:
        return {"vectorized": False}
    domain = re.sub(r'^www\.', '', domain.lower().strip())
    domain_dir = EXTRACTS_DIR / domain
    return {"vectorized": _is_domain_complete(domain_dir)}


@router.get("/reports/{filename}")
def serve_report(filename: str):
    """Serve a generated report file by name."""
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    path = REPORTS_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Report not found")
    return FileResponse(str(path))


@router.get("/daily-limit")
def daily_limit_endpoint(request: Request):
    client_id = request.headers.get("x-client-id", "").strip()
    if not client_id:
        return {"used": 0, "limit": DAILY_RUN_LIMIT, "remaining": DAILY_RUN_LIMIT}
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _db_lock, _db() as conn:
        used = conn.execute(
            "SELECT COUNT(*) FROM runs WHERE client_id = ? AND date(started_at) = ?",
            (client_id, today),
        ).fetchone()[0]
    return {"used": used, "limit": DAILY_RUN_LIMIT, "remaining": max(0, DAILY_RUN_LIMIT - used)}


@router.get("/admin/verify")
def admin_verify(request: Request):
    """Check whether the provided password grants admin (limit-bypass) access."""
    pw = request.headers.get("x-admin-password", "").strip()
    if pw and hmac.compare_digest(pw, CLEAR_PASSWORD):
        return {"ok": True}
    return {"ok": False}


@router.post("/start-run")
async def start_run(request: Request):
    body      = await request.json()
    riva_url  = body.get("riva_url", "")
    comp_url  = body.get("comp_url", "")
    client_id = request.headers.get("x-client-id", "").strip() or "unknown"
    admin_pw  = request.headers.get("x-admin-password", "").strip()
    is_admin  = bool(admin_pw and hmac.compare_digest(admin_pw, CLEAR_PASSWORD))
    today     = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _db_lock, _db() as conn:
        used = conn.execute(
            "SELECT COUNT(*) FROM runs WHERE client_id = ? AND date(started_at) = ?",
            (client_id, today),
        ).fetchone()[0]
        if not is_admin and used >= DAILY_RUN_LIMIT:
            raise HTTPException(
                status_code=429,
                detail=f"Daily limit of {DAILY_RUN_LIMIT} runs reached. Try again tomorrow.",
            )
        run_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO runs (id, riva_url, comp_url, started_at, client_id) VALUES (?, ?, ?, ?, ?)",
            (run_id, riva_url, comp_url, datetime.now(timezone.utc).isoformat(), client_id),
        )
    return {"allowed": True, "run_id": run_id, "used": used + 1, "limit": DAILY_RUN_LIMIT, "admin": is_admin}


@router.get("/runs")
def list_runs():
    with _db_lock, _db() as conn:
        rows = conn.execute(
            "SELECT * FROM runs ORDER BY started_at DESC LIMIT 50"
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/list-reports")
def list_reports_endpoint():
    files = []
    if REPORTS_DIR.exists():
        for f in sorted(REPORTS_DIR.glob("*"), key=lambda x: x.stat().st_mtime, reverse=True):
            if not f.is_file() or f.name.startswith("."):
                continue
            if f.suffix.lower() != ".pdf":
                continue
            files.append({
                "filename": f.name,
                "size": f.stat().st_size,
                "modified": datetime.fromtimestamp(f.stat().st_mtime).isoformat(),
                "url": f"/reports/{f.name}",
                "type": "pdf",
            })
    return {"files": files}


@router.get("/clear-preview")
def clear_preview():
    """Return a summary of everything that would be wiped by /clear-data."""
    domains = []
    total_vectors = 0
    if EXTRACTS_DIR.exists():
        for domain_dir in sorted(EXTRACTS_DIR.iterdir()):
            if not domain_dir.is_dir():
                continue
            txt_files = list(domain_dir.glob("*.txt"))
            vec_count = 0
            for txt_file in txt_files:
                try:
                    raw = txt_file.read_text(encoding="utf-8")
                    lines = raw.splitlines()
                    past_divider = False
                    content_lines: list[str] = []
                    for line in lines:
                        if not past_divider:
                            if line.startswith("=" * 10):
                                past_divider = True
                        else:
                            content_lines.append(line)
                    content = "\n".join(content_lines).strip()
                    vec_count += len(_chunk_text(content))
                except Exception:
                    pass
            total_vectors += vec_count
            domains.append({"domain": domain_dir.name, "files": len(txt_files), "vectors": vec_count})

    report_files = []
    if REPORTS_DIR.exists():
        for f in sorted(REPORTS_DIR.glob("*"), key=lambda x: x.stat().st_mtime, reverse=True):
            if f.is_file() and not f.name.startswith(".") and f.suffix.lower() == ".pdf":
                report_files.append({
                    "filename": f.name,
                    "size": f.stat().st_size,
                    "type": "pdf",
                })

    with _db_lock, _db() as conn:
        run_count     = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        session_count = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]

    # Live vector count directly from Cloudflare Vectorize
    cf_vectors: int | None = None
    if CF_ACCOUNT_ID and CF_API_TOKEN:
        try:
            info_url = (
                f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}"
                f"/vectorize/v2/indexes/{CF_INDEX_NAME}"
            )
            resp = requests.get(info_url, headers=CF_HEADERS, timeout=10)
            if resp.ok:
                cf_vectors = resp.json().get("result", {}).get("vectors_count")
        except Exception:
            pass

    return {
        "domains":       domains,
        "total_vectors": total_vectors,
        "report_files":  report_files,
        "run_count":     run_count,
        "session_count": session_count,
        "cf_vectors":    cf_vectors,
    }


@router.delete("/clear-data")
async def clear_data_endpoint(request: Request):
    body = await request.json()
    if body.get("password", "") != CLEAR_PASSWORD:
        raise HTTPException(status_code=401, detail="Invalid password")

    deleted_vectors = 0
    deleted_files = 0

    # delete and recreate the Vectorize index for a clean slate
    index_error: str | None = None
    if CF_ACCOUNT_ID and CF_API_TOKEN:
        index_base = (
            f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}"
            f"/vectorize/v2/indexes/{CF_INDEX_NAME}"
        )
        try:
            del_resp = await asyncio.to_thread(
                requests.delete, index_base, headers=CF_HEADERS, timeout=30
            )
            # 404 just means it didn't exist yet, that's fine
            if del_resp.ok or del_resp.status_code == 404:
                # Small pause to let Cloudflare propagate the deletion
                await asyncio.sleep(2)
                # Recreate with same config as original index
                create_url = (
                    f"https://api.cloudflare.com/client/v4/accounts/{CF_ACCOUNT_ID}"
                    f"/vectorize/v2/indexes"
                )
                create_resp = await asyncio.to_thread(
                    requests.post,
                    create_url,
                    headers=CF_HEADERS,
                    json={
                        "name": CF_INDEX_NAME,
                        "config": {"dimensions": 768, "metric": "cosine"},
                    },
                    timeout=30,
                )
                if create_resp.ok:
                    deleted_vectors = -1  # signals "index wiped and recreated"
                else:
                    index_error = f"recreate failed {create_resp.status_code}: {create_resp.text[:200]}"
            else:
                index_error = f"delete failed {del_resp.status_code}: {del_resp.text[:200]}"
        except Exception as e:
            index_error = str(e)

    # 2. Wipe extract directories
    if EXTRACTS_DIR.exists():
        shutil.rmtree(str(EXTRACTS_DIR))
    EXTRACTS_DIR.mkdir(parents=True, exist_ok=True)

    # 3. Delete report files
    if REPORTS_DIR.exists():
        for f in REPORTS_DIR.glob("*"):
            if f.is_file():
                try:
                    f.unlink()
                    deleted_files += 1
                except Exception:
                    pass

    # 4. Clear SQLite tables
    with _db_lock, _db() as conn:
        conn.execute("DELETE FROM intel")
        conn.execute("DELETE FROM sessions")
        conn.execute("DELETE FROM runs")

    index_reset = deleted_vectors == -1
    return {
        "cleared": True,
        "index_reset": index_reset,
        "files_deleted": deleted_files,
        "index_error": index_error,
    }
