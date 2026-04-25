import puppeteer, { Browser, Page, CDPSession } from '@cloudflare/puppeteer';
import { DurableObject } from 'cloudflare:workers';

export interface Env {
  BROWSER: Fetcher;
  SESSION: DurableObjectNamespace;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms));
}

// ── Session Durable Object ────────────────────────────────────────────────────
//
// One DO instance per browser session. This solves the "hung Worker" error —
// DOs are designed for long-running stateful WebSocket connections. The runtime
// never kills them for being "hung" because that's their whole purpose.
//
// Later (Phase 3): this.ctx.storage.sql gives each session its own SQLite DB
// for persisting thought logs, navigation history, and scraped content.

export class SessionDO extends DurableObject<Env> {
  private browser: Browser | null = null;
  private page: Page | null = null;
  private cdp: CDPSession | null = null;

  // ── WebSocket upgrade ───────────────────────────────────────────────────
  override async fetch(req: Request): Promise<Response> {
    console.log('[DO] Fetch received in SessionDO');
    if (req.headers.get('Upgrade') !== 'websocket') {
      console.warn('[DO] Rejecting request: Expected WebSocket upgrade');
      return new Response('Expected WebSocket upgrade', { status: 426 });
    }

    try {
      const pair = new WebSocketPair();
      const [client, server] = Object.values(pair) as [WebSocket, WebSocket];

      // ctx.acceptWebSocket (not server.accept()) enables hibernation:
      // the DO sleeps between messages, costing nothing while idle.
      console.log('[DO] Accepting WebSocket with hibernation...');
      this.ctx.acceptWebSocket(server);

      console.log('[DO] Returning 101 Switching Protocols');
      return new Response(null, { status: 101, webSocket: client });
    } catch (e: any) {
      console.error('[DO] Error in fetch:', e.message || e);
      return new Response(`DO Internal Error: ${e.message || e}`, { status: 500 });
    }
  }

  // ── Incoming messages from frontend ─────────────────────────────────────
  override async webSocketMessage(ws: WebSocket, message: string | ArrayBuffer): Promise<void> {
    try {
      const msg = JSON.parse(message as string) as Record<string, unknown>;

      if (typeof msg.url === 'string' && !this.browser) {
        // First message: { url: "https://..." } — kick off the browser session.
        // ctx.waitUntil keeps the DO alive for the full duration of browsing.
        const url = msg.url.startsWith('http') ? msg.url : `https://${msg.url}`;
        this.ctx.waitUntil(this.startBrowserSession(ws, url));

      } else if (msg.type === 'click' && this.page) {
        // HITL click from the dashboard canvas
        const x = (msg.x as number) * 1280;
        const y = (msg.y as number) * 720;
        this.sendThought(ws, `HITL click → (${Math.round(x)}, ${Math.round(y)})`, 'hitl');
        await this.page.mouse.click(x, y);
        await sleep(500);
      }
    } catch { /* ignore malformed messages */ }
  }

  override async webSocketClose(): Promise<void> {
    await this.cleanup();
  }

  override async webSocketError(): Promise<void> {
    await this.cleanup();
  }

  // ── Browser session ──────────────────────────────────────────────────────
  private async startBrowserSession(ws: WebSocket, url: string): Promise<void> {
    try {
      // Launch Cloudflare-managed Chrome — replaces local Playwright entirely
      this.browser = await puppeteer.launch(this.env.BROWSER);
      this.page = await this.browser.newPage();
      await this.page.setViewport({ width: 1280, height: 720 });

      // CDP session — same protocol calls as the old Python backend
      this.cdp = await this.page.createCDPSession();

      this.cdp.on('Page.screencastFrame', async (params: { data: string; sessionId: number }) => {
        // params.data is already base64 JPEG — pass straight to frontend
        this.sendFrame(ws, params.data);
        try {
          await this.cdp!.send('Page.screencastFrameAck', { sessionId: params.sessionId });
        } catch { /* session may have closed */ }
      });

      await this.cdp.send('Page.startScreencast', {
        format: 'jpeg',
        quality: 70,
        maxWidth: 1280,
        maxHeight: 720,
        everyNthFrame: 2,
      });

      // ── Agent navigation ────────────────────────────────────────────────
      this.sendThought(ws, `Navigating to ${url} ...`, 'navigating');
      await this.page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30_000 });
      this.sendThought(ws, 'Page loaded. Scanning for nav targets...', 'scanning');
      await sleep(2000);

      const target = await this.findPricingTarget();

