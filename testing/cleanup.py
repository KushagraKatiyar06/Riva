"""
testing/cleanup.py
Clean up local extracts and/or Cloudflare Vectorize vectors.

Usage:
    python testing/cleanup.py
"""

import os
import json
import shutil
import hashlib
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID")
API_TOKEN  = os.getenv("CLOUDFLARE_API_TOKEN")
INDEX_NAME = "riva-intel"
DIMENSIONS = 768
METRIC     = "cosine"
CHUNK_SIZE = 400
CHUNK_OVERLAP = 60
DELETE_BATCH  = 500   # max IDs per Vectorize delete call

HEADERS      = {"Authorization": f"Bearer {API_TOKEN}", "Content-Type": "application/json"}
EXTRACTS_DIR = Path(__file__).parent / "extracts"


# ---------------------------------------------------------------------------
# Helpers — same ID logic as vectorize.py so IDs are consistent
# ---------------------------------------------------------------------------
def chunk_text(text: str) -> list[str]:
    chunks, start = [], 0
    while start < len(text):
        chunks.append(text[start : start + CHUNK_SIZE].strip())
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return [c for c in chunks if len(c) > 50]


def vector_id(domain: str, intel_type: str, url: str, idx: int) -> str:
    return hashlib.md5(f"{domain}:{intel_type}:{url}:{idx}".encode()).hexdigest()


def ids_for_file(filepath: Path) -> list[str]:
    """Recompute all vector IDs that were generated from a given extract file."""
    try:
        raw   = filepath.read_text(encoding="utf-8")
        lines = raw.splitlines()
        meta, past_divider, content_lines = {}, False, []
        for line in lines:
            if not past_divider:
                if line.startswith("URL      :"):
                    meta["url"]  = line.split(":", 1)[1].strip()
                elif line.startswith("Type     :"):
                    meta["type"] = line.split(":", 1)[1].strip()
                elif line.startswith("=" * 10):
                    past_divider = True
            else:
                content_lines.append(line)
        content = "\n".join(content_lines).strip()
        if not content:
            return []
        domain     = filepath.parent.name
        intel_type = meta.get("type", "unknown")
        url        = meta.get("url", "")
        chunks     = chunk_text(content)
        return [vector_id(domain, intel_type, url, i) for i in range(len(chunks))]
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Cloudflare Vectorize API
# ---------------------------------------------------------------------------
def cf_delete_ids(ids: list[str]) -> int:
    """Delete vectors by ID in batches. Returns total deleted."""
    total = 0
    for i in range(0, len(ids), DELETE_BATCH):
        batch = ids[i : i + DELETE_BATCH]
        url   = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/vectorize/v2/indexes/{INDEX_NAME}/delete-by-ids"
        resp  = requests.post(url, headers=HEADERS, json={"ids": batch}, timeout=30)
        resp.raise_for_status()
        data  = resp.json()
        if not data.get("success"):
            raise RuntimeError(f"Delete failed: {data}")
        total += data["result"].get("count", len(batch))
    return total


def cf_delete_index() -> bool:
    url  = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/vectorize/v2/indexes/{INDEX_NAME}"
    resp = requests.delete(url, headers=HEADERS, timeout=20)
    return resp.status_code in (200, 204)


def cf_create_index() -> bool:
    url  = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/vectorize/v2/indexes"
    body = {"name": INDEX_NAME, "config": {"dimensions": DIMENSIONS, "metric": METRIC}}
    resp = requests.post(url, headers=HEADERS, json=body, timeout=20)
    if not resp.json().get("success", False):
        return False
    # Enable metadata indexing for 'domain' so server-side filtering works
    cf_enable_domain_metadata_index()
    return True


def cf_enable_domain_metadata_index() -> bool:
    """Enable metadata indexing for the 'domain' field (required for server-side filtering)."""
    url  = (
        f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}"
        f"/vectorize/v2/indexes/{INDEX_NAME}/metadata-index/create"
    )
    resp = requests.post(url, headers=HEADERS, json={"propertyName": "domain", "indexType": "string"}, timeout=20)
    data = resp.json()
    return data.get("success", False)


def cf_index_exists() -> bool:
    url  = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/vectorize/v2/indexes/{INDEX_NAME}"
    resp = requests.get(url, headers=HEADERS, timeout=10)
    return resp.status_code == 200


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------
def list_domains() -> list[str]:
    if not EXTRACTS_DIR.exists():
        return []
    return sorted(d.name for d in EXTRACTS_DIR.iterdir() if d.is_dir())


