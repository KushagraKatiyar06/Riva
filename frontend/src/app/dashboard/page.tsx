'use client';

import { useEffect, useRef, useState, Suspense } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';
import { Shield } from 'lucide-react';

// ── Types ────────────────────────────────────────────────────
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
};

const STATE_ICONS: Record<string, string> = {
  info:       '·',
  navigating: '→',
  scanning:   '◎',
  found:      '✓',
  hovering:   '⌖',
  clicking:   '↵',
  hitl:       '⊕',
  complete:   '★',
  error:      '✗',
};

// ── Thought Log ──────────────────────────────────────────────
function ThoughtLog({ thoughts, label, color }: { thoughts: Thought[]; label: string; color: string }) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [thoughts]);

  return (
    <div
      className="flex flex-col h-48 overflow-y-auto rounded-t-lg border-b"
      style={{
        background: '#020609',
        borderColor: `${color}22`,
        fontFamily: "'Geist Mono', 'Courier New', monospace",
      }}
    >
      <div
        className="flex items-center gap-2 px-3 py-1.5 border-b text-xs font-bold tracking-[3px] uppercase sticky top-0"
        style={{ background: '#030810', borderColor: `${color}22`, color }}
      >
        <span style={{ opacity: 0.5 }}>◈</span>
        <span>{label} — THOUGHT STREAM</span>
      </div>

      <div className="flex-1 px-3 py-2 space-y-0.5">
        {thoughts.length === 0 ? (
          <p className="text-xs" style={{ color: 'rgba(255,255,255,0.2)' }}>Waiting for agent...</p>
        ) : (
          thoughts.map((t, i) => (
            <div key={i} className="flex items-start gap-2 text-xs leading-5">
              <span className="shrink-0 mt-0.5" style={{ color: STATE_COLORS[t.state] ?? '#aaa', width: 12 }}>
                {STATE_ICONS[t.state] ?? '·'}
              </span>
              <span className="shrink-0" style={{ color: 'rgba(255,255,255,0.25)', fontSize: 10 }}>
                {t.ts}
              </span>
              <span style={{ color: STATE_COLORS[t.state] ?? '#ccc' }}>{t.text}</span>
            </div>
          ))
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}

// ── Browser Panel ────────────────────────────────────────────
function BrowserPanel({
  label,
  color,
  frameSrc,
  active,
  onCanvasClick,
  status,
}: {
  label: string;
  color: string;
  frameSrc: string;
  active: boolean;
  onCanvasClick?: (x: number, y: number) => void;
  status: string;
}) {
  function handleClick(e: React.MouseEvent<HTMLDivElement>) {
    if (!onCanvasClick || !frameSrc) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const x = (e.clientX - rect.left) / rect.width;
    const y = (e.clientY - rect.top) / rect.height;
    onCanvasClick(x, y);
  }

  return (
    <div
      className="flex flex-col rounded-b-lg overflow-hidden flex-1"
      style={{ border: `1px solid ${color}22`, borderTop: 'none' }}
    >
      <div
        className="flex items-center gap-2 px-3 py-2 border-b"
        style={{ background: '#08111f', borderColor: `${color}22` }}
      >
        <div className="flex gap-1.5">
          <div className="w-3 h-3 rounded-full" style={{ background: '#ff5f56' }} />
          <div className="w-3 h-3 rounded-full" style={{ background: '#ffbd2e' }} />
          <div className="w-3 h-3 rounded-full" style={{ background: '#27c93f' }} />
        </div>
        <div
          className="flex-1 text-xs rounded px-2 py-0.5 mx-2"
          style={{ background: '#040b16', color: 'rgba(255,255,255,0.3)', fontFamily: 'monospace' }}
        >
          {active ? (status.length > 50 ? status.slice(0, 50) + '…' : status) : '—'}
        </div>
        <div
          className="text-xs font-bold tracking-[2px] uppercase px-2"
          style={{ color, opacity: active ? 1 : 0.3 }}
        >
          {label}
        </div>
      </div>

      <div
        className="relative flex-1 flex items-center justify-center"
        style={{ background: '#020609', cursor: active && frameSrc ? 'crosshair' : 'default', minHeight: 0 }}
        onClick={handleClick}
      >
        {frameSrc ? (
          <img src={frameSrc} alt="preview" className="w-full h-full object-contain block" draggable={false} />
        ) : (
          <p className="text-xs tracking-widest uppercase opacity-20" style={{ color }}>{label} OFFLINE</p>
        )}
      </div>
    </div>
  );
}

// ── Dashboard Content ─────────────────────────────────────────
function DashboardContent() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const targetUrl = searchParams.get('url') || '';

  const [rivaThoughts, setRivaThoughts] = useState<Thought[]>([]);
  const [compThoughts, setCompThoughts] = useState<Thought[]>([]);

  const [rivaFrame, setRivaFrame] = useState<string>('');
  const [rivaStatus, setRivaStatus] = useState<string>(targetUrl);
  const [wsState, setWsState] = useState<'connecting' | 'open' | 'closed'>('connecting');

  const wsRef = useRef<WebSocket | null>(null);

  function now() { return new Date().toLocaleTimeString('en-US', { hour12: false }); }

  useEffect(() => {
    setCompThoughts([{ text: 'Awaiting second URL input.', state: 'info', ts: now() }]);
  }, []);

  function addThought(text: string, state: string) {
    setRivaThoughts(prev => [...prev, { text, state, ts: now() }]);
  }

  useEffect(() => {
    if (!targetUrl) return;
    const ws = new WebSocket(`ws://localhost:8000/ws/browse`);
    wsRef.current = ws;
    let hasConnected = false;

    ws.onopen = () => {
      hasConnected = true;
      setWsState('open');
      ws.send(JSON.stringify({ url: targetUrl }));
    };

    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.type === 'frame') setRivaFrame(`data:image/jpeg;base64,${msg.data}`);
        else if (msg.type === 'thought') addThought(msg.text, msg.state);
      } catch {}
    };

    ws.onclose = () => setWsState('closed');
    ws.onerror = () => { if (!hasConnected) addThought('Connecting to agent...', 'info'); };

    return () => ws.close();
  }, [targetUrl]);

  function handleRivaClick(x: number, y: number) {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: 'click', x, y }));
    }
  }

  return (
    <div className="h-screen flex flex-col bg-[#050a15] text-white overflow-hidden">
      <header className="flex items-center justify-between px-6 py-3 border-b border-white/5 bg-[#030810]">
        <button onClick={() => router.push('/')} className="flex items-center gap-2">
          <Shield size={18} color="#00ffff" />
          <span className="font-bold tracking-[8px] uppercase">RIVA</span>
        </button>
        <div className="text-[10px] font-mono text-[#00ffff] opacity-50 truncate max-w-[40%]">
          TARGET: {targetUrl}
        </div>
        <div className="flex items-center gap-2 text-xs font-bold uppercase opacity-40">
           <div className={`w-2 h-2 rounded-full ${wsState === 'open' ? 'bg-green-500' : 'bg-red-500'}`} />
           {wsState}
        </div>
      </header>

      <div className="flex flex-1 overflow-hidden">
        <div className="flex flex-col flex-1 border-r border-white/5">
          <div className="px-4 py-2 text-[10px] font-bold tracking-[4px] uppercase bg-cyan-500/5 text-[#00ffff]">◈ YOUR SITE</div>
          <ThoughtLog thoughts={rivaThoughts} label="Riva" color="#00ffff" />
          <BrowserPanel label="RIVA" color="#00ffff" frameSrc={rivaFrame} active onCanvasClick={handleRivaClick} status={rivaStatus} />
        </div>
        <div className="flex flex-col flex-1">
          <div className="px-4 py-2 text-[10px] font-bold tracking-[4px] uppercase bg-red-500/5 text-[#ff3333]">◈ COMPETITOR</div>
          <ThoughtLog thoughts={compThoughts} label="Competitor" color="#ff3333" />
          <BrowserPanel label="COMP" color="#ff3333" frameSrc="" active={false} status="" />
        </div>
      </div>
    </div>
  );
}

export default function Dashboard() {
  return <Suspense fallback={<div>Loading...</div>}><DashboardContent /></Suspense>;
}
