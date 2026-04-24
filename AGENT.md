# Riva Agentic AI: Implementation Blueprint
**Tagline:** Autonomous Competitive Intelligence at the Edge.

## 1. Core Value Proposition
Riva replaces manual GTM research by using autonomous agents to "travel" through documentation and pricing pages, synthesizing real-time battlecards and marketing strategies.

## 2. The MVP Functionality
* **Dual-URL Input:** Next.js dashboard for user and competitor URLs. Upload links to documentation of your product, and the competitor. However you can also upload pdfs of the doucmentation, however many numbers of files which skips the browser agent loop.
* **Autonomous Research Loop:** `browser-use` handles navigation, calculators, and sub-pages.
* **HITL Snippet:** Live video feed of the browser session for CAPTCHA/Login intervention. Watching the agents travel through pages with log feeds updating its thought process with web sockets
* **Semantic Synthesis:** Generates GTM Battlecards and Marketing Playbooks. Competitve Analysis, how to approach marketing your product over the competitor. Uses rags and vectors to simplify propmts.
* **Persistent Memory:** Isolated databases per crawl to track changes
    over time.
* **GTM Slide Deck Generation:** Slide Deck Automation: Conditional generation of a PowerPoint GTM strategy. The agent "offers" to build the deck once research is finalized, ensuring the synthesis is ready for immediate stakeholder presentation.

## 3. Technical Stack (Prioritize Cloudflare Stack 2026)
| Component | Technology | 2026 Spec / Feature |
| :--- | :--- | :--- |
| **Frontend** | Next.js (React) | Cloudflare Pages + WebSockets for streaming. |
| **Orchestration** | Agents SDK | `AIChatAgent` for long-lived workflows. |
| **Logic Layer** | Python (FastAPI) | Cloudflare Sandbox (Ubuntu container). |
| **Browser Engine** | Browser Run | Managed Chrome via CDP. |
| **State & Memory** | Durable Objects | SQLite Facets (10GB isolated memory). |
| **Knowledge Base** | AI Search | Hybrid RAG (Vectorize + BM25). |
| **Validation** | Pydantic | Strict schema enforcement to stop hallucinations. |

## 4. Architectural Implementation
### A. The "Eyes" (Browser Interaction)
* **Action:** `browser-use` (Python) connects to `wss://browser.cloudflare.com`.
* **Rationale:** Decoupled architecture reduces memory footprint in the Python sandbox.

### B. The "Brain" (Context & RAG)
* **Action:** Scraped text is indexed into Hybrid AI Search.
* **Rationale:** BM25 ensures exact keyword matching for pricing terms (e.g., "egress") that vectors might miss.

### C. The "Memory" (Stateful Isolation)
* **Action:** Every session uses a unique Durable Object Facet with internal SQLite.
* **Rationale:** Physical data isolation ensures enterprise-grade multi-tenancy.

## 5. Success Metrics
* **Speed:** < 3- 5 minutes for full dual-site crawl.
* **Accuracy:** 100% (Pydantic validated).
* **Visibility:** 1:1 real-time visual parity on the dashboard.

## 6. Agentic Quality of Life (UX)
* **Reasoning Stream:** Live WebSocket feed of "thoughts" (intent -> action -> result).
* **Visual Audit Trail:** Automatic snapshots of key pages for data verification.
* **Resilient Navigation:** Smart-waits for dynamic content (calculators/drawers).
* **Reasoning Stream:** Live WebSocket feed of "thoughts" (intent -> action -> result).
* **Visual Audit Trail:** Automatic snapshots of key pages for data verification.
* **Freshness & Sanity Checks:** Automatic detection of "stale" documentation and cross-verification of outlier data (e.g., unusually low pricing).
* **Interactive HITL Resume:** Users can click into the live stream to solve a CAPTCHA and hit "Resume" to keep the agent moving.