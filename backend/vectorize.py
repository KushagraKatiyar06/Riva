# Handles all vectorization work: chunking text, getting embeddings from
# Cloudflare Workers AI, upserting to the Vectorize index, and tracking
# which domains have been fully indexed via a marker file.

import json
import time
import hashlib
import requests
from pathlib import Path

from .config import CF_ACCOUNT_ID, CF_HEADERS, CF_EMBED_MODEL, CF_INDEX_NAME

_CHUNK_SIZE    = 400
_CHUNK_OVERLAP = 60
_EMBED_BATCH   = 100  # CF AI accepts up to 100 texts per batch


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


_COMPLETE_MARKER = ".domain_complete"


def _is_domain_complete(domain_dir: Path) -> bool:
    """Return True only if this domain was fully vectorized in a previous run."""
    return (domain_dir / _COMPLETE_MARKER).exists()


def _mark_domain_complete(domain_dir: Path):
    """Write the completion marker — called only when ALL files vectorized with zero errors."""
    try:
        (domain_dir / _COMPLETE_MARKER).touch()
    except Exception:
        pass


def _clear_domain_complete(domain_dir: Path):
    """Remove completion marker (called when extracts are wiped or re-scraped)."""
    marker = domain_dir / _COMPLETE_MARKER
    if marker.exists():
        try:
            marker.unlink()
        except Exception:
            pass


def _read_file_url(filepath: Path) -> str:
    """Read just the URL line from an extract file header — fast, no full parse needed."""
    try:
        with filepath.open(encoding="utf-8") as f:
            for line in f:
                if line.startswith("URL      :"):
                    return line.split(":", 1)[1].strip()
                if line.startswith("=" * 10):
                    break
    except Exception:
        pass
    return ""


def _load_vec_cache(domain_dir: Path) -> set:
    """Load the set of URLs already vectorized for this domain."""
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
    """Chunk, embed, and upsert a single extract file. Returns the number of vectors upserted,
    or -1 if the URL was already in the cache and skipped."""
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

    if total > 0 and cache is not None and url:
        cache.add(url)

    return total
