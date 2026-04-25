'use client';

import { useEffect, useRef, useState, Suspense } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';
import { Shield, Play, Pause, ArrowRight } from 'lucide-react';

type Thought = { text: string; state: string; ts: string };

const STATE_COLORS: Record<string, string> = {
  info:       '#aaaaaa',
  navigating: '#00ccff',
  scanning:   '#ffcc00',
  found:      '#00ff88',
  hovering:   '#bb88ff',
  clicking:   '#ff8800',
  hitl:       '#ff4466',
  complete:   '#00ff88',
  error:      '#ff3333',
  user:       '#ffffff',
};

function ThoughtLog({ thoughts, label, color }: { thoughts: Thought[]; label: string; color: string }) {
  const bottomRef = useRef<HTMLDivElement>(null);
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [thoughts]);

  return (
    <div className="flex flex-col h-40 overflow-y-auto rounded-t-lg border-b bg-[#020609] border-white/5 font-mono">
      <div className="flex items-center gap-2 px-3 py-1.5 border-b text-[9px] font-bold tracking-[3px] uppercase sticky top-0 bg-[#030810]" style={{ color }}>
        <span>◈</span> {label} STREAM
      </div>
      <div className="flex-1 px-3 py-2 space-y-0.5">
        {thoughts.map((t, i) => (
          <div key={i} className="flex items-start gap-2 text-[10px] leading-5">
            <span className="shrink-0 text-white/20">[{t.ts}]</span>
            <span style={{ color: STATE_COLORS[t.state] ?? '#ccc' }}>{t.text}</span>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}

function BrowserPanel({ label, color, frameSrc, active, isPaused, onTogglePause, onInteraction, onGoto }: any) {
  const [manualUrl, setManualUrl] = useState('');

  function handleClick(e: any) {
    if (!active || !frameSrc) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const x = (e.clientX - rect.left) / rect.width;
    const y = (e.clientY - rect.top) / rect.height;
    onInteraction?.('click', x, y);
  }

  function submitGoto() {
    if (!manualUrl.trim()) return;
    onGoto?.(manualUrl.trim());
    setManualUrl('');
  }

  return (
    <div className="flex flex-col rounded-b-lg overflow-hidden flex-1 border border-white/5 bg-[#020609]">
      <div className="flex items-center gap-2 px-3 py-2 bg-[#08111f] border-b border-white/5">
        <button
          onClick={onTogglePause}
          className="flex items-center gap-1.5 px-3 py-1 rounded font-bold text-[9px] tracking-widest transition-all shrink-0"
          style={{
            background: isPaused ? 'rgba(255,204,0,0.2)' : 'rgba(0,255,255,0.1)',
            color: isPaused ? '#ffcc00' : '#00ffff',
            border: `1px solid ${isPaused ? '#ffcc00' : '#00ffff'}44`
          }}
        >
          {isPaused ? <Play size={10} fill="currentColor"/> : <Pause size={10} fill="currentColor" />}
          {isPaused ? 'RESUME' : 'PAUSE'}
        </button>

        <div className="flex-1 flex gap-2">
          <input
            className="flex-1 bg-black/40 border border-white/10 rounded px-2 py-1 text-[9px] text-[#00ffff] font-mono outline-none"
            placeholder="Paste URL to navigate..."
            value={manualUrl}
            onChange={e => setManualUrl(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); submitGoto(); } }}
          />
          <button
            onClick={submitGoto}
            className="p-1 shrink-0 text-white/30 hover:text-[#00ffff] transition-colors"
          >
            <ArrowRight size={12}/>
          </button>
        </div>
      </div>

      <div
        className="relative flex-1 bg-black flex items-center justify-center overflow-hidden"
        onClick={handleClick}
        style={{ cursor: active && frameSrc ? 'crosshair' : 'default' }}
      >
        {frameSrc ? (
          <img src={frameSrc} className="w-full h-full object-contain pointer-events-none select-none" alt="preview" />
        ) : (
          <div className="text-white/5 text-[10px] tracking-[4px] uppercase">{label} OFFLINE</div>
        )}

        {isPaused && (
          <div className="absolute inset-0 border-2 border-yellow-500/20 pointer-events-none shadow-[inset_0_0_80px_rgba(234,179,8,0.1)]" />
        )}
      </div>
    </div>
  );
}

export default function Dashboard() {
  return <Suspense fallback={<div>Loading...</div>}><DashboardContent /></Suspense>;
}

function DashboardContent() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const rivaUrl = searchParams.get('riva') || '';
  const compUrl = searchParams.get('comp') || '';

  const [rivaThoughts, setRivaThoughts] = useState<Thought[]>([]);
  const [compThoughts, setCompThoughts] = useState<Thought[]>([]);
  const [rivaFrame,    setRivaFrame]    = useState('');
  const [compFrame,    setCompFrame]    = useState('');
  const [rivaPaused,   setRivaPaused]   = useState(false);
  const [compPaused,   setCompPaused]   = useState(false);
  const [rivaWsState,  setRivaWsState]  = useState<'connecting' | 'open' | 'closed'>('connecting');
  const [compWsState,  setCompWsState]  = useState<'connecting' | 'open' | 'closed'>('connecting');
  const rivaWsRef = useRef<WebSocket | null>(null);
  const compWsRef = useRef<WebSocket | null>(null);

  function now() { return new Date().toLocaleTimeString('en-US', { hour12: false, minute: '2-digit', second: '2-digit' }); }

  // Riva WS
  useEffect(() => {
    if (!rivaUrl) return;
    const ws = new WebSocket('ws://localhost:8000/ws/browse');
    rivaWsRef.current = ws;
    ws.onopen  = () => { setRivaWsState('open'); ws.send(JSON.stringify({ url: rivaUrl })); };
    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      if (msg.type === 'frame')      setRivaFrame(`data:image/jpeg;base64,${msg.data}`);
      else if (msg.type === 'thought') setRivaThoughts(p => [...p, { ...msg, ts: now() }]);
      else if (msg.type === 'auto_pause') setRivaPaused(true);
    };
    ws.onclose = () => setRivaWsState('closed');
    return () => ws.close();
  }, [rivaUrl]);

  // Competitor WS
  useEffect(() => {
    if (!compUrl) return;
    const ws = new WebSocket('ws://localhost:8000/ws/browse');
    compWsRef.current = ws;
    ws.onopen  = () => { setCompWsState('open'); ws.send(JSON.stringify({ url: compUrl })); };
    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      if (msg.type === 'frame')      setCompFrame(`data:image/jpeg;base64,${msg.data}`);
      else if (msg.type === 'thought') setCompThoughts(p => [...p, { ...msg, ts: now() }]);
      else if (msg.type === 'auto_pause') setCompPaused(true);
    };
    ws.onclose = () => setCompWsState('closed');
    return () => ws.close();
  }, [compUrl]);

  function sendToWs(wsRef: React.RefObject<WebSocket | null>, msg: object) {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(msg));
    }
  }

  function togglePause(side: 'riva' | 'comp') {
    if (side === 'riva') {
      const next = !rivaPaused;
      setRivaPaused(next);
      sendToWs(rivaWsRef, { type: next ? 'pause' : 'resume' });
    } else {
      const next = !compPaused;
      setCompPaused(next);
      sendToWs(compWsRef, { type: next ? 'pause' : 'resume' });
    }
  }

  return (
    <div className="h-screen flex flex-col bg-[#050a15] text-white overflow-hidden">
      <header className="flex items-center justify-between px-6 py-3 border-b border-white/5 bg-[#030810]">
        <button onClick={() => router.push('/')} className="flex items-center gap-3">
          <Shield size={20} className="text-[#00ffff]" />
          <span className="font-bold tracking-[8px] text-lg uppercase">RIVA</span>
        </button>
        <div className="flex items-center gap-4 text-[10px] font-mono">
          <span className="text-[#00ffff]/40 truncate max-w-[200px]">{rivaUrl}</span>
          <span className="text-white/20">vs</span>
          <span className="text-[#ff3333]/40 truncate max-w-[200px]">{compUrl}</span>
        </div>
        <div className="flex items-center gap-4 text-[10px] font-bold">
          <div className="flex items-center gap-1.5">
            <div className={`w-2 h-2 rounded-full ${rivaWsState === 'open' ? 'bg-green-500 shadow-[0_0_8px_#22c55e]' : 'bg-red-500'}`} />
            <span className="text-white/30">RIVA</span>
          </div>
          <div className="flex items-center gap-1.5">
            <div className={`w-2 h-2 rounded-full ${compWsState === 'open' ? 'bg-green-500 shadow-[0_0_8px_#22c55e]' : 'bg-red-500'}`} />
            <span className="text-white/30">COMP</span>
          </div>
        </div>
      </header>

      <div className="flex flex-1 overflow-hidden">
        <div className="flex flex-col flex-1 border-r border-white/5">
          <div className="px-4 py-2 text-[10px] font-bold tracking-[4px] bg-cyan-500/5 text-[#00ffff] border-b border-white/5">◈ YOUR SITE</div>
          <ThoughtLog thoughts={rivaThoughts} label="RIVA" color="#00ffff" />
          <BrowserPanel
            label="RIVA" color="#00ffff" frameSrc={rivaFrame} active
            isPaused={rivaPaused} onTogglePause={() => togglePause('riva')}
            onInteraction={(t: string, x: number, y: number) => sendToWs(rivaWsRef, { type: t, x, y })}
            onGoto={(url: string) => sendToWs(rivaWsRef, { type: 'goto', url })}
          />
        </div>
        <div className="flex flex-col flex-1 bg-black/20">
          <div className="px-4 py-2 text-[10px] font-bold tracking-[4px] bg-red-500/5 text-[#ff3333] border-b border-white/5">◈ COMPETITOR</div>
          <ThoughtLog thoughts={compThoughts} label="COMP" color="#ff3333" />
          <BrowserPanel
            label="COMP" color="#ff3333" frameSrc={compFrame} active
            isPaused={compPaused} onTogglePause={() => togglePause('comp')}
            onInteraction={(t: string, x: number, y: number) => sendToWs(compWsRef, { type: t, x, y })}
            onGoto={(url: string) => sendToWs(compWsRef, { type: 'goto', url })}
          />
        </div>
      </div>
    </div>
  );
}
