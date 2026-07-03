# Standalone script to (re)vectorize all extract files. Useful if you want to
# rebuild the index without going through the UI. Reads from testing/extracts/.

import os
import json
import hashlib
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

ACCOUNT_ID    = os.getenv("CLOUDFLARE_ACCOUNT_ID")
API_TOKEN     = os.getenv("CLOUDFLARE_API_TOKEN")
INDEX_NAME    = "riva-intel"
EMBED_MODEL   = "@cf/baai/bge-base-en-v1.5"
CHUNK_SIZE    = 400
CHUNK_OVERLAP = 60
EMBED_BATCH   = 50

HEADERS = {"Authorization": f"Bearer {API_TOKEN}", "Content-Type": "application/json"}
EXTRACTS_DIR = Path(__file__).parent / "extracts"


def chunk_text(text: str) -> list[str]:
    chunks, start = [], 0
    while start < len(text):
        chunks.append(text[start : start + CHUNK_SIZE].strip())
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return [c for c in chunks if len(c) > 50]


def embed_batch(texts: list[str]) -> list[list[float]]:
    url  = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/ai/run/{EMBED_MODEL}"
    resp = requests.post(url, headers=HEADERS, json={"text": texts}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(f"Embedding failed: {data}")
    return data["result"]["data"]


def upsert_vectors(vectors: list[dict]) -> int:
    url    = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/vectorize/v2/indexes/{INDEX_NAME}/upsert"
    ndjson = "\n".join(json.dumps(v) for v in vectors)
    resp   = requests.post(
        url,
        headers={**HEADERS, "Content-Type": "application/x-ndjson"},
        data=ndjson.encode("utf-8"),
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(f"Upsert failed: {data}")
    return data["result"].get("count", len(vectors))


def parse_extract(filepath: Path) -> dict | None:
    try:
        raw   = filepath.read_text(encoding="utf-8")
        lines = raw.splitlines()
        meta  = {}
        content_lines = []
        past_divider  = False
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
        content = "\n".join(content_lines).strip()
        if not content:
            return None
        meta["content"] = content
        meta["domain"]  = filepath.parent.name
        return meta
    except Exception as e:
        print(f"    Skip {filepath.name}: {e}")
        return None


def vector_id(domain: str, intel_type: str, url: str, idx: int) -> str:
    return hashlib.md5(f"{domain}:{intel_type}:{url}:{idx}".encode()).hexdigest()


def process_file(filepath: Path) -> int:
    parsed = parse_extract(filepath)
    if not parsed:
        return 0

    domain     = parsed["domain"]
    intel_type = parsed.get("type", "unknown")
    url        = parsed.get("url", "")
    chunks     = chunk_text(parsed["content"])

    if not chunks:
        return 0

    print(f"    {len(chunks)} chunks", end="", flush=True)

    total = 0
    for batch_start in range(0, len(chunks), EMBED_BATCH):
        batch      = chunks[batch_start : batch_start + EMBED_BATCH]
        embeddings = embed_batch(batch)

        vectors = [
            {
                "id":     vector_id(domain, intel_type, url, batch_start + i),
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
        total += upsert_vectors(vectors)

    print(f" → {total} upserted")
    return total


def main():
    if not ACCOUNT_ID or not API_TOKEN:
        print("❌  Missing CLOUDFLARE_ACCOUNT_ID or CLOUDFLARE_API_TOKEN in .env")
        return

    txt_files = sorted(EXTRACTS_DIR.rglob("*.txt"))
    if not txt_files:
        print("❌  No extract files found in testing/extracts/ — run the browser agent first.")
        return

    domains = {f.parent.name for f in txt_files}
    print(f"Found {len(txt_files)} files across {len(domains)} domain(s): {', '.join(sorted(domains))}\n")

    total = 0
    for filepath in txt_files:
        print(f"  [{filepath.parent.name}] {filepath.name}")
        try:
            total += process_file(filepath)
        except Exception as e:
            print(f"    ❌ {e}")

    print(f"\n✅  Done — {total} total vectors in '{INDEX_NAME}'")


if __name__ == "__main__":
    main()
