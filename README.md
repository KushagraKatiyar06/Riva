# Riva: Autonomous Competitive Intelligence at the Edge

Riva is an autonomous competitive intelligence platform that uses AI agents to research competitor websites in real-time. It provides a live video feed of the agent's browser session, a stream of its reasoning, and a Human-In-The-Loop (HITL) system for manual intervention (e.g., CAPTCHAs).

The end goal is to automatically generate structured battlecards and GTM (Go-To-Market) strategy slide decks by comparing your product against competitors.

## 🚀 Project Overview

- **Frontend**: Next.js (React 19, Tailwind CSS 4, TypeScript) dashboard.
- **Backend**: Cloudflare Workers + Durable Objects for stateful, long-lived agent sessions.
- **Browser Engine**: Cloudflare Browser Rendering (managed Chrome) controlled via Puppeteer and CDP.
- **Intelligence**: Hybrid RAG (Vectorize + BM25) for semantic and keyword-based search across scraped data.

## 🛠️ Getting Started

To run the current prototype, you need to start both the Cloudflare Worker (backend) and the Next.js application (frontend).

### 1. Backend (Cloudflare Worker)
The backend requires Cloudflare's infrastructure to run the managed browser.

```bash
cd backend/worker
npm install
npx wrangler login  # Authenticate with your Cloudflare account
npm run dev         # Runs 'wrangler dev --remote'
```
*Note: `--remote` is mandatory because the Browser Rendering binding only exists in Cloudflare's production environment.*

### 2. Frontend (Next.js)
The frontend connects to the local wrangler proxy by default.

```bash
cd frontend
npm install
npm run dev
```

Visit [http://localhost:3000](http://localhost:3000) to use the application.

## 🏗️ Architecture

- **`frontend/`**: The Next.js dashboard and landing page.
- **`backend/worker/`**: The primary backend. Uses **Durable Objects** for session persistence and **WebSocket Hibernation** for efficient real-time communication.
- **`backend/main.py`**: The original Python MVP (FastAPI + Playwright), now preserved for reference.
- **`testing/`**: Experimental scripts for scraping and agent testing.

## 🗺️ Roadmap

1. **Phase 3**: Persistent memory using SQLite within Durable Objects.
2. **Phase 4**: Hybrid RAG integration with Cloudflare Vectorize.
3. **Phase 5**: Dual-agent loops for automated gap detection between sites.
4. **Phase 6**: Automated PowerPoint and JSON battlecard generation.