def clean_extracts_all():
    if not EXTRACTS_DIR.exists():
        print("  Nothing to delete — extracts directory doesn't exist.")
        return
    shutil.rmtree(EXTRACTS_DIR)
    EXTRACTS_DIR.mkdir()
    print("  ✅ All local extracts deleted.")


def clean_extracts_domain(domain: str):
    domain_dir = EXTRACTS_DIR / domain
    if not domain_dir.exists():
        print(f"  No extracts found for '{domain}'.")
        return
    shutil.rmtree(domain_dir)
    print(f"  ✅ Local extracts for '{domain}' deleted.")


def clean_vectors_all():
    print("  Deleting Vectorize index...", end=" ", flush=True)
    if cf_delete_index():
        print("deleted.")
    else:
        print("index may not exist, continuing.")
    print("  Recreating index...", end=" ", flush=True)
    if cf_create_index():
        print("✅ done.")
    else:
        print("❌ failed — recreate manually with:")
        print(f"     npx wrangler vectorize create {INDEX_NAME} --dimensions={DIMENSIONS} --metric={METRIC}")


def clean_vectors_domain(domain: str):
    files = list((EXTRACTS_DIR / domain).rglob("*.txt")) if (EXTRACTS_DIR / domain).exists() else []
    if not files:
        print(f"  No extract files for '{domain}' — can't compute vector IDs.")
        return
    ids = []
    for f in files:
        ids.extend(ids_for_file(f))
    if not ids:
        print(f"  No vector IDs found for '{domain}'.")
        return
    print(f"  Deleting {len(ids)} vectors for '{domain}'...", end=" ", flush=True)
    deleted = cf_delete_ids(ids)
    print(f"✅ {deleted} deleted.")


# ---------------------------------------------------------------------------
# Menu
# ---------------------------------------------------------------------------
def confirm(prompt: str) -> bool:
    return input(f"{prompt} [y/N]: ").strip().lower() == "y"


def main():
    if not ACCOUNT_ID or not API_TOKEN:
        print("❌  Missing CLOUDFLARE_ACCOUNT_ID or CLOUDFLARE_API_TOKEN in .env")
        return

    domains = list_domains()

    print("\nRiva Cleanup")
    print("=" * 40)
    print("1  Clean everything  (local extracts + Vectorize index)")
    print("2  Clean local extracts only")
    print("3  Clean Vectorize vectors only  (wipe + recreate index)")
    print("4  Clean by domain")
    print("5  Enable domain metadata indexing  (fixes empty Supabase/secondary domain in reports)")
    print("0  Exit\n")

    choice = input("Choice: ").strip()

    if choice == "1":
        if confirm("  ⚠️  Delete ALL local extracts and ALL Vectorize vectors?"):
            clean_extracts_all()
            clean_vectors_all()

    elif choice == "2":
        if confirm("  Delete all local extract files?"):
            clean_extracts_all()

    elif choice == "3":
        if confirm("  ⚠️  Wipe and recreate the entire Vectorize index?"):
            clean_vectors_all()

    elif choice == "5":
        print("  Enabling metadata indexing for 'domain' field...", end=" ", flush=True)
        if cf_enable_domain_metadata_index():
            print("✅ done.")
        else:
            print("❌ failed (may already be enabled, or check your API token permissions).")

    elif choice == "4":
        if not domains:
            print("  No domains found in testing/extracts/")
            return
        print("\n  Available domains:")
        for i, d in enumerate(domains, 1):
            files = list((EXTRACTS_DIR / d).rglob("*.txt"))
            print(f"    {i}. {d}  ({len(files)} file(s))")
        raw = input("\n  Enter domain number (or name): ").strip()
        domain = domains[int(raw) - 1] if raw.isdigit() and 1 <= int(raw) <= len(domains) else raw
        if domain not in domains:
            print(f"  Domain '{domain}' not found.")
            return
        print(f"\n  What to clean for '{domain}'?")
        print("  a  Both local files and Vectorize vectors")
        print("  b  Local files only")
        print("  c  Vectorize vectors only")
        sub = input("  Choice: ").strip().lower()
        if sub == "a" and confirm(f"  Delete local + vectors for '{domain}'?"):
            clean_extracts_domain(domain)
            clean_vectors_domain(domain)
        elif sub == "b" and confirm(f"  Delete local extracts for '{domain}'?"):
            clean_extracts_domain(domain)
        elif sub == "c" and confirm(f"  Delete Vectorize vectors for '{domain}'?"):
            clean_vectors_domain(domain)

    elif choice == "0":
        return
    else:
        print("  Invalid choice.")


if __name__ == "__main__":
    main()
