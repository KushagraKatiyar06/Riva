"""
testing/query.py
Query the Vectorize index for competitive intelligence using RAG.
"""

import os
import requests
from dotenv import load_dotenv
from google import genai

load_dotenv()

ACCOUNT_ID  = os.getenv("CLOUDFLARE_ACCOUNT_ID")
API_TOKEN   = os.getenv("CLOUDFLARE_API_TOKEN")
GEMINI_KEY  = os.getenv("GEMINI_API_KEY")
INDEX_NAME  = "riva-intel"
EMBED_MODEL = "@cf/baai/bge-base-en-v1.5"

HEADERS = {"Authorization": f"Bearer {API_TOKEN}", "Content-Type": "application/json"}


def embed_query(text: str) -> list[float]:
    url  = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/ai/run/{EMBED_MODEL}"
    resp = requests.post(url, headers=HEADERS, json={"text": [text]}, timeout=20)
    resp.raise_for_status()
    return resp.json()["result"]["data"][0]


def search(vector: list[float], top_k: int = 15) -> list[dict]:
    url  = f"https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/vectorize/v2/indexes/{INDEX_NAME}/query"
    resp = requests.post(
        url, headers=HEADERS,
        json={"vector": vector, "topK": top_k, "returnMetadata": "all"},
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(f"Query failed: {data}")
    return data["result"].get("matches", [])


def build_context(matches: list[dict]) -> str:
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


def answer(question: str, context: str) -> str:
    client = genai.Client(api_key=GEMINI_KEY)
    prompt = f"""You are a competitive intelligence analyst. Use ONLY the context below to answer the question.
Be specific. Where multiple domains are present, highlight the differences clearly.

---
{context}
---

Question: {question}
Answer:"""
    return client.models.generate_content(model="gemini-2.5-flash", contents=prompt).text.strip()


def main():
    missing = [k for k in ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN", "GEMINI_API_KEY") if not os.getenv(k)]
    if missing:
        print(f"❌  Missing env vars: {', '.join(missing)}")
        return

    print("\nRiva Intelligence Query")
    print("=" * 45)
    print("Ask anything about the sites you've analysed.")
    print("Type 'quit' to exit.\n")

    while True:
        try:
            question = input("Question: ").strip()
        except (EOFError, KeyboardInterrupt):
            break

        if not question or question.lower() in ("quit", "exit", "q"):
            break

        try:
            print("  Searching...", end=" ", flush=True)
            vector  = embed_query(question)
            matches = search(vector, top_k=15)

            if not matches:
                print("no results. Have you run vectorize.py yet?")
                continue

            context = build_context(matches)
            print("analysing...\n")
            print(answer(question, context))
            print("\n" + "-" * 45 + "\n")

        except Exception as e:
            print(f"\n  ❌ {e}\n")


if __name__ == "__main__":
    main()
