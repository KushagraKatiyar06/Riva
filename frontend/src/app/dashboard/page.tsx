'use client';

import { useEffect, useRef, useState, Suspense } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';
import { Shield, Play, Pause, ArrowRight, ExternalLink } from 'lucide-react';

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

function BrowserPanel({ label, color, frameSrc, active, status, isPaused, onTogglePause, onInteraction, onGoto }: any) {
  const [manualUrl, setManualUrl] = useState('');
  const lastMoveRef = useRef(0);

  function handleMouse(e: any, type: string) {
    if (!active || !frameSrc || !isPaused) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const x = (e.clientX - rect.left) / rect.width;
    const y = (e.clientY - rect.top) / rect.height;

    if (type === 'mousemove') {
      const now = Date.now();
      if (now - lastMoveRef.current < 80) return;
      lastMoveRef.current = now;
    }
    onInteraction(type, x, y);
  }

  function handleWheel(e: any) {
    if (!active || !frameSrc || !isPaused) return;
    onInteraction('scroll', 0, 0, e.deltaY);
  }

  return (
    <div className="flex flex-col rounded-b-lg overflow-hidden flex-1 border border-white/5 bg-[#020609]">
      <div className="flex items-center gap-2 px-3 py-2 bg-[#08111f] border-b border-white/5">
        <button 
          onClick={onTogglePause}
          className="flex items-center gap-1.5 px-3 py-1 rounded font-bold text-[9px] tracking-widest transition-all"
          style={{ 
            background: isPaused ? 'rgba(255,204,0,0.2)' : 'rgba(0,255,255,0.1)',
            color: isPaused ? '#ffcc00' : '#00ffff',
            border: `1px solid ${isPaused ? '#ffcc00' : '#00ffff'}44`
          }}
        >
          {isPaused ? <Play size={10} fill="currentColor"/> : <Pause size={10} fill="currentColor" />}
          {isPaused ? 'RESUME' : 'PAUSE'}
        </button>
        
        {isPaused ? (
          <div className="flex-1 flex gap-2">
            <input 
              className="flex-1 bg-black/40 border border-white/10 rounded px-2 py-1 text-[9px] text-[#00ffff] font-mono outline-none"
              placeholder="Enter URL to redirect..."
              value={manualUrl}
              onChange={e => setManualUrl(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && (onGoto(manualUrl), setManualUrl(''))}
            />
            <button onClick={() => {onGoto(manualUrl); setManualUrl('');}} className="p-1 hover:text-[#00ffff]"><ArrowRight size={12}/></button>
          </div>
        ) : (
          <div className="flex-1 text-[9px] bg-black/20 text-white/30 px-3 py-1 rounded truncate font-mono text-center">
            {status || 'WAITING...'}
          </div>
        )}
      </div>

      <div 
        className="relative flex-1 bg-black flex items-center justify-center overflow-hidden"
        style={{ cursor: isPaused ? 'crosshair' : 'wait' }}
        onMouseMove={e => handleMouse(e, 'mousemove')}
        onClick={e => handleMouse(e, 'click')}
        onWheel={handleWheel}
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
  const targetUrl = searchParams.get('url') || '';

  const [rivaThoughts, setRivaThoughts] = useState<Thought[]>([]);
  const [compThoughts, setCompThoughts] = useState<Thought[]>([]);
  const [rivaFrame, setRivaFrame] = useState('');
  const [rivaPaused, setRivaPaused] = useState(false);
  const [compPaused, setCompPaused] = useState(false);
  const [wsState, setWsState] = useState<'connecting' | 'open' | 'closed'>('connecting');
  const wsRef = useRef<WebSocket | null>(null);

  function now() { return new Date().toLocaleTimeString('en-US', { hour12: false, minute:'2-digit', second:'2-digit' }); }

  useEffect(() => {
    if (!targetUrl) return;
    const ws = new WebSocket(`ws://localhost:8000/ws/browse`);
    wsRef.current = ws;
    ws.onopen = () => { setWsState('open'); ws.send(JSON.stringify({ url: targetUrl })); };
    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      if (msg.type === 'frame') setRivaFrame(`data:image/jpeg;base64,${msg.data}`);
      else if (msg.type === 'thought') setRivaThoughts(p => [...p, { ...msg, ts: now() }]);
      else if (msg.type === 'auto_pause') setRivaPaused(true);
    };
    ws.onclose = () => setWsState('closed');
    return () => ws.close();
  }, [targetUrl]);

  function handleInteraction(type: string, x: number, y: number, deltaY = 0) {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type, x, y, deltaY }));
    }
  }

  function handleGoto(url: string) {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: 'goto', url }));
    }
  }

  function togglePause(side: 'riva' | 'comp') {
    const isP = side === 'riva' ? !rivaPaused : !compPaused;
    if (side === 'riva') setRivaPaused(isP); else setCompPaused(isP);
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: isP ? 'pause' : 'resume' }));
    }
  }

  return (
    <div className="h-screen flex flex-col bg-[#050a15] text-white overflow-hidden">
      <header className="flex items-center justify-between px-6 py-3 border-b border-white/5 bg-[#030810]">
        <button onClick={() => router.push('/')} className="flex items-center gap-3">
          <Shield size={20} className="text-[#00ffff]" />
          <span className="font-bold tracking-[8px] text-lg uppercase">RIVA</span>
        </button>
        <div className="text-[10px] font-mono text-[#00ffff]/60 bg-black/40 px-4 py-1.5 rounded-full border border-white/5">
          TARGET: {targetUrl}
        </div>
        <div className="flex items-center gap-3 text-[10px] font-bold">
           <div className={`w-2 h-2 rounded-full ${wsState === 'open' ? 'bg-green-500 shadow-[0_0_10px_#22c55e]' : 'bg-red-500'}`} />
           {wsState.toUpperCase()}
        </div>
      </header>

      <div className="flex flex-1 overflow-hidden">
        <div className="flex flex-col flex-1 border-r border-white/5">
          <div className="px-4 py-2 text-[10px] font-bold tracking-[4px] bg-cyan-500/5 text-[#00ffff] border-b border-white/5">◈ PRIMARY AGENT</div>
          <ThoughtLog thoughts={rivaThoughts} label="RIVA" color="#00ffff" />
          <BrowserPanel 
            label="RIVA" color="#00ffff" frameSrc={rivaFrame} active status={targetUrl}
            isPaused={rivaPaused} onTogglePause={() => togglePause('riva')}
            onInteraction={handleInteraction} onGoto={handleGoto}
          />
        </div>
        <div className="flex flex-col flex-1 bg-black/20">
          <div className="px-4 py-2 text-[10px] font-bold tracking-[4px] bg-red-500/5 text-[#ff3333] border-b border-white/5">◈ COMPETITOR INTEL</div>
          <ThoughtLog thoughts={compThoughts} label="COMP" color="#ff3333" />
          <BrowserPanel 
            label="COMP" color="#ff3333" frameSrc="" active={false} status=""
            isPaused={compPaused} onTogglePause={() => togglePause('comp')}
          />
        </div>
      </div>
    </div>
  );
}
