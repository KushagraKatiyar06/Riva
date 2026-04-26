# Riva — Autonomous Competitive Intelligence

Riva lets you paste two URLs: your product and a competitor. Riva then dispatches autonomous browser agents to research both in parallel. It extracts pricing, features, and documentation, vectorizes everything into Cloudflare's knowledge base, and gives you an AI chat interface to interrogate the data. When you're ready, it generates a polished one-pager PDF or a full PowerPoint GTM deck.

---

## What it does

1. **Dual browser agents** — two headless Chrome sessions navigate to your URLs, find pricing pages and docs, and extract all the content. You watch them work in real time with a live video feed and a thought stream.
2. **Human-in-the-loop** — if an agent hits a CAPTCHA or gets stuck, it pauses and lets you intervene. You can also click directly into the browser feed, paste a URL, or skip an agent entirely if the data is already cached.
3. **Vectorized knowledge base** — extracted content is chunked and embedded into Cloudflare Vectorize. The same data is reused across sessions, so re-running on the same domains is instant.
4. **AI chat** — once both agents finish, you can ask anything about the data. The system runs a RAG query against your session's domains and synthesizes an answer with Gemini.
5. **Report generation** — type `html` for a one-pager PDF battlecard or `pptx` for a full GTM strategy slide deck. Both are rendered inline so you can preview before downloading.

---

## Tech stack

| Layer | Technology |
|---|---|
| Frontend | Next.js 15, React 19, Tailwind CSS 4, TypeScript |
| Backend | FastAPI, Python 3.11 |
| Browser automation | Playwright + Chrome CDP |
| Embeddings | Cloudflare Workers AI (BGE base en v1.5) |
| Vector store | Cloudflare Vectorize v2 |
| AI synthesis | Google Gemini 2.5 Flash |
| Reports | python-pptx, Playwright PDF rendering |

---

## Running locally

### Prerequisites

- Python 3.11+
- Node.js 18+
- Google Chrome installed (for the browser agents)
- A Cloudflare account with Vectorize enabled
- A Gemini API key

### 1. Clone and set up Python

```bash
git clone https://github.com/your-username/riva.git
cd riva

python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

pip install -r requirements.txt
playwright install chromium
```

### 2. Set up environment variables

Copy the example file and fill in your keys:

```bash
cp .env.example .env
```

```
GEMINI_API_KEY=your_gemini_api_key
CLOUDFLARE_ACCOUNT_ID=your_cloudflare_account_id
CLOUDFLARE_API_TOKEN=your_cloudflare_api_token
```

You'll also need a Cloudflare Vectorize index named `riva-intel`. Create it in the Cloudflare dashboard or via Wrangler:

```bash
npx wrangler vectorize create riva-intel --dimensions=768 --metric=cosine
```

### 3. Start the backend

```bash
python run.py
# Backend runs at http://localhost:8000
```

### 4. Start the frontend

```bash
cd frontend
npm install
npm run dev
# Frontend runs at http://localhost:3000
```

Open `http://localhost:3000`, paste two URLs, and hit Go.


# Created by Kushagra Katiyar
