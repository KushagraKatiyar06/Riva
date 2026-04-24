'use client';

import { useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';

export default function Home() {
  const router = useRouter();
  const [url, setUrl] = useState('');

  // SVG element refs for animation
  const rivaPupilRef = useRef<SVGGElement>(null);
  const rivaLidRef   = useRef<SVGRectElement>(null);
  const compPupilRef = useRef<SVGGElement>(null);
  const compLidRef   = useRef<SVGRectElement>(null);
  const smileRef     = useRef<SVGPathElement>(null);
  const ctaRef       = useRef<HTMLDivElement>(null);

  // Sentient eye loops
  useEffect(() => {
    let alive = true;

    function blink(lid: SVGRectElement | null) {
      if (!lid) return;
      lid.style.transform = 'translateY(0%)';
      setTimeout(() => { if (lid) lid.style.transform = 'translateY(-100%)'; }, 150);
    }

    function sentientLoop(
      pupil: SVGGElement | null,
      lid: SVGRectElement | null,
      glowEl: SVGCircleElement | null
    ) {
      if (!alive || !pupil) return;
      const x = (Math.random() - 0.5) * 50;
      const y = (Math.random() - 0.5) * 30;
      pupil.style.transform = `translate(${x}px, ${y}px)`;
      if (glowEl) glowEl.setAttribute('r', Math.random() > 0.5 ? '13' : '8');
      if (Math.random() > 0.8) blink(lid);
      setTimeout(
        () => sentientLoop(pupil, lid, glowEl),
        Math.random() * 2000 + 800
      );
    }

    const rivaGlow = document.getElementById('riva-glow-el') as SVGCircleElement | null;
    const compGlow = document.getElementById('comp-glow-el') as SVGCircleElement | null;

    sentientLoop(rivaPupilRef.current, rivaLidRef.current, rivaGlow);
    setTimeout(() => sentientLoop(compPupilRef.current, compLidRef.current, compGlow), 400);

    return () => { alive = false; };
  }, []);

  // Magnetic smile follows cursor
  useEffect(() => {
    function onMove(e: MouseEvent) {
      const cta   = ctaRef.current;
      const smile = smileRef.current;
      if (!cta || !smile) return;
      const rect    = cta.getBoundingClientRect();
      const cx      = rect.left + rect.width  / 2;
      const cy      = rect.top  + rect.height / 2;
      const dist    = Math.hypot(e.clientX - cx, e.clientY - cy);
      if (dist < 400) {
        const mx = (e.clientX - cx) / 12;
        const my = (e.clientY - cy) / 8;
        smile.setAttribute('d', `M20,10 Q${100 + mx},${50 + my} 180,10`);
      } else {
        smile.setAttribute('d', 'M20,10 Q100,50 180,10');
      }
    }
    document.addEventListener('mousemove', onMove);
    return () => document.removeEventListener('mousemove', onMove);
  }, []);

  function scrollToInput() {
    document.getElementById('input-section')?.scrollIntoView({ behavior: 'smooth' });
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!url.trim()) return;
    router.push(`/dashboard?url=${encodeURIComponent(url.trim())}`);
  }

  return (
    <main className="bg-[#050a15] text-white" style={{ fontFamily: "'Inter', -apple-system, sans-serif" }}>

      {/* ── HERO ───────────────────────────────────────────────── */}
      <section
        className="min-h-screen flex flex-col items-center justify-center relative overflow-hidden"
        style={{ background: 'radial-gradient(circle at center, #111d35 0%, #050a15 80%)' }}
      >
        {/* Subtle grid overlay */}
        <div
          className="absolute inset-0 opacity-5"
          style={{
            backgroundImage:
              'linear-gradient(#00ffff 1px, transparent 1px), linear-gradient(90deg, #00ffff 1px, transparent 1px)',
            backgroundSize: '60px 60px',
          }}
        />

        <h1
          className="text-7xl font-thin tracking-[20px] uppercase mb-12 relative"
          style={{ opacity: 0.85, letterSpacing: '20px' }}
        >
          RIVA
        </h1>

        {/* Dual eyes */}
        <div className="flex gap-24 mb-14">
          {/* Riva eye — cyan */}
          <div className="w-64 text-center">
            <svg viewBox="0 0 200 120" overflow="visible">
              <defs>
                <clipPath id="eye-clip-r">
                  <path d="M10,60 Q100,-15 190,60 Q100,135 10,60" />
                </clipPath>
              </defs>
              <g clipPath="url(#eye-clip-r)">
                <path d="M10,60 Q100,-15 190,60 Q100,135 10,60" fill="#e0f7fa" />
                <g ref={rivaPupilRef} style={{ transition: 'transform 0.4s cubic-bezier(0.175,0.885,0.32,1.275)' }}>
                  <circle cx="100" cy="60" r="28" fill="#002233" />
                  <circle
                    id="riva-glow-el"
                    cx="100" cy="60" r="10" fill="#00ffff"
                    style={{ filter: 'drop-shadow(0 0 10px #00ffff)', transition: 'r 0.4s' }}
                  />
                </g>
                <rect
                  ref={rivaLidRef}
                  width="200" height="120" fill="#1a2a4a"
                  style={{ transform: 'translateY(-100%)', transition: 'transform 0.15s ease-in-out' }}
                />
              </g>
              <path d="M10,60 Q100,-15 190,60" fill="none" stroke="#00ffff" strokeWidth="2" opacity="0.25" />
            </svg>
            <p className="mt-3 text-xs font-bold tracking-[4px] uppercase" style={{ color: '#00ffff', textShadow: '0 0 10px #00ffff55' }}>
              YOUR SITE
            </p>
          </div>

          {/* Competitor eye — red */}
          <div className="w-64 text-center">
            <svg viewBox="0 0 200 120" overflow="visible">
              <defs>
                <clipPath id="eye-clip-c">
                  <path d="M10,60 Q100,-15 190,60 Q100,135 10,60" />
                </clipPath>
              </defs>
              <g clipPath="url(#eye-clip-c)">
                <path d="M10,60 Q100,-15 190,60 Q100,135 10,60" fill="#fbe9e7" />
                <g ref={compPupilRef} style={{ transition: 'transform 0.4s cubic-bezier(0.175,0.885,0.32,1.275)' }}>
                  <circle cx="100" cy="60" r="28" fill="#330000" />
                  <circle
                    id="comp-glow-el"
                    cx="100" cy="60" r="10" fill="#ff3333"
                    style={{ filter: 'drop-shadow(0 0 10px #ff3333)', transition: 'r 0.4s' }}
                  />
                </g>
                <rect
                  ref={compLidRef}
                  width="200" height="120" fill="#2a1a1a"
                  style={{ transform: 'translateY(-100%)', transition: 'transform 0.15s ease-in-out' }}
                />
              </g>
              <path d="M10,60 Q100,-15 190,60" fill="none" stroke="#ff3333" strokeWidth="2" opacity="0.25" />
            </svg>
            <p className="mt-3 text-xs font-bold tracking-[4px] uppercase" style={{ color: '#ff3333', textShadow: '0 0 10px #ff333355' }}>
              COMPETITOR
            </p>
          </div>
        </div>

        {/* Magnetic smile CTA */}
        <div
          ref={ctaRef}
          onClick={scrollToInput}
          className="flex flex-col items-center cursor-pointer select-none"
          style={{
            animation: 'breath 4s infinite ease-in-out',
          }}
        >
          <svg viewBox="0 0 200 60" style={{ width: 220, overflow: 'visible' }}>
            <path
              ref={smileRef}
              d="M20,10 Q100,50 180,10"
              fill="none"
              stroke="#00ffff"
              strokeWidth="3"
              strokeLinecap="round"
              style={{ transition: 'stroke 0.2s' }}
            />
          </svg>
          <span
            className="text-xs font-bold tracking-[5px] uppercase mt-2"
            style={{ color: '#00ffff', opacity: 0.7 }}
          >
            Enter Nexus
          </span>
        </div>

        {/* Scroll indicator */}
        <div className="absolute bottom-8 flex flex-col items-center gap-1 opacity-30">
          <div className="w-px h-8 bg-white" style={{ animation: 'pulse 2s infinite' }} />
        </div>
      </section>

      {/* ── INPUT SECTION ──────────────────────────────────────── */}
      <section
        id="input-section"
        className="min-h-screen flex flex-col items-center justify-center px-8"
        style={{ background: 'radial-gradient(circle at center, #0a1628 0%, #050a15 70%)' }}
      >
        <div className="w-full max-w-2xl">
          <div className="mb-2 text-xs font-bold tracking-[6px] uppercase" style={{ color: '#00ffff', opacity: 0.6 }}>
            Step 01
          </div>
          <h2 className="text-4xl font-thin tracking-widest uppercase mb-3">
            Begin Analysis
          </h2>
          <p className="text-sm mb-10" style={{ color: 'rgba(255,255,255,0.4)', letterSpacing: '1px' }}>
            Enter a URL and Riva will autonomously research and synthesize competitive intelligence.
          </p>

          <form onSubmit={handleSubmit} className="flex flex-col gap-4">
            <div
              className="flex items-center border rounded-lg px-4 py-3 gap-3"
              style={{ borderColor: 'rgba(0,255,255,0.25)', background: 'rgba(0,255,255,0.04)' }}
            >
              <span style={{ color: '#00ffff', opacity: 0.5, fontSize: 12, letterSpacing: 2 }}>URL</span>
              <input
                type="text"
                value={url}
                onChange={e => setUrl(e.target.value)}
                placeholder="https://competitor.com"
                className="flex-1 bg-transparent outline-none text-sm"
                style={{ color: 'white', caretColor: '#00ffff' }}
                autoComplete="off"
                spellCheck={false}
              />
            </div>

            <button
              type="submit"
              className="w-full py-3 rounded-lg text-sm font-bold tracking-[4px] uppercase transition-all duration-200"
              style={{
                background: 'linear-gradient(135deg, rgba(0,255,255,0.15) 0%, rgba(0,255,255,0.05) 100%)',
                border: '1px solid rgba(0,255,255,0.4)',
                color: '#00ffff',
                boxShadow: '0 0 20px rgba(0,255,255,0.1)',
              }}
              onMouseEnter={e => {
                (e.currentTarget as HTMLButtonElement).style.boxShadow = '0 0 30px rgba(0,255,255,0.3)';
                (e.currentTarget as HTMLButtonElement).style.borderColor = 'rgba(0,255,255,0.8)';
              }}
              onMouseLeave={e => {
                (e.currentTarget as HTMLButtonElement).style.boxShadow = '0 0 20px rgba(0,255,255,0.1)';
                (e.currentTarget as HTMLButtonElement).style.borderColor = 'rgba(0,255,255,0.4)';
              }}
            >
              Initiate Analysis
            </button>
          </form>

          <p className="mt-6 text-xs" style={{ color: 'rgba(255,255,255,0.2)', letterSpacing: '1px' }}>
            The agent will navigate autonomously. You can intervene at any time via the live preview.
          </p>
        </div>
      </section>

      <style>{`
        @keyframes breath {
          0%, 100% { transform: scale(1); filter: drop-shadow(0 0 5px rgba(0,255,255,0.2)); }
          50%       { transform: scale(1.05); filter: drop-shadow(0 0 15px rgba(0,255,255,0.5)); }
        }
      `}</style>
    </main>
  );
}
