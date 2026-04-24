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
      {/* Chrome-style toolbar */}
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
        <div
          className="w-2 h-2 rounded-full"
          style={{
            background: active ? color : '#333',
            boxShadow: active ? `0 0 6px ${color}` : 'none',
            animation: active ? 'pulse 2s infinite' : 'none',
          }}
        />
      </div>

      {/* Preview area */}
      <div
        className="relative flex-1 flex items-center justify-center"
        style={{
          background: '#020609',
          cursor: active && frameSrc ? 'crosshair' : 'default',
          minHeight: 0,
        }}
        onClick={handleClick}
      >
        {frameSrc ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={frameSrc}
            alt="browser preview"
            className="w-full h-full object-contain block"
            draggable={false}
          />
        ) : (
          <div className="flex flex-col items-center gap-4 opacity-20">
            {/* Idle eye */}
            <svg viewBox="0 0 200 120" style={{ width: 120 }}>
              <defs>
                <clipPath id={`idle-clip-${label}`}>
                  <path d="M10,60 Q100,-15 190,60 Q100,135 10,60" />
                </clipPath>
              </defs>
              <g clipPath={`url(#idle-clip-${label})`}>
                <path d="M10,60 Q100,-15 190,60 Q100,135 10,60" fill={active ? '#e0f7fa' : '#2a1a1a'} />
                <circle cx="100" cy="60" r="28" fill={active ? '#002233' : '#1a0000'} />
                <circle cx="100" cy="60" r="10" fill={color} style={{ filter: `drop-shadow(0 0 6px ${color})` }} />
                <rect width="200" height="120" fill={active ? '#1a2a4a' : '#2a1a1a'} style={{ transform: 'translateY(-100%)' }} />
              </g>
              <path d="M10,60 Q100,-15 190,60" fill="none" stroke={color} strokeWidth="2" opacity="0.3" />
            </svg>
            <p className="text-xs tracking-widest uppercase" style={{ color }}>
              {active ? 'Connecting...' : 'Pending'}
            </p>
          </div>
        )}

        {/* HITL hint overlay */}
        {active && frameSrc && (
          <div
            className="absolute bottom-2 right-2 text-xs px-2 py-1 rounded"
            style={{
              background: 'rgba(0,0,0,0.7)',
              color: 'rgba(255,255,255,0.4)',
              fontFamily: 'monospace',
              letterSpacing: 1,
            }}
          >
            click to intervene
          </div>
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
  const [compThoughts] = useState<Thought[]>([
    { text: 'Awaiting second URL input.', state: 'info', ts: now() },
  ]);

  const [rivaFrame, setRivaFrame] = useState<string>('');
  const [rivaStatus, setRivaStatus] = useState<string>(targetUrl);
  const [wsState, setWsState] = useState<'connecting' | 'open' | 'closed'>('connecting');

  const wsRef = useRef<WebSocket | null>(null);

  function now() {
    return new Date().toLocaleTimeString('en-US', { hour12: false });
  }

  function addThought(text: string, state: string) {
    setRivaThoughts(prev => [...prev, { text, state, ts: now() }]);
    if (state === 'navigating' && text.startsWith('Landed')) {
      setRivaStatus(text.replace('Landed on: ', ''));
    }
  }

  useEffect(() => {
    if (!targetUrl) return;

    const ws = new WebSocket('ws://localhost:8000/ws/browse');
    wsRef.current = ws;
    ws.onopen = () => {
      setWsState('open');
      ws.send(JSON.stringify({ url: targetUrl }));
    };

    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data as string);
        if (msg.type === 'frame') {
          setRivaFrame(`data:image/jpeg;base64,${msg.data}`);
        } else if (msg.type === 'thought') {
          addThought(msg.text, msg.state);
        }
      } catch {
        /* ignore malformed */
      }
    };

    ws.onclose = () => setWsState('closed');
    ws.onerror = () => addThought('WebSocket error — check backend.', 'error');

    return () => { ws.close(); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetUrl]);

  function handleRivaClick(x: number, y: number) {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: 'click', x, y }));
    }
  }

  const wsIndicatorColor =
    wsState === 'open' ? '#00ff88' :
    wsState === 'connecting' ? '#ffcc00' : '#ff3333';

  return (
    <div
      className="h-screen flex flex-col overflow-hidden"
      style={{ background: '#050a15', fontFamily: "'Inter', -apple-system, sans-serif" }}
    >
      {/* ── Top Bar ─────────────────────────────────────────── */}
      <header
        className="flex items-center justify-between px-6 py-3 border-b shrink-0"
        style={{ borderColor: 'rgba(0,255,255,0.1)', background: '#030810' }}
      >
        <button
          onClick={() => router.push('/')}
          className="flex items-center gap-2"
        >
          <div
            className="w-7 h-7 rounded-full flex items-center justify-center"
            style={{ background: '#00ffff', boxShadow: '0 0 12px rgba(0,255,255,0.4)' }}
          >
            <Shield size={14} color="black" />
          </div>
          <span className="text-lg font-bold tracking-[6px] uppercase">RIVA</span>
        </button>

        <div className="flex items-center gap-4 text-xs" style={{ fontFamily: 'monospace' }}>
          <div className="flex items-center gap-2">
            <div
              className="w-2 h-2 rounded-full"
              style={{
                background: wsIndicatorColor,
                boxShadow: `0 0 6px ${wsIndicatorColor}`,
                animation: wsState === 'open' ? 'pulse 2s infinite' : 'none',
              }}
            />
            <span style={{ color: 'rgba(255,255,255,0.4)' }}>Agent WS:</span>
            <span style={{ color: wsIndicatorColor, textTransform: 'uppercase' }}>{wsState}</span>
          </div>
          <div style={{ color: 'rgba(255,255,255,0.2)' }}>|</div>
          <div style={{ color: 'rgba(255,255,255,0.4)' }}>
            <span style={{ color: 'rgba(255,255,255,0.2)' }}>target: </span>
            <span style={{ color: '#00ffff' }}>{targetUrl.slice(0, 40)}{targetUrl.length > 40 ? '…' : ''}</span>
          </div>
        </div>
      </header>

      {/* ── Two-Panel Layout ─────────────────────────────────── */}
      <div className="flex flex-1 gap-0 overflow-hidden">

        {/* ── LEFT: Riva / Your Site ──────────────────────────── */}
        <div
          className="flex flex-col flex-1 overflow-hidden border-r"
          style={{ borderColor: 'rgba(0,255,255,0.12)' }}
        >
          <div
            className="px-4 py-2 text-xs font-bold tracking-[4px] uppercase border-b shrink-0"
            style={{
              color: '#00ffff',
              borderColor: 'rgba(0,255,255,0.12)',
              background: 'rgba(0,255,255,0.03)',
            }}
          >
            ◈ YOUR SITE — AGENT ACTIVE
          </div>

          <ThoughtLog thoughts={rivaThoughts} label="Riva" color="#00ffff" />

          <BrowserPanel
            label="RIVA"
            color="#00ffff"
            frameSrc={rivaFrame}
            active
            onCanvasClick={handleRivaClick}
            status={rivaStatus}
          />
        </div>

        {/* ── RIGHT: Competitor (placeholder) ─────────────────── */}
        <div className="flex flex-col flex-1 overflow-hidden">
          <div
            className="px-4 py-2 text-xs font-bold tracking-[4px] uppercase border-b shrink-0"
            style={{
              color: '#ff3333',
              borderColor: 'rgba(255,51,51,0.12)',
              background: 'rgba(255,51,51,0.03)',
            }}
          >
            ◈ COMPETITOR — PENDING
          </div>

          <ThoughtLog thoughts={compThoughts} label="Competitor" color="#ff3333" />

          <BrowserPanel
            label="COMP"
            color="#ff3333"
            frameSrc=""
            active={false}
            status=""
          />
        </div>
      </div>
    </div>
  );
}

// ── Page ─────────────────────────────────────────────────────
export default function Dashboard() {
  return (
    <Suspense
      fallback={
        <div className="h-screen flex items-center justify-center bg-[#050a15] text-white tracking-widest">
          INITIALIZING...
        </div>
      }
    >
      <DashboardContent />
    </Suspense>
  );
}

function now() {
  return new Date().toLocaleTimeString('en-US', { hour12: false });
}
