# Riva Browser Architecture: Cloudflare Managed Chrome & Live Stream Bridge

## 1. Overview
This document outlines the architecture for connecting the Riva agent to Cloudflare's managed browser service (`browser.cloudflare.com`) while providing a real-time visual "audit trail" and Human-In-The-Loop (HITL) capability in the Next.js dashboard.

## 2. The Infrastructure Stack
*   **Browser Engine:** Cloudflare Browser Rendering (managed Chrome accessed via CDP over WSS).
*   **Orchestration Layer:** Python (FastAPI) + `browser-use` / Playwright.
*   **Communication:** WebSockets (Bi-directional for frames, logs, and user input).
*   **Frontend:** Next.js (React) + Canvas API for frame rendering.

## 3. Backend Implementation (The Bridge)
The Python backend acts as a high-speed proxy between the Cloudflare browser and the User Interface.

### A. Connectivity
*   Connect to Cloudflare using the WebSocket endpoint: `wss://browser.cloudflare.com/v1?token={CF_API_TOKEN}`.
*   Initialize the session and enable **CDP Screencast**:
    ```python
    # Conceptual CDP call
    await page.client.send('Page.startScreencast', {
        'format': 'jpeg',
        'quality': 80,
        'everyNthFrame': 1
    })
    ```

### B. The WebSocket Hub
A FastAPI WebSocket endpoint handles three concurrent streams:
1.  **Binary Stream (Outbound):** Sends JPEG frames from CDP `screencastFrame` events to the frontend.
2.  **JSON Stream (Outbound):** Sends the Agent's "Thought Stream" (Intent -> Action -> Result).
3.  **Command Stream (Inbound):** Receives manual click/type coordinates from the user for HITL intervention.

## 4. Frontend Implementation (The Dashboard)
The Next.js UI provides the "Eyes" for the user.

### A. Live Eyes Component
*   **Rendering:** Uses a `<canvas>` or optimized `<img>` tag to render incoming binary frames.
*   **Input Mapping:** Listens for mouse events on the canvas. Converts local click coordinates $(x, y)$ to the remote browser's resolution and sends them back via WebSocket.

### B. Reasoning Feed
*   A side-car terminal component that renders the agent's logic in real-time.
*   States: `Searching`, `Found Pricing`, `Parsing Table`, `Analyzing Documentation`.

## 5. Sequence of Operations
1.  **Initiation:** User enters a URL; Next.js opens a WebSocket connection to FastAPI.
2.  **Provisioning:** FastAPI requests a browser instance from Cloudflare.
3.  **Streaming:** As the agent navigates, Cloudflare sends frames -> FastAPI proxies to WebSocket -> Next.js renders.
4.  **Intervention:** If a CAPTCHA appears, the agent pauses. The user clicks the canvas; coordinates are sent back to FastAPI -> Cloudflare Browser; the user solves it manually.
5.  **Extraction:** Once reached, the agent parses pricing/docs and sends the final structured JSON data to the UI.

## 6. Key Advantages
*   **Cloud Native:** No local Chrome dependencies; scales horizontally on Cloudflare.
*   **Low Latency:** JPEG screencasting over WebSockets provides near-instant visual feedback.
*   **Enterprise HITL:** Built-in mechanism for handling bot-detection without failing the autonomous loop.
