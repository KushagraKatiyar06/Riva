import puppeteer, { Browser, Page, CDPSession } from '@cloudflare/puppeteer';
import { DurableObject } from 'cloudflare:workers';

export interface Env {
  BROWSER: Fetcher;
  RIVA_BRAIN: DurableObjectNamespace;
  GEMINI_API_KEY: string;
}

function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms));
}

export class RivaBrainDO extends DurableObject<Env> {
  private browser: Browser | null = null;
  private page: Page | null = null;
  private cdp: CDPSession | null = null;
  private idleTimer: any = null;

  constructor(ctx: DurableObjectState, env: Env) {
    super(ctx, env);
    try {
      this.ctx.storage.sql.exec(`
        CREATE TABLE IF NOT EXISTS thoughts (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          text TEXT NOT NULL,
          state TEXT NOT NULL,
          ts DATETIME DEFAULT CURRENT_TIMESTAMP
        );
      `);
      console.log('[DO] SQLite Memory Initialized');
    } catch (e: any) {
      console.error('[DO] SQLite Init Failed:', e.message || e);
    }
    this.resetIdleTimer();
  }

  private resetIdleTimer(): void {
    if (this.idleTimer) clearTimeout(this.idleTimer);
    this.idleTimer = setTimeout(() => this.cleanup(), 5 * 60 * 1000);
  }

  override async fetch(req: Request): Promise<Response> {
    const pair = new WebSocketPair();
    const [client, server] = Object.values(pair) as [WebSocket, WebSocket];
    this.ctx.acceptWebSocket(server);
    return new Response(null, { status: 101, webSocket: client });
  }

  override async webSocketMessage(ws: WebSocket, message: string | ArrayBuffer): Promise<void> {
    this.resetIdleTimer();
    const msg = JSON.parse(message as string);
    if (msg.url) {
      const url = msg.url.startsWith('http') ? msg.url : `https://${msg.url}`;
      
      // Replay History
      try {
        const history = this.ctx.storage.sql.exec(`SELECT text, state, ts FROM thoughts ORDER BY id ASC`).toArray();
        for (const row of history) {
          ws.send(JSON.stringify({ type: 'thought', text: row.text, state: row.state, ts: row.ts }));
        }
      } catch {}

      if (!this.browser) {
        this.ctx.waitUntil(this.startBrowserSession(ws, url));
      }
    }
  }

  private async startBrowserSession(ws: WebSocket, url: string): Promise<void> {
    try {
      this.sendThought(ws, 'Launching Agentic Brain...', 'info');
      this.browser = await puppeteer.launch(this.env.BROWSER);
      this.page = await this.browser.newPage();
      await this.page.setViewport({ width: 1280, height: 720 });
      this.cdp = await this.page.createCDPSession();
      
      this.cdp.on('Page.screencastFrame', async (p: any) => {
        this.sendFrame(ws, p.data);
        try { await this.cdp!.send('Page.screencastFrameAck', { sessionId: p.sessionId }); } catch {}
      });

      await this.cdp.send('Page.startScreencast', { format: 'jpeg', quality: 70, maxWidth: 1280, maxHeight: 720, everyNthFrame: 2 });
      
      this.sendThought(ws, `Navigating to ${url}...`, 'navigating');
      await this.page.goto(url, { waitUntil: 'domcontentloaded' });
      this.sendThought(ws, 'Page ready. Loop active.', 'complete');
    } catch (e: any) {
      this.sendThought(ws, `Error: ${e.message}`, 'error');
      this.cleanup();
    }
  }

  private sendThought(ws: WebSocket, text: string, state = 'info'): void {
    try { this.ctx.storage.sql.exec(`INSERT INTO thoughts (text, state) VALUES (?, ?)`, text, state); } catch {}
    const msg = JSON.stringify({ type: 'thought', text, state, ts: new Date().toLocaleTimeString() });
    for (const c of this.ctx.getWebSockets()) { try { c.send(msg); } catch {} }
  }

  private sendFrame(ws: WebSocket, data: string): void {
    const msg = JSON.stringify({ type: 'frame', data });
    for (const c of this.ctx.getWebSockets()) { try { c.send(msg); } catch {} }
  }

  private async cleanup(): Promise<void> {
    try { if (this.browser) await this.browser.close(); } catch {}
    this.browser = null; this.page = null;
  }
}

export default {
  async fetch(req: Request, env: Env): Promise<Response> {
    const url = new URL(req.url);
    if (url.pathname === '/ws/browse') {
      const id = env.RIVA_BRAIN.idFromName(url.searchParams.get('session') ?? 'default');
      return await env.RIVA_BRAIN.get(id).fetch(req);
    }
    return new Response('Riva Worker Active');
  }
};