      if (target) {
        this.sendThought(ws, `Found '${target.label}' — highlighting`, 'found');
        await this.page.evaluate(({ x, y }: { x: number; y: number }) => {
          const el = document.elementFromPoint(x, y) as HTMLElement | null;
          if (el) {
            el.style.outline = '3px solid #00ffff';
            el.style.backgroundColor = 'rgba(0,255,255,0.15)';
          }
        }, { x: target.x, y: target.y });

        await sleep(1500);
        this.sendThought(ws, `Hovering '${target.label}'...`, 'hovering');
        await this.page.mouse.move(target.x, target.y);
        await sleep(800);
        this.sendThought(ws, `Clicking '${target.label}'...`, 'clicking');
        await this.page.mouse.click(target.x, target.y);
        await sleep(3000);
        this.sendThought(ws, `Landed on: ${this.page.url()}`, 'navigating');
      } else {
        this.sendThought(ws, 'No pricing link found — scrolling to scan.', 'scanning');
      }

      this.sendThought(ws, 'Scrolling to survey content...', 'scanning');
      await this.page.evaluate(() => window.scrollTo({ top: 600, behavior: 'smooth' }));
      await sleep(2000);
      await this.page.evaluate(() => window.scrollTo({ top: 1200, behavior: 'smooth' }));
      await sleep(2000);

      this.sendThought(ws, 'Navigation complete. HITL mode active — click the preview to interact.', 'complete');

      // Session stays alive via the open WebSocket + DO hibernation.
      // HITL clicks handled in webSocketMessage() above.

    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : String(e);
      this.sendThought(ws, `Error: ${msg}`, 'error');
      await this.cleanup();
    }
  }

  // ── Helpers ──────────────────────────────────────────────────────────────
  private async findPricingTarget(): Promise<{ x: number; y: number; label: string } | null> {
    if (!this.page) return null;
    return this.page.evaluate(() => {
      const keywords = ['pricing', 'plans'];
      const els = [...document.querySelectorAll<HTMLElement>('a[href], button')];
      for (const el of els) {
        const text = (el.textContent ?? '').trim().toLowerCase();
        const matched = keywords.find(k => text === k || text.startsWith(k + ' '));
        if (!matched) continue;
        const rect = el.getBoundingClientRect();
        if (rect.width > 0 && rect.height > 0 && rect.top >= 0 && rect.top < window.innerHeight) {
          return {
            x: rect.left + rect.width / 2,
            y: rect.top + rect.height / 2,
            label: (el.textContent ?? '').trim(),
          };
        }
      }
      return null;
    });
  }

  private sendThought(ws: WebSocket, text: string, state = 'info'): void {
    try { ws.send(JSON.stringify({ type: 'thought', text, state })); } catch { /* ws closed */ }
  }

  private sendFrame(ws: WebSocket, data: string): void {
    try { ws.send(JSON.stringify({ type: 'frame', data })); } catch { /* ws closed */ }
  }

  private async cleanup(): Promise<void> {
    try { await this.cdp?.send('Page.stopScreencast'); } catch {}
    try { await this.browser?.close(); } catch {}
    this.browser = null;
    this.page = null;
    this.cdp = null;
  }
}

// ── Worker entry point ────────────────────────────────────────────────────────
//
// The Worker's only job is routing: hand WebSocket connections off to a
// SessionDO instance. All browser logic lives in the DO.

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    const url = new URL(req.url);
    console.log(`[Worker] Incoming request: ${req.method} ${url.pathname}`);

    if (req.headers.get('Upgrade') === 'websocket' && url.pathname === '/ws/browse') {
      console.log('[Worker] WebSocket upgrade detected, routing to Durable Object...');
      try {
        // Each session gets its own DO keyed by UUID — sessions never collide.
        // Pass ?session=<id> to reconnect to an existing session later.
        const sessionId = url.searchParams.get('session') ?? crypto.randomUUID();
        console.log(`[Worker] Session ID: ${sessionId}`);
        
        const id = env.SESSION.idFromName(sessionId);
        const stub = env.SESSION.get(id);
        
        console.log('[Worker] Forwarding fetch to Durable Object stub...');
        return await stub.fetch(req);
      } catch (e: any) {
        console.error('[Worker] Durable Object routing failed:', e.message || e);
        return new Response(`Durable Object Error: ${e.message || e}`, { status: 502 });
      }
    }

    if (url.pathname === '/api/health') {
      return Response.json({ status: 'healthy', version: '1.0.0' });
    }

    return Response.json({ status: 'online', message: 'Riva Worker is running' });
  },
};
