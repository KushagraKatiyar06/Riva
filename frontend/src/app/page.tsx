'use client';

import { useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';

export default function Home() {
  const router = useRouter();
  const [rivaUrl,  setRivaUrl]  = useState('');
  const [compUrl,  setCompUrl]  = useState('');

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
    if (!rivaUrl.trim() && !compUrl.trim()) return;
    const params = new URLSearchParams();
    if (rivaUrl.trim()) params.set('riva', rivaUrl.trim());
    if (compUrl.trim()) params.set('comp', compUrl.trim());
    router.push(`/dashboard?${params.toString()}`);
  }

  return (
    <main className="text-white" style={{ fontFamily: "'Inter', -apple-system, sans-serif" }}>

      {/* ── HERO ───────────────────────────────────────────────── */}
      <section
        className="min-h-screen flex flex-col items-center justify-center relative overflow-hidden"
        style={{ background: 'radial-gradient(ellipse at 50% 40%, #2a1a60 0%, #1a0d40 35%, #0d0820 70%, #060412 100%)' }}
      >
        {/* Subtle grid overlay */}
        <div
          className="absolute inset-0 opacity-[0.04]"
          style={{
            backgroundImage:
              'linear-gradient(rgba(160,120,255,1) 1px, transparent 1px), linear-gradient(90deg, rgba(160,120,255,1) 1px, transparent 1px)',
            backgroundSize: '60px 60px',
          }}
        />

        <div className="flex flex-col items-center relative z-10">
          <h1
            className="text-4xl sm:text-7xl font-thin uppercase mb-2"
            style={{ color: '#c8aaff', textShadow: '0 0 40px rgba(160,100,255,0.4)', letterSpacing: 'clamp(8px, 3vw, 20px)' }}
          >
            RIVA
          </h1>
          <p className="text-xs tracking-[4px] uppercase mb-14" style={{ color: 'rgba(200,170,255,0.4)' }}>
            Agentic Competitive Analysis Tool
          </p>

          {/* Dual eyes */}
          <div className="flex gap-8 sm:gap-24 mb-14">
            {/* Riva eye — cyan */}
            <div className="w-36 sm:w-64 text-center">
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
                    width="200" height="120" fill="#1a0d40"
                    style={{ transform: 'translateY(-100%)', transition: 'transform 0.15s ease-in-out' }}
                  />
                </g>
                <path d="M10,60 Q100,-15 190,60" fill="none" stroke="#4db8ff" strokeWidth="2" opacity="0.4" />
              </svg>
              <p className="mt-3 text-xs font-bold tracking-[4px] uppercase" style={{ color: '#4db8ff', textShadow: '0 0 10px #4db8ff55' }}>
                YOUR SITE
              </p>
            </div>

            {/* Competitor eye — red */}
            <div className="w-36 sm:w-64 text-center">
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
                    width="200" height="120" fill="#200a1a"
                    style={{ transform: 'translateY(-100%)', transition: 'transform 0.15s ease-in-out' }}
                  />
                </g>
                <path d="M10,60 Q100,-15 190,60" fill="none" stroke="#ff6b6b" strokeWidth="2" opacity="0.4" />
              </svg>
              <p className="mt-3 text-xs font-bold tracking-[4px] uppercase" style={{ color: '#ff6b6b', textShadow: '0 0 10px #ff6b6b55' }}>
                COMPETITOR
              </p>
            </div>
          </div>

          {/* Magnetic smile CTA */}
          <div
            ref={ctaRef}
            onClick={scrollToInput}
            className="flex flex-col items-center cursor-pointer select-none"
            style={{ animation: 'breath 4s infinite ease-in-out' }}
          >
            <svg viewBox="0 0 200 60" style={{ width: 220, overflow: 'visible' }}>
              <path
                ref={smileRef}
                d="M20,10 Q100,50 180,10"
                fill="none"
                stroke="#c8aaff"
                strokeWidth="2.5"
                strokeLinecap="round"
                style={{ transition: 'stroke 0.2s' }}
              />
            </svg>
            <span
              className="text-xs font-bold tracking-[5px] uppercase mt-2"
              style={{ color: '#c8aaff', opacity: 0.6 }}
            >
              begin
            </span>
          </div>
        </div>

        {/* Scroll indicator */}
        <div className="absolute bottom-8 flex flex-col items-center gap-1 opacity-20">
          <div className="w-px h-8 bg-white" style={{ animation: 'pulse 2s infinite' }} />
        </div>
      </section>

      {/* ── INPUT SECTION ──────────────────────────────────────── */}
      <section
        id="input-section"
        className="min-h-screen flex flex-col items-center justify-center px-4 sm:px-8"
        style={{ background: 'radial-gradient(ellipse at 50% 50%, #1a1040 0%, #0d0820 50%, #060412 100%)' }}
      >
        <div className="w-full max-w-3xl">
          <p className="text-sm mb-10" style={{ color: 'rgba(200,170,255,0.35)', letterSpacing: '1px' }}>
            Paste two product website links and get autonomous competitive analysis reports and a GTM strategy.
          </p>

          <form onSubmit={handleSubmit} className="flex flex-col gap-4">
            {/* Inputs — side by side on sm+, stacked on mobile */}
            <div className="flex flex-col sm:flex-row gap-4">
              {/* Your site — cyan */}
              <div className="flex-1">
                <div className="mb-1.5 text-[10px] font-bold tracking-[4px] uppercase" style={{ color: '#4db8ff', opacity: 0.6 }}>
                  Your Site
                </div>
                <div
                  className="flex items-center border rounded-lg px-4 py-3 gap-3"
                  style={{ borderColor: 'rgba(77,184,255,0.55)', background: 'rgba(0,0,0,0.45)' }}
                >
                  <span style={{ color: '#4db8ff', opacity: 0.5, fontSize: 12, letterSpacing: 2 }}>URL</span>
                  <input
                    type="text"
                    value={rivaUrl}
                    onChange={e => setRivaUrl(e.target.value)}
                    placeholder="https://yoursite.com"
                    className="flex-1 bg-transparent outline-none text-sm"
                    style={{ color: 'white', caretColor: '#4db8ff' }}
                    autoComplete="off"
                    spellCheck={false}
                  />
                </div>
              </div>

              {/* Competitor — red */}
              <div className="flex-1">
                <div className="mb-1.5 text-[10px] font-bold tracking-[4px] uppercase flex items-center gap-2" style={{ color: '#ff6b6b', opacity: 0.6 }}>
                  Competing Product
                  <span className="text-white/20 normal-case tracking-normal font-normal">(optional)</span>
                </div>
                <div
                  className="flex items-center border rounded-lg px-4 py-3 gap-3"
                  style={{ borderColor: 'rgba(255,107,107,0.55)', background: 'rgba(0,0,0,0.45)' }}
                >
                  <span style={{ color: '#ff6b6b', opacity: 0.5, fontSize: 12, letterSpacing: 2 }}>URL</span>
                  <input
                    type="text"
                    value={compUrl}
                    onChange={e => setCompUrl(e.target.value)}
                    placeholder="https://competitor.com"
                    className="flex-1 bg-transparent outline-none text-sm"
                    style={{ color: 'white', caretColor: '#ff6b6b' }}
                    autoComplete="off"
                    spellCheck={false}
                  />
                </div>
              </div>
            </div>

            <div className="flex justify-center mt-2">
              <button
                type="submit"
                className="px-8 py-2.5 rounded-lg text-sm font-bold tracking-[4px] uppercase transition-all duration-200 flex items-center gap-3"
                style={{
                  background: 'linear-gradient(135deg, rgba(160,100,255,0.15) 0%, rgba(160,100,255,0.05) 100%)',
                  border: '1px solid rgba(160,100,255,0.4)',
                  color: '#c8aaff',
                  boxShadow: '0 0 20px rgba(160,100,255,0.1)',
                  opacity: rivaUrl.trim() || compUrl.trim() ? 1 : 0.4,
                }}
                onMouseEnter={e => {
                  (e.currentTarget as HTMLButtonElement).style.boxShadow = '0 0 30px rgba(160,100,255,0.3)';
                  (e.currentTarget as HTMLButtonElement).style.borderColor = 'rgba(160,100,255,0.8)';
                }}
                onMouseLeave={e => {
                  (e.currentTarget as HTMLButtonElement).style.boxShadow = '0 0 20px rgba(160,100,255,0.1)';
                  (e.currentTarget as HTMLButtonElement).style.borderColor = 'rgba(160,100,255,0.4)';
                }}
              >
                <span style={{ fontSize: 12}}>→</span>
              </button>
            </div>
          </form>
        </div>
      </section>

      <style>{`
        @keyframes breath {
          0%, 100% { transform: scale(1); filter: drop-shadow(0 0 5px rgba(160,100,255,0.2)); }
          50%       { transform: scale(1.05); filter: drop-shadow(0 0 15px rgba(160,100,255,0.5)); }
        }
      `}</style>
    </main>
  );
}
