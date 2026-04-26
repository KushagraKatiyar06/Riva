'use client';

import { useEffect, useRef, useState, useMemo, Suspense } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';
import { Play, Pause, ArrowRight, Send, Download, Maximize2, Minimize2 } from 'lucide-react';

const API_BASE = (process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000').replace(/\/$/, '');
const WS_BASE  = API_BASE.replace(/^http/, 'ws');

type Thought     = { text: string; state: string; ts: string };
type PipelineLog = { text: string; state: string; ts: string };
type ChatMsg     = { role: 'assistant' | 'user'; text: string; ts: string };

const STATE_COLORS: Record<string, string> = {
  info:       '#8a8a9e',
  navigating: '#6eb0e8',
  scanning:   '#c8a84a',
  found:      '#6dc08a',
  hovering:   '#9b7fd4',
  clicking:   '#d4874a',
  hitl:       '#d45070',
  complete:   '#6dc08a',
  error:      '#d46060',
  user:       '#e8e0f0',
  success:    '#6dc08a',
};

const PIPELINE_STATE_COLORS: Record<string, string> = {
  info:    '#7a7a8e',
  success: '#6eb0c8',
  error:   '#d46060',
};

function now() {
  return new Date().toLocaleTimeString('en-US', {
    hour12: false, minute: '2-digit', second: '2-digit',
  });
}

// ---------------------------------------------------------------------------
// SmallEye — animated eye synced to agent state
// ---------------------------------------------------------------------------
function SmallEye({ color, bgFill, darkFill, lidFill, glowId, size = 44, agentState = 'info' }: {
  color: string;
  bgFill: string;
  darkFill: string;
  lidFill: string;
  glowId: string;
  size?: number;
  agentState?: string;
}) {
  const pupilRef = useRef<SVGGElement>(null);
  const lidRef   = useRef<SVGRectElement>(null);
  const modeRef  = useRef<string>('idle');

  // Sync mode to agent state
  useEffect(() => {
    if (['navigating', 'scanning', 'hovering', 'clicking'].includes(agentState)) {
      modeRef.current = 'thinking';
    } else if (['found', 'success'].includes(agentState)) {
      modeRef.current = 'found';
    } else if (agentState === 'complete') {
      modeRef.current = 'complete';
      // Rapid blink sequence then calm
      let count = 0;
      const rapidBlink = () => {
        if (count >= 8 || !lidRef.current) return;
        lidRef.current.style.transform = 'translateY(0%)';
        setTimeout(() => { if (lidRef.current) lidRef.current.style.transform = 'translateY(-100%)'; }, 90);
        count++;
        setTimeout(rapidBlink, 190);
      };
      setTimeout(rapidBlink, 150);
    } else if (agentState === 'hitl' || agentState === 'error') {
      modeRef.current = 'alert';
    } else {
      modeRef.current = 'idle';
    }
  }, [agentState]);

  // Animation loop
  useEffect(() => {
    let alive = true;

    function blink() {
      if (!lidRef.current) return;
      lidRef.current.style.transform = 'translateY(0%)';
      setTimeout(() => { if (lidRef.current) lidRef.current.style.transform = 'translateY(-100%)'; }, 130);
    }

    function setGlow(r: number) {
      const el = document.getElementById(glowId) as SVGCircleElement | null;
      if (el) el.setAttribute('r', String(r));
    }

    function loop() {
      if (!alive || !pupilRef.current) return;
      const mode = modeRef.current;

      if (mode === 'thinking') {
        // Fast erratic looking-around
        const x = (Math.random() - 0.5) * 46;
        const y = (Math.random() - 0.5) * 24;
        pupilRef.current.style.transform = `translate(${x}px, ${y}px)`;
        setGlow(Math.random() > 0.4 ? 4 : 2.5);
        setTimeout(loop, Math.random() * 350 + 120);
      } else if (mode === 'found') {
        // Dilated, slow gentle drift
        const x = (Math.random() - 0.5) * 8;
        const y = (Math.random() - 0.5) * 5;
        pupilRef.current.style.transform = `translate(${x}px, ${y}px)`;
        setGlow(11); // big dilation
        setTimeout(loop, Math.random() * 2500 + 1800);
      } else if (mode === 'complete') {
        // Centered, small pupil, very slow
        pupilRef.current.style.transform = 'translate(0px, 0px)';
        setGlow(3);
        setTimeout(loop, 5000);
      } else if (mode === 'alert') {
        // Darting left-right
        const x = (Math.random() - 0.5) * 22;
        pupilRef.current.style.transform = `translate(${x}px, 2px)`;
        setGlow(3);
        setTimeout(loop, Math.random() * 250 + 150);
      } else {
        // Idle: gentle drifting
        const x = (Math.random() - 0.5) * 28;
        const y = (Math.random() - 0.5) * 14;
        pupilRef.current.style.transform = `translate(${x}px, ${y}px)`;
        setGlow(Math.random() > 0.5 ? 5.5 : 3);
        if (Math.random() > 0.82) blink();
        setTimeout(loop, Math.random() * 2400 + 900);
      }
    }

    loop();
    return () => { alive = false; };
  }, [glowId]);

  return (
    <svg viewBox="0 0 80 48" style={{ width: size, overflow: 'visible', flexShrink: 0 }}>
      <defs>
        <clipPath id={`clip-${glowId}`}>
          <path d="M3,24 Q40,-7 77,24 Q40,55 3,24" />
        </clipPath>
      </defs>
      <g clipPath={`url(#clip-${glowId})`}>
        <path d="M3,24 Q40,-7 77,24 Q40,55 3,24" fill={bgFill} />
        <g ref={pupilRef} style={{ transition: 'transform 0.3s cubic-bezier(0.175,0.885,0.32,1.275)' }}>
          <circle cx="40" cy="24" r="14" fill={darkFill} />
          <circle id={glowId} cx="40" cy="24" r="4.5" fill={color}
            style={{ filter: `drop-shadow(0 0 8px ${color})`, transition: 'r 0.35s ease' }} />
        </g>
        <rect ref={lidRef} width="80" height="48" fill={lidFill}
          style={{ transform: 'translateY(-100%)', transition: 'transform 0.11s ease-in-out' }} />
      </g>
      <path d="M3,24 Q40,-7 77,24" fill="none" stroke={color} strokeWidth="1.2" opacity="0.3" />
    </svg>
  );
}

// ---------------------------------------------------------------------------
// MiniLog — eye in header, synced to agent, shared expand state
// ---------------------------------------------------------------------------
function MiniLog({
  thoughts, color, accentColor, domain, eyeBgFill, eyeDarkFill, eyeLidFill, glowId,
  expanded, onToggle,
}: {
  thoughts: Thought[];
  color: string;
  accentColor: string;
  domain: string;
  eyeBgFill: string;
  eyeDarkFill: string;
  eyeLidFill: string;
  glowId: string;
  expanded: boolean;
  onToggle: () => void;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (expanded && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [thoughts, expanded]);

  const lastThought = thoughts[thoughts.length - 1];
  const agentState  = lastThought?.state || 'info';

  return (
    <div className="shrink-0 overflow-hidden"
      style={{
        height: expanded ? 200 : 54,
        transition: 'height 0.25s ease',
        background: 'rgba(0,0,0,0.28)',
      }}>
      <div className="flex items-center justify-between px-2.5 shrink-0"
        style={{
          height: 54,
          background: 'rgba(0,0,0,0.22)',
          borderBottom: expanded ? `1px solid ${accentColor}22` : 'none',
        }}>
        <div className="flex items-center gap-3">
          <SmallEye color={color} bgFill={eyeBgFill} darkFill={eyeDarkFill}
            lidFill={eyeLidFill} glowId={glowId} size={52} agentState={agentState} />
          {domain && (
            <span style={{ color: 'rgba(255,255,255,0.75)', fontSize: 10,
              fontFamily: "'DM Sans', system-ui, sans-serif", letterSpacing: '0.5px', fontWeight: 500 }}>
              {domain}
            </span>
          )}
        </div>
        <button onClick={onToggle} style={{ color: 'rgba(255,255,255,0.3)' }}
          className="hover:text-white/60 transition-colors p-2">
          {expanded ? <Minimize2 size={11} /> : <Maximize2 size={11} />}
        </button>
      </div>

      {expanded && (
        <div ref={scrollRef} className="overflow-y-auto px-3 py-2 space-y-0.5"
          style={{ height: 146, fontFamily: "'Fira Code', monospace" }}>
          {thoughts.length === 0 && (
            <div style={{ fontSize: 10, color: '#3a3a4e' }}>waiting...</div>
          )}
          {thoughts.map((t, i) => (
            <div key={i} style={{ display: 'flex', gap: 8, fontSize: 10, lineHeight: '19px' }}>
              <span style={{ flexShrink: 0, color: 'rgba(255,255,255,0.13)' }}>[{t.ts}]</span>
              <span style={{ color: STATE_COLORS[t.state] ?? '#9a9ab0' }}>{t.text}</span>
            </div>
          ))}
        </div>
      )}

      {!expanded && lastThought && (
        <div style={{ padding: '0 12px', display: 'flex', gap: 8, fontSize: 10,
          lineHeight: '19px', fontFamily: "'Fira Code', monospace",
          position: 'absolute', bottom: 0, left: 0, right: 0, height: 0, overflow: 'visible' }}>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// BrowserPanel
// ---------------------------------------------------------------------------
function BrowserPanel({
  label, frameSrc, active, isPaused, isComplete, wsActive,
  stuckMilestone, canSkip, onTogglePause, onSkip, onInteraction, onGoto, onRestart,
}: any) {
  const [manualUrl, setManualUrl] = useState('');

  function handleClick(e: any) {
    if (!active || !frameSrc) return;
    const rect = e.currentTarget.getBoundingClientRect();
    onInteraction?.('click', (e.clientX - rect.left) / rect.width, (e.clientY - rect.top) / rect.height);
  }

  function submitGoto() {
    if (!manualUrl.trim()) return;
    if (!wsActive) { onRestart?.(manualUrl.trim()); } else { onGoto?.(manualUrl.trim()); }
    setManualUrl('');
  }

  return (
    <div className="flex flex-col flex-1 overflow-hidden" style={{ background: 'rgba(0,0,0,0.35)' }}>
      <div className="flex items-center gap-2 px-3 py-2 border-b border-white/5 shrink-0"
        style={{ background: 'rgba(0,0,0,0.22)' }}>
        {isComplete ? (
          <div className="flex items-center gap-1.5 px-3 py-1 rounded shrink-0"
            style={{ background: 'rgba(109,192,138,0.1)', color: '#6dc08a',
              border: '1px solid rgba(109,192,138,0.22)', fontSize: 9, fontWeight: 500,
              letterSpacing: '1.5px', fontFamily: "'DM Sans', sans-serif" }}>
            ✓ complete
          </div>
        ) : (
          <>
            <button onClick={onTogglePause}
              className="flex items-center gap-1.5 px-3 py-1 rounded shrink-0 transition-all"
              style={{
                background: isPaused ? 'rgba(109,192,138,0.15)' : 'rgba(255,255,255,0.92)',
                color:      isPaused ? '#6dc08a' : '#140e28',
                border:     `1px solid ${isPaused ? 'rgba(109,192,138,0.5)' : 'rgba(255,255,255,0.8)'}`,
                fontSize: 9, fontWeight: 700, letterSpacing: '1.5px',
                fontFamily: "'DM Sans', sans-serif",
              }}>
              {isPaused ? <Play size={9} fill="currentColor" /> : <Pause size={9} fill="currentColor" />}
              {isPaused ? 'resume' : 'pause'}
            </button>
            <button onClick={canSkip ? onSkip : undefined}
              className="flex items-center gap-1.5 px-3 py-1 rounded shrink-0 transition-all"
              disabled={!canSkip}
              style={{
                background: 'rgba(255,255,255,0.92)',
                color: '#140e28',
                border: '1px solid rgba(255,255,255,0.8)',
                fontSize: 9, fontWeight: 700, letterSpacing: '1.5px',
                fontFamily: "'DM Sans', sans-serif",
                opacity: canSkip ? 1 : 0.35,
                cursor: canSkip ? 'pointer' : 'not-allowed',
              }}>
              <ArrowRight size={9} />
              skip
            </button>
          </>
        )}
        <div className="flex-1 flex gap-2">
          <input className="flex-1 rounded px-2 py-1 outline-none"
            style={{
              background: 'rgba(0,0,0,0.4)',
              border: stuckMilestone && !isComplete
                ? '1px solid rgba(200,168,74,0.8)'
                : '1px solid rgba(255,255,255,0.1)',
              color: 'rgba(255,255,255,0.9)',
              fontSize: 9, fontFamily: "'Fira Code', monospace",
              boxShadow: stuckMilestone && !isComplete
                ? '0 0 8px rgba(200,168,74,0.25)' : 'none',
            }}
            placeholder={stuckMilestone && !isComplete
              ? `paste the ${stuckMilestone} url here and press enter...`
              : wsActive ? 'navigate to url...' : 'open url in new browser...'}
            value={manualUrl}
            onChange={e => setManualUrl(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); submitGoto(); } }}
          />
          <button onClick={submitGoto} className="p-1 shrink-0 transition-colors"
            style={{ color: 'rgba(255,255,255,0.3)' }}
            onMouseEnter={e => { (e.currentTarget as HTMLElement).style.color = 'rgba(255,255,255,0.7)'; }}
            onMouseLeave={e => { (e.currentTarget as HTMLElement).style.color = 'rgba(255,255,255,0.3)'; }}>
            <ArrowRight size={12} />
          </button>
        </div>
      </div>

      <div className="relative flex-1 bg-black flex items-center justify-center overflow-hidden"
        onClick={handleClick}
        style={{ cursor: active && frameSrc && !isComplete ? 'crosshair' : 'default' }}>
        {frameSrc ? (
          <img src={frameSrc} className="w-full h-full object-contain pointer-events-none select-none"
            style={{ opacity: isComplete ? 0.35 : 1, filter: isComplete ? 'grayscale(0.5)' : 'none' }}
            alt="preview" />
        ) : (
          <div style={{ color: 'rgba(255,255,255,0.06)', fontSize: 10, letterSpacing: '3px',
            fontFamily: "'DM Sans', sans-serif" }}>
            {label} offline
          </div>
        )}
        {isPaused && !isComplete && (
          <div className="absolute inset-0 pointer-events-none"
            style={{ border: '2px solid rgba(109,192,138,0.15)',
              boxShadow: 'inset 0 0 60px rgba(109,192,138,0.04)' }} />
        )}
        {stuckMilestone && !isComplete && (
          <div className="absolute bottom-0 left-0 right-0 px-4 py-4 flex flex-col gap-2"
            style={{ background: 'linear-gradient(to top, rgba(0,0,0,0.95) 80%, transparent)' }}>
            <div style={{ fontSize: 10, fontWeight: 600, color: '#c8a84a', letterSpacing: '1.5px',
              fontFamily: "'DM Sans', sans-serif" }}>
              ⚠ couldn&apos;t find {stuckMilestone}
            </div>
            <div style={{ fontSize: 10, color: 'rgba(255,255,255,0.7)', lineHeight: '18px',
              fontFamily: "'DM Sans', sans-serif" }}>
              Navigate to the{' '}
              <span style={{ color: '#c8a84a', fontWeight: 600 }}>{stuckMilestone}</span>{' '}
              page yourself, copy the URL, and paste it into the highlighted bar above — the agent will take it from there.
            </div>
          </div>
        )}
        {isComplete && frameSrc && (
          <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
            <div className="flex flex-col items-center gap-2 px-6 py-4 rounded-xl"
              style={{ background: 'rgba(0,0,0,0.55)', border: '1px solid rgba(109,192,138,0.2)' }}>
              <div style={{ color: '#6dc08a', fontSize: 11, fontWeight: 500, letterSpacing: '2.5px',
                fontFamily: "'DM Sans', sans-serif" }}>
                ✓ scraping complete
              </div>
              <div style={{ color: 'rgba(255,255,255,0.3)', fontSize: 9, fontFamily: "'Fira Code', monospace" }}>
                browser closed
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// InferenceLogPanel
// ---------------------------------------------------------------------------
function InferenceLogPanel({
  logs, status, hasReports, bottomExpanded, onToggleBottom,
}: {
  logs: PipelineLog[];
  status: string;
  hasReports: boolean;
  bottomExpanded: boolean;
  onToggleBottom: () => void;
}) {
  const bottomRef = useRef<HTMLDivElement>(null);
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [logs]);

  const statusColor = status === 'ready' ? '#6eb0c8' : status === 'vectorizing' ? '#c8a84a' : '#4a4a58';

  return (
    <div className="flex flex-col h-full border-r border-white/5"
      style={{ background: 'rgba(0,0,0,0.28)', fontFamily: "'Fira Code', monospace" }}>
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-white/5 shrink-0"
        style={{ background: 'rgba(0,0,0,0.18)' }}>
        <div className="flex items-center gap-2">
          <span style={{ color: '#c8a84a', fontSize: 9, fontWeight: 500, letterSpacing: '2px',
            fontFamily: "'DM Sans', sans-serif" }}>
            Inference Logs
          </span>
          <span style={{ background: statusColor + '1a', color: statusColor, fontSize: 8,
            fontWeight: 500, padding: '1px 6px', borderRadius: 3, letterSpacing: '0.5px',
            fontFamily: "'DM Sans', sans-serif" }}>
            {status}
          </span>
        </div>
        <div className="flex items-center gap-2">
          {hasReports && (
            <div className="flex items-center gap-1 px-2 py-0.5 rounded"
              style={{ background: 'rgba(109,192,138,0.08)', color: '#6dc08a',
                border: '1px solid rgba(109,192,138,0.2)', fontSize: 8,
                fontFamily: "'DM Sans', sans-serif" }}>
              ↓ report ready
            </div>
          )}
          <button onClick={onToggleBottom} style={{ color: 'rgba(255,255,255,0.25)' }}
            className="hover:text-white/60 transition-colors p-0.5">
            {bottomExpanded ? <Minimize2 size={11} /> : <Maximize2 size={11} />}
          </button>
        </div>
      </div>
      <div className="flex-1 overflow-y-auto px-3 py-2 space-y-0.5 min-h-0">
        {logs.length === 0 && (
          <div style={{ fontSize: 10, color: '#2e2e3e' }}>
            will vectorize each domain as agents complete...
          </div>
        )}
        {logs.map((l, i) => (
          <div key={i} style={{ display: 'flex', gap: 8, fontSize: 10, lineHeight: '19px' }}>
            <span style={{ flexShrink: 0, color: 'rgba(255,255,255,0.2)' }}>[{l.ts}]</span>
            <span style={{ color: PIPELINE_STATE_COLORS[l.state] ?? '#6a6a7e' }}>{l.text}</span>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// InlineChatPanel
// ---------------------------------------------------------------------------
function InlineChatPanel({
  messages, onSend, pipelineReady, pipelineStatus, bottomExpanded, onToggleBottom,
}: {
  messages: ChatMsg[];
  onSend: (text: string) => void;
  pipelineReady: boolean;
  pipelineStatus: string;
  bottomExpanded: boolean;
  onToggleBottom: () => void;
}) {
  const [input, setInput] = useState('');
  const bottomRef = useRef<HTMLDivElement>(null);
  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages]);

  // Map pipeline status to eye state
  const chatEyeState = pipelineStatus === 'vectorizing' ? 'scanning'
    : pipelineStatus === 'ready' ? 'found'
    : 'info';

  function send() {
    if (!input.trim() || !pipelineReady) return;
    onSend(input.trim());
    setInput('');
  }

  return (
    <div className="flex flex-col h-full" style={{ background: 'rgba(0,0,0,0.18)' }}>
      <div className="flex items-center justify-between px-2.5 py-1.5 border-b border-white/5 shrink-0"
        style={{ background: 'rgba(0,0,0,0.18)' }}>
        <div className="flex items-center gap-2">
          <SmallEye color="#4db8ff" bgFill="#e0f7fa" darkFill="#001a2e" lidFill="#1a0d40"
            glowId="chat-eye-glow" size={40} agentState={chatEyeState} />
          <span style={{ color: 'rgba(255,255,255,0.8)', fontSize: 10, fontWeight: 500,
            letterSpacing: '0.5px', fontFamily: "'DM Sans', sans-serif" }}>
            Riva Chat
          </span>
        </div>
        <button onClick={onToggleBottom} style={{ color: 'rgba(255,255,255,0.25)' }}
          className="hover:text-white/60 transition-colors p-0.5">
          {bottomExpanded ? <Minimize2 size={11} /> : <Maximize2 size={11} />}
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-3 py-2 space-y-2.5 min-h-0"
        style={{ fontFamily: "'Fira Code', monospace" }}>
        {messages.length === 0 && (
          <div style={{ fontSize: 10, color: '#2e2e3e', lineHeight: '19px' }}>
            pipeline will message you when analysis is complete.
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`flex flex-col gap-0.5 ${m.role === 'user' ? 'items-end' : 'items-start'}`}>
            <div style={{ fontSize: 9, fontWeight: 500, letterSpacing: '1px',
              color: m.role === 'assistant' ? '#9580c8' : 'rgba(255,255,255,0.4)',
              fontFamily: "'DM Sans', sans-serif" }}>
              {m.role === 'assistant' ? 'riva' : 'you'}
            </div>
            <div style={{
              maxWidth: 260, padding: '5px 11px', borderRadius: 6, fontSize: 10,
              lineHeight: '18px', whiteSpace: 'pre-wrap',
              ...(m.role === 'assistant'
                ? { background: 'rgba(149,128,200,0.09)', color: '#cec0e8', border: '1px solid rgba(149,128,200,0.14)' }
                : { background: 'rgba(77,184,255,0.07)', color: 'rgba(255,255,255,0.9)', border: '1px solid rgba(77,184,255,0.12)' })
            }}>
              {m.text}
            </div>
            <div style={{ fontSize: 8, color: 'rgba(255,255,255,0.15)' }}>{m.ts}</div>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>

      <div className="px-3 py-2 border-t border-white/5 shrink-0">
        {!pipelineReady && (
          <div style={{ fontSize: 9, color: '#3a3a4e', marginBottom: 5, fontFamily: "'DM Sans', sans-serif" }}>
            available after vectorization
          </div>
        )}
        <div className="flex gap-2">
          <input className="flex-1 rounded px-3 py-1.5 outline-none"
            style={{ background: 'rgba(0,0,0,0.4)', border: '1px solid rgba(255,255,255,0.1)',
              color: 'rgba(255,255,255,0.9)', fontSize: 10, fontFamily: "'Fira Code', monospace" }}
            placeholder={pipelineReady ? 'ask something...' : 'waiting...'}
            value={input}
            disabled={!pipelineReady}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); send(); } }}
          />
          <button onClick={send} disabled={!pipelineReady || !input.trim()}
            className="p-1.5 rounded transition-all"
            style={{
              background: pipelineReady && input.trim() ? 'rgba(149,128,200,0.2)' : 'rgba(255,255,255,0.04)',
              color:      pipelineReady && input.trim() ? '#9580c8' : 'rgba(255,255,255,0.15)',
            }}>
            <Send size={12} />
          </button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Report modal
// ---------------------------------------------------------------------------
type ReportEntry = { type: 'pdf' | 'pptx'; url: string; previewUrl: string; filename: string };

function ReportPreviewSection({ reports }: { reports: ReportEntry[] }) {
  const [minimized, setMinimized] = useState(false);

  if (reports.length === 0) return null;

  return (
    <div className="shrink-0 rounded-lg overflow-hidden"
      style={{ border: '1px solid rgba(149,128,200,0.2)', background: '#0a0820',
        marginBottom: 12, transition: 'all 0.25s ease' }}>
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 border-b"
        style={{ borderColor: 'rgba(149,128,200,0.12)', background: 'rgba(0,0,0,0.3)' }}>
        <div className="flex items-center gap-3">
          <span style={{ fontSize: 9, fontWeight: 700, letterSpacing: '2px', color: '#9580c8',
            fontFamily: "'DM Sans', sans-serif", textTransform: 'uppercase' }}>
            Reports
          </span>
          <span style={{ fontSize: 9, color: 'rgba(255,255,255,0.25)',
            fontFamily: "'DM Sans', sans-serif" }}>
            {reports.length} ready
          </span>
        </div>
        <button onClick={() => setMinimized(v => !v)}
          className="p-1 transition-colors"
          style={{ color: 'rgba(255,255,255,0.3)' }}>
          {minimized ? <Maximize2 size={11} /> : <Minimize2 size={11} />}
        </button>
      </div>

      {!minimized && (
        <div className="flex" style={{ height: 560, gap: 1 }}>
          {reports.map((r, i) => {
            const fullUrl    = `${API_BASE}${r.url}`;
            const previewUrl = `${API_BASE}${r.previewUrl}`;
            return (
              <div key={i} className="flex flex-col flex-1 min-w-0"
                style={{ borderRight: i < reports.length - 1 ? '1px solid rgba(255,255,255,0.06)' : 'none' }}>
                {/* Per-report header */}
                <div className="flex items-center justify-between px-3 py-1.5 shrink-0"
                  style={{ background: 'rgba(0,0,0,0.2)', borderBottom: '1px solid rgba(255,255,255,0.05)' }}>
                  <span style={{ fontSize: 9, color: 'rgba(255,255,255,0.45)',
                    fontFamily: "'DM Sans', sans-serif", letterSpacing: '1px' }}>
                    {r.type === 'pdf' ? 'one-pager report' : 'powerpoint deck'}
                  </span>
                  <div className="flex items-center gap-2">
                    <a href={previewUrl} target="_blank" rel="noreferrer"
                      className="flex items-center gap-1 px-2 py-0.5 rounded transition-all"
                      style={{ background: 'rgba(149,128,200,0.08)', color: '#9580c8',
                        border: '1px solid rgba(149,128,200,0.18)', fontSize: 8,
                        fontFamily: "'DM Sans', sans-serif", textDecoration: 'none' }}>
                      open tab
                    </a>
                    <a href={fullUrl} download={r.filename}
                      className="flex items-center gap-1 px-2 py-0.5 rounded transition-all"
                      style={{ background: 'rgba(109,192,138,0.08)', color: '#6dc08a',
                        border: '1px solid rgba(109,192,138,0.2)', fontSize: 8,
                        fontFamily: "'DM Sans', sans-serif", textDecoration: 'none' }}>
                      <Download size={8} />
                      download
                    </a>
                  </div>
                </div>
                {/* Preview iframe */}
                <iframe
                  src={previewUrl}
                  className="flex-1 w-full border-0"
                  title={`${r.type} preview`}
                  style={{ background: r.type === 'pdf' ? 'white' : '#050912' }}
                />
              </div>
            );
          })}
        </div>
      )}
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

  const storageKey = `riva-session:${rivaUrl}|${compUrl}`;

  const sessionIdRef = useRef(crypto.randomUUID());

  // All state starts at server-safe defaults (no sessionStorage on server).
  // Saved session is applied in a useEffect after hydration to avoid mismatches.
  const [rivaThoughts,       setRivaThoughts]       = useState<Thought[]>([]);
  const [compThoughts,       setCompThoughts]       = useState<Thought[]>([]);
  const [rivaFrame,          setRivaFrame]          = useState('');
  const [compFrame,          setCompFrame]          = useState('');
  const [rivaPaused,         setRivaPaused]         = useState(false);
  const [compPaused,         setCompPaused]         = useState(false);
  const [rivaComplete,       setRivaComplete]       = useState(false);
  const [compComplete,       setCompComplete]       = useState(false);
  const [isRestoring,        setIsRestoring]        = useState(false);
  const [rivaWsState,        setRivaWsState]        = useState<'connecting'|'open'|'closed'>('connecting');
  const [compWsState,        setCompWsState]        = useState<'connecting'|'open'|'closed'>('connecting');

  // Apply saved session after mount (client-only)
  useEffect(() => {
    if (!rivaUrl && !compUrl) return;
    try {
      const raw = sessionStorage.getItem(storageKey);
      if (!raw) return;
      const saved = JSON.parse(raw);
      if (saved.sessionId) sessionIdRef.current = saved.sessionId;
      if (saved.rivaFrame)      setRivaFrame(saved.rivaFrame);
      if (saved.compFrame)      setCompFrame(saved.compFrame);
      if (saved.rivaComplete)   setRivaComplete(saved.rivaComplete);
      if (saved.compComplete)   setCompComplete(saved.compComplete);
      if (saved.pipelineLogs)   setPipelineLogs(saved.pipelineLogs);
      if (saved.pipelineStatus) setPipelineStatus(saved.pipelineStatus);
      if (saved.reports?.length)  setReports(saved.reports);
      if (saved.chatMessages)   setChatMessages(saved.chatMessages);
      setRivaWsState('closed');
      setCompWsState('closed');
      setIsRestoring(true);
    } catch { /* ignore */ }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const sessionId = sessionIdRef.current;
  const expected = isRestoring ? 0 : (rivaUrl ? 1 : 0) + (compUrl ? 1 : 0);
  const [rivaStuckMilestone, setRivaStuckMilestone] = useState<string | null>(null);
  const [compStuckMilestone, setCompStuckMilestone] = useState<string | null>(null);

  const [pipelineLogs,    setPipelineLogs]    = useState<PipelineLog[]>([]);
  const [pipelineStatus,  setPipelineStatus]  = useState<'waiting'|'vectorizing'|'ready'>('waiting');

  const [reports, setReports] = useState<ReportEntry[]>([]);

  const [chatMessages, setChatMessages] = useState<ChatMsg[]>([]);

  const [rivaCanSkip, setRivaCanSkip] = useState(false);
  const [compCanSkip, setCompCanSkip] = useState(false);

  const [logsExpanded,   setLogsExpanded]   = useState(true);
  const [bottomExpanded, setBottomExpanded] = useState(false);

  useEffect(() => {
    if (!rivaUrl && !compUrl) return;
    try {
      sessionStorage.setItem(storageKey, JSON.stringify({
        sessionId, rivaComplete, compComplete, rivaFrame, compFrame,
        chatMessages, pipelineLogs, pipelineStatus, reports,
      }));
    } catch {}
  }, [sessionId, rivaComplete, compComplete, rivaFrame, compFrame,
      chatMessages, pipelineLogs, pipelineStatus, reports]); // eslint-disable-line react-hooks/exhaustive-deps

  // Check if each domain is already vectorized (enables skip button)
  useEffect(() => {
    function checkDomain(url: string, setter: (v: boolean) => void) {
      if (!url) return;
      try {
        const domain = new URL(url.startsWith('http') ? url : 'https://' + url).hostname
          .replace(/^www\./, '');
        fetch(`${API_BASE}/check-vectorized?domain=${encodeURIComponent(domain)}`)
          .then(r => r.json())
          .then(d => setter(!!d.vectorized))
          .catch(() => {});
      } catch {}
    }
    checkDomain(rivaUrl, setRivaCanSkip);
    checkDomain(compUrl, setCompCanSkip);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rivaUrl, compUrl]);

  const rivaWsRef     = useRef<WebSocket | null>(null);
  const compWsRef     = useRef<WebSocket | null>(null);
  const pipelineWsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    if (!sessionId || expected === 0) return;
    const ws = new WebSocket(`${WS_BASE}/ws/pipeline?session=${sessionId}&expected=${expected}`);
    pipelineWsRef.current = ws;
    ws.onopen  = () => {};
    ws.onclose = () => {};
    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      if (msg.type === 'log')           setPipelineLogs(p => [...p, { text: msg.text, state: msg.state, ts: now() }]);
      else if (msg.type === 'chat')     setChatMessages(p => [...p, { role: 'assistant', text: msg.text, ts: now() }]);
      else if (msg.type === 'status')   setPipelineStatus(msg.value);
      else if (msg.type === 'report_ready') {
        setReports(prev => [...prev, {
          type: msg.report_type as 'pdf' | 'pptx',
          url: msg.url,
          previewUrl: msg.preview_url || msg.url,
          filename: msg.filename,
        }]);
      }
    };
    return () => ws.close();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  useEffect(() => {
    if (isRestoring || !rivaUrl) { setRivaWsState('closed'); return; }
    const ws = new WebSocket(`${WS_BASE}/ws/browse?session=${sessionId}&role=riva`);
    rivaWsRef.current = ws;
    ws.onopen    = () => { setRivaWsState('open'); ws.send(JSON.stringify({ url: rivaUrl })); };
    ws.onclose   = () => setRivaWsState('closed');
    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      if (msg.type === 'frame')               setRivaFrame(`data:image/jpeg;base64,${msg.data}`);
      else if (msg.type === 'thought')        setRivaThoughts(p => [...p, { ...msg, ts: now() }]);
      else if (msg.type === 'auto_pause')     setRivaPaused(true);
      else if (msg.type === 'stuck_guidance') setRivaStuckMilestone(msg.milestone);
      else if (msg.type === 'browse_complete') { setRivaComplete(true); setRivaStuckMilestone(null); }
    };
    return () => ws.close();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rivaUrl]);

  useEffect(() => {
    if (isRestoring || !compUrl) { setCompWsState('closed'); return; }
    const ws = new WebSocket(`${WS_BASE}/ws/browse?session=${sessionId}&role=comp`);
    compWsRef.current = ws;
    ws.onopen    = () => { setCompWsState('open'); ws.send(JSON.stringify({ url: compUrl })); };
    ws.onclose   = () => setCompWsState('closed');
    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      if (msg.type === 'frame')               setCompFrame(`data:image/jpeg;base64,${msg.data}`);
      else if (msg.type === 'thought')        setCompThoughts(p => [...p, { ...msg, ts: now() }]);
      else if (msg.type === 'auto_pause')     setCompPaused(true);
      else if (msg.type === 'stuck_guidance') setCompStuckMilestone(msg.milestone);
      else if (msg.type === 'browse_complete') { setCompComplete(true); setCompStuckMilestone(null); }
    };
    return () => ws.close();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [compUrl]);

  function sendToWs(wsRef: React.RefObject<WebSocket | null>, msg: object) {
    if (wsRef.current?.readyState === WebSocket.OPEN) wsRef.current.send(JSON.stringify(msg));
  }

  function togglePause(side: 'riva' | 'comp') {
    if (side === 'riva') {
      const next = !rivaPaused; setRivaPaused(next);
      sendToWs(rivaWsRef, { type: next ? 'pause' : 'resume' });
    } else {
      const next = !compPaused; setCompPaused(next);
      sendToWs(compWsRef, { type: next ? 'pause' : 'resume' });
    }
  }

  function skipBrowse(side: 'riva' | 'comp') {
    sendToWs(side === 'riva' ? rivaWsRef : compWsRef, { type: 'skip' });
  }

  function restartBrowse(url: string, side: 'riva' | 'comp') {
    const wsRef       = side === 'riva' ? rivaWsRef       : compWsRef;
    const setWsState  = side === 'riva' ? setRivaWsState  : setCompWsState;
    const setThoughts = side === 'riva' ? setRivaThoughts : setCompThoughts;
    const setFrame    = side === 'riva' ? setRivaFrame    : setCompFrame;
    const setPaused   = side === 'riva' ? setRivaPaused   : setCompPaused;
    const setComplete = side === 'riva' ? setRivaComplete : setCompComplete;

    wsRef.current?.close();
    setComplete(false); setPaused(false); setThoughts([]); setFrame('');

    const ws = new WebSocket(`${WS_BASE}/ws/browse?session=${sessionId}&role=${side}`);
    wsRef.current = ws;
    setWsState('connecting');
    ws.onopen  = () => { setWsState('open'); ws.send(JSON.stringify({ url })); };
    ws.onclose = () => setWsState('closed');
    ws.onmessage = (e) => {
      const msg = JSON.parse(e.data);
      if (msg.type === 'frame')           setFrame(`data:image/jpeg;base64,${msg.data}`);
      else if (msg.type === 'thought')    setThoughts(p => [...p, { ...msg, ts: now() }]);
      else if (msg.type === 'auto_pause') setPaused(true);
      else if (msg.type === 'browse_complete') setComplete(true);
    };
  }

  function sendChat(text: string) {
    setChatMessages(p => [...p, { role: 'user', text, ts: now() }]);
    sendToWs(pipelineWsRef, { type: 'chat', text });
  }

  const pipelineReady = pipelineStatus === 'ready';

  function hostname(url: string) {
    try { return new URL(url).hostname; } catch { return url; }
  }

  const RIVA_COLOR = 'rgba(77,184,255,0.55)';
  const COMP_COLOR = 'rgba(255,107,107,0.55)';

  return (
    <>
      <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Fira+Code:wght@400;500&family=DM+Sans:opsz,wght@9..40,300;9..40,400;9..40,500;9..40,600&display=swap" />
      <div className="h-screen flex flex-col overflow-hidden text-white"
        style={{ background: 'linear-gradient(135deg, #0e0d2b 0%, #1a1040 40%, #0d0820 100%)',
          fontFamily: "'DM Sans', system-ui, sans-serif" }}>

        {/* ── Navbar ── */}
        <div className="flex justify-center px-6 pt-4 pb-2 shrink-0">
          <header className="flex items-center justify-between px-5 py-2.5 rounded-full w-full"
            style={{
              maxWidth: '80rem',
              background: 'rgba(255,255,255,0.06)',
              backdropFilter: 'blur(20px)',
              WebkitBackdropFilter: 'blur(20px)',
              border: '1px solid rgba(255,255,255,0.12)',
              boxShadow: '0 4px 24px rgba(0,0,0,0.3)',
            }}>
            <button onClick={() => router.push('/')} className="flex items-center gap-2.5">
              <svg viewBox="0 0 40 24" style={{ width: 26, overflow: 'visible' }}>
                <defs><clipPath id="nav-eye-clip"><path d="M1,12 Q20,-4 39,12 Q20,28 1,12" /></clipPath></defs>
                <g clipPath="url(#nav-eye-clip)">
                  <path d="M1,12 Q20,-4 39,12 Q20,28 1,12" fill="#e0f7fa" />
                  <circle cx="20" cy="12" r="6" fill="#002233" />
                  <circle cx="20" cy="12" r="3" fill="#00ffff" style={{ filter: 'drop-shadow(0 0 4px #00ffff)' }} />
                </g>
              </svg>
              <span style={{ fontWeight: 600, letterSpacing: '6px', fontSize: 14,
                textTransform: 'uppercase', color: '#ffffff' }}>RIVA</span>
            </button>

            <div className="flex items-center gap-3" style={{ fontSize: 10, fontFamily: "'Fira Code', monospace" }}>
              {rivaUrl && <span style={{ color: 'rgba(77,184,255,0.7)' }} className="truncate max-w-[80px] sm:max-w-[160px]">{hostname(rivaUrl)}</span>}
              {rivaUrl && compUrl && <span style={{ color: 'rgba(255,255,255,0.6)', fontSize: 11 }}>vs</span>}
              {compUrl && <span style={{ color: 'rgba(255,107,107,0.7)' }} className="truncate max-w-[80px] sm:max-w-[160px]">{hostname(compUrl)}</span>}
            </div>

            <button
              onClick={() => { try { sessionStorage.removeItem(storageKey); } catch {} router.push('/'); }}
              className="flex items-center gap-2 px-4 py-1.5 rounded-full transition-all"
              style={{ background: 'rgba(212,96,96,0.08)', color: '#e87070',
                border: '1px solid rgba(212,96,96,0.35)', fontSize: 10, fontWeight: 500, letterSpacing: '1.5px' }}>
              End Session
            </button>
          </header>
        </div>

        {/* ── Main ── */}
        <div className="flex-1 flex flex-col min-h-0 px-3">
          <div className="flex-1 flex flex-col min-h-0">

            {/* Mini logs row */}
            {(rivaUrl || compUrl) && (
              <div className="flex gap-2 shrink-0 pt-2 pb-1.5">
                {rivaUrl && (
                  <div className="flex-1 min-w-0 rounded-lg overflow-hidden"
                    style={{ border: `1.5px solid ${RIVA_COLOR}` }}>
                    <MiniLog thoughts={rivaThoughts} color="#4db8ff" accentColor="#4db8ff"
                      domain={hostname(rivaUrl)}
                      eyeBgFill="#e0f7fa" eyeDarkFill="#001a2e" eyeLidFill="#1a0d40"
                      glowId="mini-log-riva" expanded={logsExpanded}
                      onToggle={() => setLogsExpanded(v => !v)} />
                  </div>
                )}
                {compUrl && (
                  <div className="flex-1 min-w-0 rounded-lg overflow-hidden"
                    style={{ border: `1.5px solid ${COMP_COLOR}` }}>
                    <MiniLog thoughts={compThoughts} color="#ff6b6b" accentColor="#ff6b6b"
                      domain={hostname(compUrl)}
                      eyeBgFill="#fbe9e7" eyeDarkFill="#1e0000" eyeLidFill="#200a1a"
                      glowId="mini-log-comp" expanded={logsExpanded}
                      onToggle={() => setLogsExpanded(v => !v)} />
                  </div>
                )}
              </div>
            )}

            {/* Browser panels — stack vertically on small screens */}
            <div className="flex-1 flex flex-col md:flex-row min-h-0 gap-2 pb-2">
              {rivaUrl && (
                <div className="flex flex-col flex-1 min-w-0 overflow-hidden rounded-lg"
                  style={{ border: `1.5px solid ${RIVA_COLOR}` }}>
                  <BrowserPanel label="riva" frameSrc={rivaFrame} active
                    isPaused={rivaPaused} isComplete={rivaComplete} wsActive={rivaWsState === 'open'}
                    stuckMilestone={rivaStuckMilestone} canSkip={rivaCanSkip}
                    onTogglePause={() => togglePause('riva')}
                    onSkip={() => skipBrowse('riva')}
                    onInteraction={(t: string, x: number, y: number) => sendToWs(rivaWsRef, { type: t, x, y })}
                    onGoto={(url: string) => { setRivaStuckMilestone(null); sendToWs(rivaWsRef, { type: 'goto', url }); }}
                    onRestart={(url: string) => restartBrowse(url, 'riva')} />
                </div>
              )}
              {compUrl && (
                <div className="flex flex-col flex-1 min-w-0 overflow-hidden rounded-lg"
                  style={{ border: `1.5px solid ${COMP_COLOR}` }}>
                  <BrowserPanel label="comp" frameSrc={compFrame} active
                    isPaused={compPaused} isComplete={compComplete} wsActive={compWsState === 'open'}
                    stuckMilestone={compStuckMilestone} canSkip={compCanSkip}
                    onTogglePause={() => togglePause('comp')}
                    onSkip={() => skipBrowse('comp')}
                    onInteraction={(t: string, x: number, y: number) => sendToWs(compWsRef, { type: t, x, y })}
                    onGoto={(url: string) => { setCompStuckMilestone(null); sendToWs(compWsRef, { type: 'goto', url }); }}
                    onRestart={(url: string) => restartBrowse(url, 'comp')} />
                </div>
              )}
            </div>
          </div>

          {/* ── Bottom: inference logs + chat ── */}
          <div className="flex flex-col sm:flex-row shrink-0 rounded-lg overflow-hidden mb-3"
            style={{
              height: bottomExpanded ? 400 : 220,
              transition: 'height 0.25s ease',
              border: '1px solid rgba(255,255,255,0.1)',
            }}>
            <div className="flex-1 min-w-0">
              <InferenceLogPanel logs={pipelineLogs} status={pipelineStatus}
                hasReports={reports.length > 0}
                bottomExpanded={bottomExpanded} onToggleBottom={() => setBottomExpanded(v => !v)} />
            </div>
            <div className="flex-1 min-w-0">
              <InlineChatPanel messages={chatMessages} onSend={sendChat}
                pipelineReady={pipelineReady} pipelineStatus={pipelineStatus}
                bottomExpanded={bottomExpanded} onToggleBottom={() => setBottomExpanded(v => !v)} />
            </div>
          </div>

          {reports.length > 0 && (
            <ReportPreviewSection reports={reports} />
          )}
        </div>
      </div>
    </>
  );
}
