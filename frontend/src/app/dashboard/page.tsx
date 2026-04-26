'use client';

import { useEffect, useRef, useState, useMemo, Suspense } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';
import { Shield, Play, Pause, ArrowRight, MessageCircle, X, Send, Download } from 'lucide-react';

type Thought     = { text: string; state: string; ts: string };
type PipelineLog = { text: string; state: string; ts: string };
type ChatMsg     = { role: 'assistant' | 'user'; text: string; ts: string };

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
  success:    '#00ff88',
};

const PIPELINE_STATE_COLORS: Record<string, string> = {
  info:    '#888888',
  success: '#00cccc',
  error:   '#ff3333',
};

function now() {
  return new Date().toLocaleTimeString('en-US', {
    hour12: false, minute: '2-digit', second: '2-digit',
  });
}

// ---------------------------------------------------------------------------
// ThoughtLog — per-browser AI stream
// ---------------------------------------------------------------------------
function ThoughtLog({
  thoughts, label, color,
}: { thoughts: Thought[]; label: string; color: string }) {
  const bottomRef = useRef<HTMLDivElement>(null);
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [thoughts]);

  return (
    <div className="flex flex-col h-32 overflow-y-auto border-b bg-[#020609] border-white/5 font-mono">
      <div
        className="flex items-center gap-2 px-3 py-1.5 border-b text-[9px] font-bold tracking-[3px] uppercase sticky top-0 bg-[#030810]"
        style={{ color }}
      >
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

// ---------------------------------------------------------------------------
// BrowserPanel
// ---------------------------------------------------------------------------
function BrowserPanel({
  label, color, frameSrc, active, isPaused, isComplete, wsActive,
  onTogglePause, onInteraction, onGoto, onRestart,
}: any) {
  const [manualUrl, setManualUrl] = useState('');

  function handleClick(e: any) {
    if (!active || !frameSrc) return;
    const rect = e.currentTarget.getBoundingClientRect();
    onInteraction?.('click', (e.clientX - rect.left) / rect.width, (e.clientY - rect.top) / rect.height);
  }

  function submitGoto() {
    if (!manualUrl.trim()) return;
    if (!wsActive) {
      // No active browser — open a new Chrome instance from this URL
      onRestart?.(manualUrl.trim());
    } else {
      onGoto?.(manualUrl.trim());
    }
    setManualUrl('');
  }

  return (
    <div className="flex flex-col flex-1 rounded-b-lg overflow-hidden border border-white/5 bg-[#020609]">
      <div className="flex items-center gap-2 px-3 py-2 bg-[#08111f] border-b border-white/5 shrink-0">
        {isComplete ? (
          <div
            className="flex items-center gap-1.5 px-3 py-1 rounded font-bold text-[9px] tracking-widest shrink-0"
            style={{ background: 'rgba(0,255,136,0.1)', color: '#00ff88', border: '1px solid #00ff8844' }}
          >
            ✓ COMPLETED
          </div>
        ) : (
          <button
            onClick={onTogglePause}
            className="flex items-center gap-1.5 px-3 py-1 rounded font-bold text-[9px] tracking-widest transition-all shrink-0"
            style={{
              background: isPaused ? 'rgba(255,204,0,0.2)' : 'rgba(0,255,255,0.1)',
              color:      isPaused ? '#ffcc00' : '#00ffff',
              border:     `1px solid ${isPaused ? '#ffcc00' : '#00ffff'}44`,
            }}
          >
            {isPaused ? <Play size={10} fill="currentColor" /> : <Pause size={10} fill="currentColor" />}
            {isPaused ? 'RESUME' : 'PAUSE'}
          </button>
        )}
        <div className="flex-1 flex gap-2">
          <input
            className="flex-1 bg-black/40 border border-white/10 rounded px-2 py-1 text-[9px] font-mono outline-none"
            style={{ color: wsActive ? '#00ffff' : '#ffffff88' }}
            placeholder={wsActive ? 'Paste URL to navigate...' : 'Paste URL to open new browser...'}
            value={manualUrl}
            onChange={e => setManualUrl(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); submitGoto(); } }}
          />
          <button onClick={submitGoto} className="p-1 shrink-0 text-white/30 hover:text-[#00ffff] transition-colors">
            <ArrowRight size={12} />
          </button>
        </div>
      </div>
      <div
        className="relative flex-1 bg-black flex items-center justify-center overflow-hidden"
        onClick={handleClick}
        style={{ cursor: active && frameSrc && !isComplete ? 'crosshair' : 'default' }}
      >
        {frameSrc ? (
          <img
            src={frameSrc}
            className="w-full h-full object-contain pointer-events-none select-none"
            style={{ opacity: isComplete ? 0.35 : 1, filter: isComplete ? 'grayscale(0.4)' : 'none' }}
            alt="preview"
          />
        ) : (
          <div className="flex flex-col items-center gap-2">
            <div className="text-white/5 text-[10px] tracking-[4px] uppercase">{label} OFFLINE</div>
            {!wsActive && (
              <div className="text-white/10 text-[9px]">Enter a URL above to open a browser</div>
            )}
          </div>
        )}
        {isPaused && !isComplete && (
          <div className="absolute inset-0 border-2 border-yellow-500/20 pointer-events-none shadow-[inset_0_0_80px_rgba(234,179,8,0.1)]" />
        )}
        {isComplete && frameSrc && (
          <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
            <div
              className="flex flex-col items-center gap-2 px-6 py-4 rounded-xl"
              style={{ background: 'rgba(0,0,0,0.55)', border: '1px solid rgba(0,255,136,0.25)' }}
            >
              <div className="text-[#00ff88] text-[11px] font-bold tracking-[4px] uppercase">✓ Scraping Complete</div>
              <div className="text-white/30 text-[9px] tracking-wider">Browser closed</div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Pipeline log — terminal-style area below browsers
// ---------------------------------------------------------------------------
function PipelineLogPanel({
  logs, status, reportReady, onOpenChat, onViewReport,
}: {
  logs: PipelineLog[];
  status: string;
  reportReady: boolean;
  onOpenChat: () => void;
  onViewReport: () => void;
}) {
  const bottomRef = useRef<HTMLDivElement>(null);
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [logs]);

  const statusColor =
    status === 'ready' ? '#00cccc' : status === 'vectorizing' ? '#ffcc00' : '#888888';

  return (
    <div className="flex flex-col h-52 shrink-0 border-t border-white/5 bg-[#010508] font-mono">
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-white/5 bg-[#020810] shrink-0">
        <div className="flex items-center gap-2 text-[9px] font-bold tracking-[3px] uppercase text-[#00cccc]">
          <span>▶</span> PIPELINE
          <span
            className="ml-2 px-1.5 py-0.5 rounded text-[8px] font-bold"
            style={{ background: statusColor + '22', color: statusColor }}
          >
            {status.toUpperCase()}
          </span>
        </div>
        <div className="flex items-center gap-2">
          {reportReady && (
            <button
              onClick={onViewReport}
              className="flex items-center gap-1.5 px-2 py-1 rounded text-[9px] font-bold tracking-widest transition-all animate-pulse"
              style={{ background: 'rgba(0,168,107,0.15)', color: '#00a86b', border: '1px solid #00a86b55' }}
            >
              <Download size={10} />
              VIEW REPORT
            </button>
          )}
          <button
            onClick={onOpenChat}
            className="flex items-center gap-1.5 px-2 py-1 rounded text-[9px] font-bold tracking-widest transition-all"
            style={{ background: 'rgba(0,204,204,0.1)', color: '#00cccc', border: '1px solid #00cccc44' }}
          >
            <MessageCircle size={10} />
            CHAT
          </button>
        </div>
      </div>
      <div className="flex-1 overflow-y-auto px-3 py-2 space-y-0.5">
        {logs.length === 0 && (
          <div className="text-[10px] text-white/15 tracking-wider">
            Pipeline active — will vectorize each domain as agents complete...
          </div>
        )}
        {logs.map((l, i) => (
          <div key={i} className="flex items-start gap-2 text-[10px] leading-5">
            <span className="shrink-0 text-white/20">[{l.ts}]</span>
            <span style={{ color: PIPELINE_STATE_COLORS[l.state] ?? '#888' }}>{l.text}</span>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Report modal — full-screen centered viewer
// ---------------------------------------------------------------------------
function ReportModal({
  reportUrl, reportType, onClose,
}: { reportUrl: string; reportType: 'pdf' | 'pptx'; onClose: () => void }) {
  const fullUrl = `http://localhost:8000${reportUrl}`;
  const label   = reportType === 'pdf' ? 'ONE-PAGER REPORT' : 'POWERPOINT DECK';

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center overflow-auto py-6 px-4"
      style={{ background: 'rgba(0,0,0,0.88)' }}
      onClick={e => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div
        className="relative w-full flex flex-col rounded-xl overflow-hidden shadow-2xl"
        style={{ maxWidth: 960, maxHeight: '90vh', background: '#0a1628', border: '1px solid rgba(0,204,204,0.2)' }}
      >
        {/* Modal header */}
        <div className="flex items-center justify-between px-4 py-3 border-b shrink-0"
          style={{ borderColor: 'rgba(0,204,204,0.15)' }}>
          <div className="text-[10px] font-bold tracking-[3px] uppercase text-[#00cccc]">
            ◈ {label}
          </div>
          <div className="flex items-center gap-2">
            <a
              href={fullUrl}
              target="_blank"
              rel="noreferrer"
              className="flex items-center gap-1.5 px-3 py-1.5 rounded text-[9px] font-bold tracking-widest transition-all"
              style={{ background: 'rgba(0,204,204,0.1)', color: '#00cccc', border: '1px solid #00cccc44' }}
            >
              Open in new tab
            </a>
            <button
              onClick={onClose}
              className="p-1.5 rounded text-white/30 hover:text-white transition-colors"
              style={{ border: '1px solid rgba(255,255,255,0.1)' }}
            >
              <X size={14} />
            </button>
          </div>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-auto" style={{ minHeight: 0 }}>
          {reportType === 'pdf' ? (
            <div className="flex flex-col items-center justify-center gap-6 py-16">
              <div className="text-white/30 text-[11px] tracking-[3px] uppercase">
                PDF report ready
              </div>
              <a
                href={fullUrl}
                target="_blank"
                rel="noreferrer"
                className="flex items-center gap-2 px-6 py-3 rounded-lg font-bold text-[13px] tracking-widest transition-all"
                style={{ background: 'rgba(0,204,204,0.15)', color: '#00cccc', border: '1px solid #00cccc55' }}
              >
                <Download size={18} />
                Open / Download PDF
              </a>
              <div className="text-white/15 text-[10px]">
                Opens in a new tab — use your browser&apos;s save button to download
              </div>
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center gap-6 py-16">
              <div className="text-white/30 text-[11px] tracking-[3px] uppercase">
                PowerPoint deck ready
              </div>
              <a
                href={fullUrl}
                target="_blank"
                rel="noreferrer"
                className="flex items-center gap-2 px-6 py-3 rounded-lg font-bold text-[13px] tracking-widest transition-all"
                style={{ background: 'rgba(0,204,204,0.15)', color: '#00cccc', border: '1px solid #00cccc55' }}
              >
                <Download size={18} />
                Download PowerPoint
              </a>
              <div className="text-white/15 text-[10px]">
                Open in Microsoft PowerPoint or Google Slides
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Chat drawer — slides in from right
// ---------------------------------------------------------------------------
function ChatDrawer({
  messages, onSend, onClose, pipelineReady,
}: {
  messages: ChatMsg[];
  onSend: (text: string) => void;
  onClose: () => void;
  pipelineReady: boolean;
}) {
  const [input, setInput] = useState('');
  const bottomRef = useRef<HTMLDivElement>(null);
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages]);

  function send() {
    if (!input.trim()) return;
    onSend(input.trim());
    setInput('');
  }

  return (
    <div
      className="fixed right-0 top-0 h-full w-80 flex flex-col z-50 border-l border-white/10"
      style={{ background: '#020c1a' }}
    >
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-white/10 shrink-0">
        <div className="text-[10px] font-bold tracking-[3px] uppercase text-[#00cccc]">
          ◈ RIVA CHAT
        </div>
        <button onClick={onClose} className="text-white/30 hover:text-white transition-colors">
          <X size={16} />
        </button>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-3 font-mono">
        {messages.length === 0 && (
          <div className="text-[10px] text-white/20 leading-6 tracking-wide">
            The pipeline will auto-message you when analysis is complete. You can also ask questions once data is vectorized.
          </div>
        )}
        {messages.map((m, i) => (
          <div
            key={i}
            className={`flex flex-col gap-1 ${m.role === 'user' ? 'items-end' : 'items-start'}`}
          >
            <div
              className="text-[9px] font-bold tracking-[2px] uppercase"
              style={{ color: m.role === 'assistant' ? '#00cccc' : '#ffffff44' }}
            >
              {m.role === 'assistant' ? 'RIVA' : 'YOU'}
            </div>
            <div
              className="max-w-[240px] px-3 py-2 rounded text-[11px] leading-5 whitespace-pre-wrap"
              style={
                m.role === 'assistant'
                  ? { background: '#0d2040', color: '#cde' }
                  : { background: 'rgba(0,204,204,0.12)', color: '#fff' }
              }
            >
              {m.text}
            </div>
            <div className="text-[8px] text-white/15">{m.ts}</div>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="px-3 py-3 border-t border-white/10 shrink-0">
        {!pipelineReady && (
          <div className="text-[9px] text-white/20 mb-2 tracking-wide">
            Chat available after vectorization completes
          </div>
        )}
        <div className="flex gap-2">
          <input
            className="flex-1 bg-black/40 border border-white/10 rounded px-3 py-2 text-[11px] text-white font-mono outline-none placeholder-white/20"
            placeholder={pipelineReady ? 'Ask a question...' : 'Waiting...'}
            value={input}
            disabled={!pipelineReady}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); send(); } }}
          />
          <button
            onClick={send}
            disabled={!pipelineReady || !input.trim()}
            className="p-2 rounded transition-all"
            style={{
              background: pipelineReady && input.trim() ? 'rgba(0,204,204,0.2)' : 'rgba(255,255,255,0.05)',
              color:      pipelineReady && input.trim() ? '#00cccc' : '#ffffff22',
            }}
          >
            <Send size={14} />
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Dashboard
// ---------------------------------------------------------------------------
export default function Dashboard() {
  return (
    <Suspense fallback={<div>Loading...</div>}>
      <DashboardContent />
    </Suspense>
  );
}

function DashboardContent() {
  const searchParams = useSearchParams();
  const router       = useRouter();
  const rivaUrl      = searchParams.get('riva') || '';
  const compUrl      = searchParams.get('comp') || '';

  // Stable session ID for this page load
  const sessionId = useMemo(() => crypto.randomUUID(), []);
  const expected  = (rivaUrl ? 1 : 0) + (compUrl ? 1 : 0);

  // Browser state
  const [rivaThoughts, setRivaThoughts] = useState<Thought[]>([]);
  const [compThoughts, setCompThoughts] = useState<Thought[]>([]);
  const [rivaFrame,    setRivaFrame]    = useState('');
  const [compFrame,    setCompFrame]    = useState('');
  const [rivaPaused,    setRivaPaused]    = useState(false);
  const [compPaused,    setCompPaused]    = useState(false);
  const [rivaComplete,  setRivaComplete]  = useState(false);
  const [compComplete,  setCompComplete]  = useState(false);
  const [rivaWsState,   setRivaWsState]   = useState<'connecting' | 'open' | 'closed'>('connecting');
  const [compWsState,   setCompWsState]   = useState<'connecting' | 'open' | 'closed'>('connecting');

  // Pipeline state
  const [pipelineLogs,   setPipelineLogs]   = useState<PipelineLog[]>([]);
  const [pipelineStatus, setPipelineStatus] = useState<'waiting' | 'vectorizing' | 'ready'>('waiting');
  const [pipelineWsState, setPipelineWsState] = useState<'connecting' | 'open' | 'closed'>('connecting');

  // Report state
  const [reportUrl,       setReportUrl]       = useState<string | null>(null);
  const [reportType,      setReportType]      = useState<'pdf' | 'pptx' | null>(null);
  const [reportModalOpen, setReportModalOpen] = useState(false);

  // Chat state
  const [chatOpen,     setChatOpen]     = useState(false);
  const [chatMessages, setChatMessages] = useState<ChatMsg[]>([]);

  const rivaWsRef     = useRef<WebSocket | null>(null);
  const compWsRef     = useRef<WebSocket | null>(null);
  const pipelineWsRef = useRef<WebSocket | null>(null);

  // Pipeline WS (opens first)
  useEffect(() => {
    if (!sessionId || expected === 0) return;
    const ws = new WebSocket(
      `ws://localhost:8000/ws/pipeline?session=${sessionId}&expected=${expected}`
    );
    pipelineWsRef.current = ws;
    ws.onopen  = () => setPipelineWsState('open');
    ws.onclose = () => setPipelineWsState('closed');
    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      if (msg.type === 'log') {
        setPipelineLogs(p => [...p, { text: msg.text, state: msg.state, ts: now() }]);
      } else if (msg.type === 'chat') {
        setChatMessages(p => [...p, { role: 'assistant', text: msg.text, ts: now() }]);
        setChatOpen(true); // auto-open drawer when assistant speaks
      } else if (msg.type === 'status') {
        setPipelineStatus(msg.value);
      } else if (msg.type === 'report_ready') {
        setReportUrl(msg.url);
        setReportType(msg.report_type);
      }
    };
    return () => ws.close();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  // Riva browse WS
  useEffect(() => {
    if (!rivaUrl) { setRivaWsState('closed'); return; }
    const ws = new WebSocket(
      `ws://localhost:8000/ws/browse?session=${sessionId}&role=riva`
    );
    rivaWsRef.current = ws;
    ws.onopen    = () => { setRivaWsState('open'); ws.send(JSON.stringify({ url: rivaUrl })); };
    ws.onclose   = () => setRivaWsState('closed');
    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      if (msg.type === 'frame') {
        setRivaFrame(`data:image/jpeg;base64,${msg.data}`);
      } else if (msg.type === 'thought') {
        setRivaThoughts(p => [...p, { ...msg, ts: now() }]);
      } else if (msg.type === 'auto_pause') {
        setRivaPaused(true);
      } else if (msg.type === 'browse_complete') {
        setRivaComplete(true);
      }
    };
    return () => ws.close();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rivaUrl]);

  // Competitor browse WS
  useEffect(() => {
    if (!compUrl) { setCompWsState('closed'); return; }
    const ws = new WebSocket(
      `ws://localhost:8000/ws/browse?session=${sessionId}&role=comp`
    );
    compWsRef.current = ws;
    ws.onopen    = () => { setCompWsState('open'); ws.send(JSON.stringify({ url: compUrl })); };
    ws.onclose   = () => setCompWsState('closed');
    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      if (msg.type === 'frame') {
        setCompFrame(`data:image/jpeg;base64,${msg.data}`);
      } else if (msg.type === 'thought') {
        setCompThoughts(p => [...p, { ...msg, ts: now() }]);
      } else if (msg.type === 'auto_pause') {
        setCompPaused(true);
      } else if (msg.type === 'browse_complete') {
        setCompComplete(true);
      }
    };
    return () => ws.close();
  // eslint-disable-next-line react-hooks/exhaustive-deps
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

  function restartBrowse(url: string, side: 'riva' | 'comp') {
    const wsRef      = side === 'riva' ? rivaWsRef      : compWsRef;
    const setWsState = side === 'riva' ? setRivaWsState : setCompWsState;
    const setThoughts = side === 'riva' ? setRivaThoughts : setCompThoughts;
    const setFrame   = side === 'riva' ? setRivaFrame   : setCompFrame;
    const setPaused  = side === 'riva' ? setRivaPaused  : setCompPaused;
    const setComplete = side === 'riva' ? setRivaComplete : setCompComplete;

    wsRef.current?.close();
    setComplete(false);
    setPaused(false);
    setThoughts([]);
    setFrame('');

    const ws = new WebSocket(
      `ws://localhost:8000/ws/browse?session=${sessionId}&role=${side}`
    );
    wsRef.current = ws;
    setWsState('connecting');

    ws.onopen  = () => { setWsState('open'); ws.send(JSON.stringify({ url })); };
    ws.onclose = () => setWsState('closed');
    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      if (msg.type === 'frame')          setFrame(`data:image/jpeg;base64,${msg.data}`);
      else if (msg.type === 'thought')   setThoughts(p => [...p, { ...msg, ts: now() }]);
      else if (msg.type === 'auto_pause') setPaused(true);
      else if (msg.type === 'browse_complete') setComplete(true);
    };
  }

  function sendChat(text: string) {
    setChatMessages(p => [...p, { role: 'user', text, ts: now() }]);
    sendToWs(pipelineWsRef, { type: 'chat', text });
  }

  const pipelineReady = pipelineStatus === 'ready';

  return (
    <div className="h-screen flex flex-col bg-[#050a15] text-white overflow-hidden">
      {/* Header */}
      <header className="flex items-center justify-between px-6 py-3 border-b border-white/5 bg-[#030810] shrink-0">
        <button onClick={() => router.push('/')} className="flex items-center gap-3">
          <Shield size={20} className="text-[#00ffff]" />
          <span className="font-bold tracking-[8px] text-lg uppercase">RIVA</span>
        </button>
        <div className="flex items-center gap-4 text-[10px] font-mono">
          {rivaUrl && <span className="text-[#00ffff]/40 truncate max-w-[200px]">{rivaUrl}</span>}
          {rivaUrl && compUrl && <span className="text-white/20">vs</span>}
          {compUrl && <span className="text-[#ff3333]/40 truncate max-w-[200px]">{compUrl}</span>}
        </div>
        <div className="flex items-center gap-4 text-[10px] font-bold">
          {rivaUrl && (
            <div className="flex items-center gap-1.5">
              <div className={`w-2 h-2 rounded-full ${rivaWsState === 'open' ? 'bg-green-500 shadow-[0_0_8px_#22c55e]' : 'bg-red-500'}`} />
              <span className="text-white/30">RIVA</span>
            </div>
          )}
          {compUrl && (
            <div className="flex items-center gap-1.5">
              <div className={`w-2 h-2 rounded-full ${compWsState === 'open' ? 'bg-green-500 shadow-[0_0_8px_#22c55e]' : 'bg-red-500'}`} />
              <span className="text-white/30">COMP</span>
            </div>
          )}
          <div className="flex items-center gap-1.5">
            <div className={`w-2 h-2 rounded-full ${pipelineWsState === 'open' ? 'bg-cyan-500 shadow-[0_0_8px_#06b6d4]' : 'bg-white/10'}`} />
            <span className="text-white/30">PIPELINE</span>
          </div>
        </div>
      </header>

      {/* Main content */}
      <div className="flex-1 flex flex-col min-h-0">

        {/* Browsers row */}
        <div className="flex-1 flex min-h-0">
          {rivaUrl && (
            <div className="flex flex-col flex-1 border-r border-white/5 min-w-0">
              <div className="px-4 py-2 text-[10px] font-bold tracking-[4px] bg-cyan-500/5 text-[#00ffff] border-b border-white/5 shrink-0">
                ◈ YOUR SITE
              </div>
              <ThoughtLog thoughts={rivaThoughts} label="RIVA" color="#00ffff" />
              <BrowserPanel
                label="RIVA" color="#00ffff" frameSrc={rivaFrame} active
                isPaused={rivaPaused} isComplete={rivaComplete}
                wsActive={rivaWsState === 'open'}
                onTogglePause={() => togglePause('riva')}
                onInteraction={(t: string, x: number, y: number) =>
                  sendToWs(rivaWsRef, { type: t, x, y })}
                onGoto={(url: string) => sendToWs(rivaWsRef, { type: 'goto', url })}
                onRestart={(url: string) => restartBrowse(url, 'riva')}
              />
            </div>
          )}

          {compUrl && (
            <div className="flex flex-col flex-1 bg-black/20 min-w-0">
              <div className="px-4 py-2 text-[10px] font-bold tracking-[4px] bg-red-500/5 text-[#ff3333] border-b border-white/5 shrink-0">
                ◈ COMPETITOR
              </div>
              <ThoughtLog thoughts={compThoughts} label="COMP" color="#ff3333" />
              <BrowserPanel
                label="COMP" color="#ff3333" frameSrc={compFrame} active
                isPaused={compPaused} isComplete={compComplete}
                wsActive={compWsState === 'open'}
                onTogglePause={() => togglePause('comp')}
                onInteraction={(t: string, x: number, y: number) =>
                  sendToWs(compWsRef, { type: t, x, y })}
                onGoto={(url: string) => sendToWs(compWsRef, { type: 'goto', url })}
                onRestart={(url: string) => restartBrowse(url, 'comp')}
              />
            </div>
          )}
        </div>

        {/* Pipeline log */}
        <PipelineLogPanel
          logs={pipelineLogs}
          status={pipelineStatus}
          reportReady={!!reportUrl}
          onOpenChat={() => setChatOpen(true)}
          onViewReport={() => setReportModalOpen(true)}
        />
      </div>

      {/* Chat drawer */}
      {chatOpen && (
        <ChatDrawer
          messages={chatMessages}
          onSend={sendChat}
          onClose={() => setChatOpen(false)}
          pipelineReady={pipelineReady}
        />
      )}

      {/* Report modal */}
      {reportModalOpen && reportUrl && reportType && (
        <ReportModal
          reportUrl={reportUrl}
          reportType={reportType as 'pdf' | 'pptx'}
          onClose={() => setReportModalOpen(false)}
        />
      )}
    </div>
  );
}
