// Landing page with the URL input form, daily run limit tracking, history
// tab, and admin/clear modals.
'use client';

import { useEffect, useRef, useState, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import { Download, Trash2, RefreshCw, X } from 'lucide-react';

const API_BASE = (process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000').replace(/\/$/, '');
const DAILY_LIMIT = 2;

function getClientId(): string {
  if (typeof window === 'undefined') return '';
  const key = 'riva-client-id';
  let id = localStorage.getItem(key);
  if (!id) {
    id = crypto.randomUUID();
    localStorage.setItem(key, id);
  }
  return id;
}

function clientHeaders() {
  return { 'Content-Type': 'application/json', 'x-client-id': getClientId() };
}

type RunEntry   = { id: string; riva_url: string; comp_url: string; started_at: string };
type ReportFile = { filename: string; size: number; modified: string; url: string; type: 'pdf' };

function formatDate(iso: string) {
  try {
    return new Date(iso).toLocaleString('en-US', {
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hour12: true,
    });
  } catch { return iso; }
}

function formatBytes(n: number) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function hostname(url: string) {
  try { return new URL(url.startsWith('http') ? url : `https://${url}`).hostname.replace(/^www\./, ''); }
  catch { return url; }
}

async function downloadBlob(href: string, filename: string) {
  const resp = await fetch(href);
  const blob = await resp.blob();
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  a.click();
  URL.revokeObjectURL(a.href);
}

type ClearPreview = {
  domains:       { domain: string; files: number; vectors: number }[];
  total_vectors: number;
  report_files:  { filename: string; size: number; type: string }[];
  run_count:     number;
  session_count: number;
  cf_vectors:    number | null;
};

function ClearModal({ onClose, onCleared }: { onClose: () => void; onCleared: () => void }) {
  const [preview,  setPreview]  = useState<ClearPreview | null>(null);
  const [loading,  setLoading]  = useState(true);
  const [password, setPassword] = useState('');
  const [clearing, setClearing] = useState(false);
  const [error,    setError]    = useState('');
  const [done,     setDone]     = useState(false);

  useEffect(() => {
    fetch(`${API_BASE}/clear-preview`)
      .then(r => r.json())
      .then(d => { setPreview(d); setLoading(false); })
      .catch(() => { setError('Could not load preview.'); setLoading(false); });
  }, []);

  async function handleClear() {
    if (!password) { setError('Enter the admin password.'); return; }
    setClearing(true);
    setError('');
    try {
      const resp = await fetch(`${API_BASE}/clear-data`, {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password }),
      });
      if (resp.status === 401) { setError('Incorrect password.'); setClearing(false); return; }
      if (!resp.ok) { setError('Server error — try again.'); setClearing(false); return; }
      const result = await resp.json();
      setDone(true);
      onCleared();
      if (result.index_error) {
        setError(`⚠ Local data cleared, but Cloudflare index wipe failed: ${result.index_error}`);
      }
    } catch {
      setError('Request failed — server may be unreachable.');
      setClearing(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: 'rgba(0,0,0,0.8)', backdropFilter: 'blur(6px)' }}
      onClick={onClose}
    >
      <div
        className="relative w-full max-w-lg rounded-2xl flex flex-col"
        style={{
          background: 'linear-gradient(135deg, #0d0820 0%, #130e2e 100%)',
          border: '1px solid rgba(212,96,96,0.3)',
          boxShadow: '0 0 60px rgba(212,96,96,0.1)',
          maxHeight: '85vh',
        }}
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-6 pt-6 pb-4 shrink-0" style={{ borderBottom: '1px solid rgba(255,255,255,0.06)' }}>
          <div className="flex items-center gap-3">
            <Trash2 size={14} style={{ color: '#d46060' }} />
            <span className="text-xs font-bold tracking-[3px] uppercase" style={{ color: '#d46060' }}>
              Clear All Data
            </span>
          </div>
          <button onClick={onClose} className="opacity-30 hover:opacity-70 transition-opacity" style={{ color: '#c8aaff' }}>
            <X size={14} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-5 min-h-0">
          {loading && (
            <p className="text-xs text-center py-8" style={{ color: 'rgba(200,170,255,0.4)' }}>
              Loading preview…
            </p>
          )}

          {!loading && done && (
            <p className="text-sm text-center py-6" style={{ color: '#6dc08a' }}>
              All data cleared successfully.
            </p>
          )}

          {!loading && !done && preview && (
            <>
              <p className="text-xs mb-4" style={{ color: 'rgba(200,170,255,0.5)' }}>
                The following will be permanently deleted:
              </p>

              {preview.domains.length > 0 ? (
                <div className="mb-4 rounded-xl overflow-hidden" style={{ border: '1px solid rgba(160,100,255,0.15)' }}>
                  <div className="px-4 py-2 flex items-center justify-between" style={{ background: 'rgba(0,0,0,0.3)', borderBottom: '1px solid rgba(160,100,255,0.1)' }}>
                    <span className="text-[10px] font-bold tracking-[2px] uppercase" style={{ color: 'rgba(200,170,255,0.5)' }}>
                      Indexed domains
                    </span>
                    <span className="text-[10px]" style={{ color: 'rgba(200,170,255,0.35)' }}>
                      {preview.cf_vectors != null
                        ? <><span style={{ color: '#6dc08a' }}>{preview.cf_vectors.toLocaleString()}</span> live in CF · ~{preview.total_vectors} local</>
                        : `~${preview.total_vectors} vectors`}
                    </span>
                  </div>
                  {preview.domains.map(d => (
                    <div key={d.domain} className="px-4 py-2 flex items-center justify-between" style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                      <span className="text-xs" style={{ color: 'rgba(200,170,255,0.65)', fontFamily: "'Fira Code', monospace" }}>
                        {d.domain}
                      </span>
                      <span className="text-[10px]" style={{ color: 'rgba(200,170,255,0.3)' }}>
                        {d.files} files · {d.vectors} vectors
                      </span>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-xs mb-4" style={{ color: 'rgba(200,170,255,0.3)' }}>No indexed domains.</p>
              )}

              {preview.report_files.length > 0 ? (
                <div className="mb-4 rounded-xl overflow-hidden" style={{ border: '1px solid rgba(160,100,255,0.15)' }}>
                  <div className="px-4 py-2" style={{ background: 'rgba(0,0,0,0.3)', borderBottom: '1px solid rgba(160,100,255,0.1)' }}>
                    <span className="text-[10px] font-bold tracking-[2px] uppercase" style={{ color: 'rgba(200,170,255,0.5)' }}>
                      Generated files ({preview.report_files.length})
                    </span>
                  </div>
                  {preview.report_files.map(f => (
                    <div key={f.filename} className="px-4 py-2 flex items-center justify-between" style={{ borderBottom: '1px solid rgba(255,255,255,0.04)' }}>
                      <span className="text-xs truncate mr-3" style={{ color: 'rgba(200,170,255,0.65)', fontFamily: "'Fira Code', monospace" }}>
                        {f.filename}
                      </span>
                      <span className="text-[10px] shrink-0" style={{ color: 'rgba(200,170,255,0.3)' }}>
                        {f.type.toUpperCase()} · {formatBytes(f.size)}
                      </span>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-xs mb-4" style={{ color: 'rgba(200,170,255,0.3)' }}>No generated files.</p>
              )}

              <div className="flex gap-3 mb-5">
                <div className="flex-1 rounded-xl px-4 py-3 text-center" style={{ background: 'rgba(0,0,0,0.3)', border: '1px solid rgba(160,100,255,0.12)' }}>
                  <p className="text-lg font-bold" style={{ color: '#c8aaff' }}>{preview.run_count}</p>
                  <p className="text-[10px] tracking-[2px] uppercase" style={{ color: 'rgba(200,170,255,0.4)' }}>runs logged</p>
                </div>
                <div className="flex-1 rounded-xl px-4 py-3 text-center" style={{ background: 'rgba(0,0,0,0.3)', border: '1px solid rgba(160,100,255,0.12)' }}>
                  <p className="text-lg font-bold" style={{ color: '#c8aaff' }}>{preview.session_count}</p>
                  <p className="text-[10px] tracking-[2px] uppercase" style={{ color: 'rgba(200,170,255,0.4)' }}>browse sessions</p>
                </div>
              </div>
            </>
          )}
        </div>

        {!loading && !done && (
          <div className="px-6 pb-6 pt-4 shrink-0" style={{ borderTop: '1px solid rgba(255,255,255,0.06)' }}>
            {error && (
              <p className="text-xs mb-3" style={{ color: '#d46060' }}>{error}</p>
            )}
            <div className="flex gap-2">
              <input
                type="password"
                value={password}
                onChange={e => { setPassword(e.target.value); setError(''); }}
                onKeyDown={e => { if (e.key === 'Enter') handleClear(); }}
                placeholder="Admin password"
                className="flex-1 rounded-lg px-3 py-2 text-xs outline-none"
                style={{
                  background: 'rgba(0,0,0,0.5)',
                  border: `1px solid ${error ? 'rgba(212,96,96,0.6)' : 'rgba(160,100,255,0.25)'}`,
                  color: 'white',
                  caretColor: '#c8aaff',
                }}
                autoFocus
              />
              <button
                onClick={handleClear}
                disabled={clearing || !password}
                className="px-4 py-2 rounded-lg text-[10px] font-bold tracking-[2px] uppercase transition-all shrink-0"
                style={{
                  background: clearing ? 'rgba(212,96,96,0.08)' : 'rgba(212,96,96,0.14)',
                  border: '1px solid rgba(212,96,96,0.4)',
                  color: clearing ? 'rgba(212,96,96,0.4)' : '#d46060',
                  cursor: clearing || !password ? 'not-allowed' : 'pointer',
                }}
              >
                {clearing ? 'clearing…' : 'confirm clear'}
              </button>
            </div>
            <p className="text-[10px] mt-2" style={{ color: 'rgba(200,170,255,0.25)' }}>
              This permanently deletes all vectors, extracts, and generated reports.
            </p>
          </div>
        )}

        {done && (
          <div className="px-6 pb-6 pt-2 shrink-0">
            <button
              onClick={onClose}
              className="w-full py-2 rounded-lg text-[10px] font-bold tracking-[3px] uppercase"
              style={{ background: 'rgba(109,192,138,0.1)', border: '1px solid rgba(109,192,138,0.3)', color: '#6dc08a' }}
            >
              close
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

function matchReports(run: RunEntry, reports: ReportFile[]): ReportFile[] {
  const slugs = [run.riva_url, run.comp_url]
    .filter(Boolean)
    .map(u => hostname(u).replace(/[^a-z0-9]/gi, '').toLowerCase());
  if (!slugs.length) return [];
  return reports.filter(r =>
    slugs.some(s => r.filename.toLowerCase().includes(s))
  );
}

function ReportBadge({ r }: { r: ReportFile }) {
  return (
    <div
      className="flex items-center justify-between px-4 py-3 rounded-xl"
      style={{ border: '1px solid rgba(160,100,255,0.15)', background: 'rgba(0,0,0,0.3)' }}
    >
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span
            className="text-[9px] font-bold tracking-[2px] uppercase px-1.5 py-0.5 rounded shrink-0"
            style={{
              background: 'rgba(109,192,138,0.12)',
              color:      '#6dc08a',
              border:     '1px solid rgba(109,192,138,0.25)',
            }}
          >
            {r.type}
          </span>
          <span className="text-xs truncate" style={{ color: 'rgba(200,170,255,0.65)', fontFamily: "'Fira Code', monospace" }}>
            {r.filename}
          </span>
        </div>
        <div className="flex gap-3 mt-1">
          <span className="text-[10px]" style={{ color: 'rgba(200,170,255,0.3)' }}>{formatBytes(r.size)}</span>
          <span className="text-[10px]" style={{ color: 'rgba(200,170,255,0.3)' }}>{formatDate(r.modified)}</span>
        </div>
      </div>
      <button
        onClick={() => downloadBlob(`${API_BASE}${r.url}`, r.filename)}
        className="flex items-center gap-1.5 ml-4 px-3 py-1.5 rounded-lg text-[10px] font-bold tracking-[2px] uppercase transition-all shrink-0"
        style={{
          background: 'rgba(109,192,138,0.08)',
          border: '1px solid rgba(109,192,138,0.25)',
          color: '#6dc08a',
        }}
      >
        <Download size={10} />
        download
      </button>
    </div>
  );
}

function HistoryTab({
  runs, reports, onRefresh,
}: {
  runs: RunEntry[];
  reports: ReportFile[];
  onRefresh: () => void;
}) {
  const [expanded,       setExpanded]       = useState<Set<string>>(new Set());
  const [showClearModal, setShowClearModal]  = useState(false);
  const [cfVectors,      setCfVectors]       = useState<number | null>(null);
  const [cfLoading,      setCfLoading]       = useState(true);

  function fetchCfCount() {
    setCfLoading(true);
    fetch(`${API_BASE}/clear-preview`)
      .then(r => r.json())
      .then(d => { setCfVectors(d.cf_vectors ?? null); setCfLoading(false); })
      .catch(() => setCfLoading(false));
  }

  useEffect(() => { fetchCfCount(); }, []);

  function handleRefresh() {
    fetchCfCount();
    onRefresh();
  }

  function toggle(id: string) {
    setExpanded(prev => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });
  }

  const matchedReportNames = new Set(
    runs.flatMap(run => matchReports(run, reports).map(r => r.filename))
  );
  const unmatchedReports = reports.filter(r => !matchedReportNames.has(r.filename));

  return (
    <div className="w-full max-w-3xl">
      {showClearModal && (
        <ClearModal
          onClose={() => setShowClearModal(false)}
          onCleared={() => { fetchCfCount(); onRefresh(); }}
        />
      )}

      <div className="flex items-center justify-between mb-4">
        <p className="text-xs font-bold tracking-[3px] uppercase" style={{ color: 'rgba(200,170,255,0.4)' }}>
          Session history
        </p>
        <div className="flex items-center gap-2">
          <button
            onClick={handleRefresh}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[10px] font-bold tracking-[2px] uppercase transition-all"
            style={{ border: '1px solid rgba(160,100,255,0.25)', color: 'rgba(200,170,255,0.5)' }}
          >
            <RefreshCw size={10} />
            refresh
          </button>
          <button
            onClick={() => setShowClearModal(true)}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-[10px] font-bold tracking-[2px] uppercase transition-all"
            style={{ border: '1px solid rgba(212,96,96,0.25)', color: 'rgba(212,96,96,0.5)' }}
          >
            <Trash2 size={10} />
            clear data
          </button>
        </div>
      </div>

      <div className="flex items-center gap-2 mb-5 px-3 py-2 rounded-lg"
        style={{ background: 'rgba(0,0,0,0.25)', border: '1px solid rgba(160,100,255,0.1)' }}>
        <span className="text-[10px] tracking-[1px] uppercase" style={{ color: 'rgba(200,170,255,0.35)' }}>
          Cloudflare Vectorize
        </span>
        <span className="text-[10px] font-bold ml-auto" style={{
          color: cfLoading ? 'rgba(200,170,255,0.3)' : cfVectors === 0 ? '#6dc08a' : cfVectors != null ? '#c8aaff' : 'rgba(200,170,255,0.3)'
        }}>
          {cfLoading ? '…' : cfVectors != null ? `${cfVectors.toLocaleString()} vectors` : 'unavailable'}
        </span>
        {!cfLoading && cfVectors === 0 && (
          <span className="text-[9px]" style={{ color: '#6dc08a' }}>✓ empty</span>
        )}
      </div>

      <div className="mb-8">
        <p className="text-xs font-bold tracking-[3px] uppercase mb-3" style={{ color: 'rgba(200,170,255,0.4)' }}>
          Generated reports
          {reports.length > 0 && (
            <span className="ml-2 text-[10px] normal-case tracking-normal font-normal" style={{ color: 'rgba(200,170,255,0.3)' }}>
              ({reports.length} file{reports.length !== 1 ? 's' : ''})
            </span>
          )}
        </p>
        {reports.length === 0 ? (
          <div className="rounded-xl px-4 py-5 text-center" style={{ border: '1px dashed rgba(160,100,255,0.12)', background: 'rgba(0,0,0,0.15)' }}>
            <p className="text-xs" style={{ color: 'rgba(200,170,255,0.25)' }}>
              Reports will appear here after an analysis completes.
            </p>
          </div>
        ) : (
          <div className="flex flex-col gap-2">
            {reports.map(r => <ReportBadge key={r.filename} r={r} />)}
          </div>
        )}
      </div>

      <p className="text-xs font-bold tracking-[3px] uppercase mb-3" style={{ color: 'rgba(200,170,255,0.4)' }}>
        Analysis runs
      </p>
      {runs.length === 0 ? (
        <p className="text-xs text-center py-8" style={{ color: 'rgba(200,170,255,0.25)' }}>
          No sessions yet.
        </p>
      ) : (
        <div className="flex flex-col gap-2">
          {runs.map(run => {
            const open = expanded.has(run.id);
            const label = [run.riva_url, run.comp_url]
              .filter(Boolean)
              .map(hostname)
              .join('  ·  ');
            const runReports = matchReports(run, reports);
            return (
              <div
                key={run.id}
                className="rounded-xl overflow-hidden"
                style={{ border: '1px solid rgba(160,100,255,0.15)', background: 'rgba(0,0,0,0.3)' }}
              >
                <button
                  className="w-full flex items-center justify-between px-4 py-3 text-left"
                  onClick={() => toggle(run.id)}
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <span
                      className="text-[9px] font-bold tracking-[1px] shrink-0"
                      style={{
                        color: open ? '#c8aaff' : 'rgba(200,170,255,0.35)',
                        transition: 'color 0.15s',
                      }}
                    >
                      {open ? '▾' : '▸'}
                    </span>
                    <span
                      className="text-xs truncate"
                      style={{ color: 'rgba(200,170,255,0.7)', fontFamily: "'Fira Code', monospace" }}
                    >
                      {label || '(no URLs)'}
                    </span>
                  </div>
                  <div className="flex items-center gap-2 shrink-0 ml-3">
                    {runReports.length > 0 && (
                      <span className="text-[9px] font-bold px-1.5 py-0.5 rounded" style={{ background: 'rgba(109,192,138,0.12)', color: '#6dc08a', border: '1px solid rgba(109,192,138,0.25)' }}>
                        {runReports.length} report{runReports.length > 1 ? 's' : ''}
                      </span>
                    )}
                    <span className="text-[10px]" style={{ color: 'rgba(200,170,255,0.3)' }}>
                      {formatDate(run.started_at)}
                    </span>
                  </div>
                </button>

                {open && (
                  <div
                    className="px-4 pb-4 flex flex-col gap-2"
                    style={{ borderTop: '1px solid rgba(160,100,255,0.1)' }}
                  >
                    {run.riva_url && (
                      <div className="flex items-center gap-3 pt-2">
                        <span className="text-[9px] font-bold tracking-[2px] uppercase w-20 shrink-0" style={{ color: '#4db8ff' }}>
                          Your Site
                        </span>
                        <span className="text-xs truncate" style={{ color: 'rgba(200,170,255,0.6)', fontFamily: "'Fira Code', monospace" }}>
                          {run.riva_url}
                        </span>
                      </div>
                    )}
                    {run.comp_url && (
                      <div className="flex items-center gap-3">
                        <span className="text-[9px] font-bold tracking-[2px] uppercase w-20 shrink-0" style={{ color: '#ff6b6b' }}>
                          Competitor
                        </span>
                        <span className="text-xs truncate" style={{ color: 'rgba(200,170,255,0.6)', fontFamily: "'Fira Code', monospace" }}>
                          {run.comp_url}
                        </span>
                      </div>
                    )}
                    {/* Matched reports for this run */}
                    {runReports.length > 0 && (
                      <div className="mt-2 flex flex-col gap-1.5">
                        <p className="text-[9px] font-bold tracking-[2px] uppercase" style={{ color: 'rgba(200,170,255,0.3)' }}>
                          Reports
                        </p>
                        {runReports.map(r => <ReportBadge key={r.filename} r={r} />)}
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {unmatchedReports.length > 0 && (
        <div className="mt-6">
          <p className="text-xs font-bold tracking-[3px] uppercase mb-3" style={{ color: 'rgba(200,170,255,0.25)' }}>
            Other reports
          </p>
          <div className="flex flex-col gap-2">
            {unmatchedReports.map(r => <ReportBadge key={r.filename} r={r} />)}
          </div>
        </div>
      )}
    </div>
  );
}

function AdminModal({ onClose, onUnlocked }: { onClose: () => void; onUnlocked: (pw: string) => void }) {
  const [pw,      setPw]      = useState('');
  const [error,   setError]   = useState('');
  const [loading, setLoading] = useState(false);

  async function verify() {
    if (!pw.trim()) return;
    setLoading(true);
    setError('');
    try {
      const resp = await fetch(`${API_BASE}/admin/verify`, {
        headers: { 'x-admin-password': pw.trim() },
      });
      const data = await resp.json();
      if (data.ok) {
        onUnlocked(pw.trim());
        onClose();
      } else {
        setError('Incorrect password.');
      }
    } catch {
      setError('Could not reach server.');
    }
    setLoading(false);
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: 'rgba(0,0,0,0.8)', backdropFilter: 'blur(6px)' }}
      onClick={onClose}
    >
      <div
        className="relative w-80 rounded-2xl p-6"
        style={{
          background: 'linear-gradient(135deg, #1a0810 0%, #200a14 100%)',
          border: '1px solid rgba(212,96,96,0.35)',
          boxShadow: '0 0 60px rgba(212,96,96,0.12)',
        }}
        onClick={e => e.stopPropagation()}
      >
        <button onClick={onClose} className="absolute top-4 right-4 opacity-30 hover:opacity-70 transition-opacity" style={{ color: '#ff6b6b' }}>
          <X size={14} />
        </button>

        <div className="flex justify-center mb-4">
          <svg viewBox="0 0 80 48" style={{ width: 48, overflow: 'visible' }}>
            <defs><clipPath id="admin-eye-clip"><path d="M3,24 Q40,-7 77,24 Q40,55 3,24" /></clipPath></defs>
            <g clipPath="url(#admin-eye-clip)">
              <path d="M3,24 Q40,-7 77,24 Q40,55 3,24" fill="#fbe9e7" />
              <circle cx="40" cy="24" r="14" fill="#330000" />
              <circle cx="40" cy="24" r="5" fill="#ff3333" style={{ filter: 'drop-shadow(0 0 6px #ff3333)' }} />
            </g>
          </svg>
        </div>

        <p className="text-[10px] font-bold tracking-[4px] uppercase text-center mb-5" style={{ color: 'rgba(255,107,107,0.6)' }}>
          Admin access
        </p>

        {error && <p className="text-[10px] mb-3 text-center" style={{ color: '#d46060' }}>{error}</p>}

        <div className="flex gap-2">
          <input
            type="password"
            value={pw}
            onChange={e => { setPw(e.target.value); setError(''); }}
            onKeyDown={e => { if (e.key === 'Enter') verify(); }}
            placeholder="password"
            autoFocus
            className="flex-1 rounded-lg px-3 py-2 text-xs outline-none"
            style={{
              background: 'rgba(0,0,0,0.5)',
              border: `1px solid ${error ? 'rgba(212,96,96,0.6)' : 'rgba(212,96,96,0.25)'}`,
              color: 'white',
              caretColor: '#ff6b6b',
            }}
          />
          <button
            onClick={verify}
            disabled={loading || !pw.trim()}
            className="px-4 py-2 rounded-lg text-[10px] font-bold tracking-[2px] uppercase shrink-0 transition-all"
            style={{
              background: 'rgba(212,96,96,0.14)',
              border: '1px solid rgba(212,96,96,0.4)',
              color: '#ff6b6b',
              opacity: loading || !pw.trim() ? 0.4 : 1,
              cursor: loading || !pw.trim() ? 'not-allowed' : 'pointer',
            }}
          >
            {loading ? '…' : 'unlock'}
          </button>
        </div>
        <p className="text-[9px] mt-3 text-center" style={{ color: 'rgba(255,107,107,0.25)' }}>
          bypasses daily run limit
        </p>
      </div>
    </div>
  );
}

const ADMIN_STORAGE_KEY = 'riva-admin-pw';

export default function Home() {
  const router = useRouter();
  const [rivaUrl, setRivaUrl] = useState('');
  const [compUrl, setCompUrl] = useState('');
  const [activeTab, setActiveTab]   = useState<'new' | 'history'>('new');
  const [showAdminModal, setShowAdminModal] = useState(false);
  const [adminPw, setAdminPw] = useState('');
  const [runsUsed, setRunsUsed]     = useState(0);
  const [runsLoaded, setRunsLoaded] = useState(false);
  const [submitLoading, setSubmitLoading] = useState(false);
  const [runs,    setRuns]    = useState<RunEntry[]>([]);
  const [reports, setReports] = useState<ReportFile[]>([]);
  const rivaPupilRef  = useRef<SVGGElement>(null);
  const rivaLidRef    = useRef<SVGRectElement>(null);
  const compPupilRef  = useRef<SVGGElement>(null);
  const compLidRef    = useRef<SVGRectElement>(null);
  const smileRef      = useRef<SVGPathElement>(null);
  const ctaRef        = useRef<HTMLDivElement>(null);

  useEffect(() => {
    try {
      const saved = localStorage.getItem(ADMIN_STORAGE_KEY);
      if (saved) setAdminPw(saved);
    } catch {}
  }, []);

  const isAdmin = !!adminPw;

  function adminHeaders() {
    const h: Record<string, string> = { ...clientHeaders() };
    if (adminPw) h['x-admin-password'] = adminPw;
    return h;
  }

  const fetchLimit = useCallback(() => {
    fetch(`${API_BASE}/daily-limit`, { headers: clientHeaders() })
      .then(r => r.json())
      .then(d => { setRunsUsed(d.used ?? 0); setRunsLoaded(true); })
      .catch(() => setRunsLoaded(true));
  }, []);

  const fetchHistory = useCallback(() => {
    fetch(`${API_BASE}/runs`)
      .then(r => r.json())
      .then(d => setRuns(Array.isArray(d) ? d : []))
      .catch(() => {});
    fetch(`${API_BASE}/list-reports`)
      .then(r => r.json())
      .then(d => setReports(d.files || []))
      .catch(() => {});
  }, []);

  useEffect(() => {
    fetchLimit();
    fetchHistory();
    const iv = setInterval(fetchHistory, 15000);
    return () => clearInterval(iv);
  }, [fetchLimit, fetchHistory]);


  // eye animation loops - each eye has independent pupil movement and blinking
  useEffect(() => {
    let alive = true;
    function blink(lid: SVGRectElement | null) {
      if (!lid) return;
      lid.style.transform = 'translateY(0%)';
      setTimeout(() => { if (lid) lid.style.transform = 'translateY(-100%)'; }, 150);
    }
    function sentientLoop(pupil: SVGGElement | null, lid: SVGRectElement | null, glowEl: SVGCircleElement | null) {
      if (!alive || !pupil) return;
      const x = (Math.random() - 0.5) * 50;
      const y = (Math.random() - 0.5) * 30;
      pupil.style.transform = `translate(${x}px, ${y}px)`;
      if (glowEl) glowEl.setAttribute('r', Math.random() > 0.5 ? '13' : '8');
      if (Math.random() > 0.8) blink(lid);
      setTimeout(() => sentientLoop(pupil, lid, glowEl), Math.random() * 2000 + 800);
    }
    const rivaGlow = document.getElementById('riva-glow-el') as SVGCircleElement | null;
    const compGlow = document.getElementById('comp-glow-el') as SVGCircleElement | null;
    sentientLoop(rivaPupilRef.current, rivaLidRef.current, rivaGlow);
    setTimeout(() => sentientLoop(compPupilRef.current, compLidRef.current, compGlow), 400);
    return () => { alive = false; };
  }, []);

  // smile curve warps toward the cursor when mouse is within 400px
  useEffect(() => {
    function onMove(e: MouseEvent) {
      const cta   = ctaRef.current;
      const smile = smileRef.current;
      if (!cta || !smile) return;
      const rect = cta.getBoundingClientRect();
      const cx   = rect.left + rect.width  / 2;
      const cy   = rect.top  + rect.height / 2;
      const dist = Math.hypot(e.clientX - cx, e.clientY - cy);
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
    if (!isAdmin && runsUsed >= DAILY_LIMIT) return;
    doSubmit();
  }

  async function doSubmit() {
    setSubmitLoading(true);
    let runId = crypto.randomUUID();
    try {
      const resp = await fetch(`${API_BASE}/start-run`, {
        method: 'POST',
        headers: adminHeaders(),
        body: JSON.stringify({ riva_url: rivaUrl.trim(), comp_url: compUrl.trim() }),
      });
      if (resp.status === 429) {
        setRunsUsed(DAILY_LIMIT);
        setSubmitLoading(false);
        return;
      }
      const data = await resp.json();
      if (data.run_id) runId = data.run_id;
      setRunsUsed(data.used ?? runsUsed + 1);
      fetchHistory();
    } catch {
      // server unreachable, proceed anyway - the dashboard will handle it
    }
    setSubmitLoading(false);
    const params = new URLSearchParams();
    if (rivaUrl.trim()) params.set('riva', rivaUrl.trim());
    if (compUrl.trim()) params.set('comp', compUrl.trim());
    params.set('run_id', runId);
    router.push(`/dashboard?${params.toString()}`);
  }

  const atLimit      = runsLoaded && runsUsed >= DAILY_LIMIT && !isAdmin;
  const progressPct  = Math.min(100, runsLoaded ? (runsUsed / DAILY_LIMIT) * 100 : 0);
  const progressColor = runsUsed >= DAILY_LIMIT && !isAdmin ? '#d46060' : runsUsed >= 5 ? '#c8a84a' : '#6dc08a';
  const canSubmit    = !!(rivaUrl.trim() || compUrl.trim()) && !atLimit && !submitLoading;

  return (
    <main className="text-white" style={{ fontFamily: "'Inter', -apple-system, sans-serif" }}>
      {showAdminModal && (
        <AdminModal
          onClose={() => setShowAdminModal(false)}
          onUnlocked={(pw) => {
            setAdminPw(pw);
            try { localStorage.setItem(ADMIN_STORAGE_KEY, pw); } catch {}
          }}
        />
      )}

      <section
        className="min-h-screen flex flex-col items-center justify-center relative overflow-hidden"
        style={{ background: 'radial-gradient(ellipse at 50% 40%, #2a1a60 0%, #1a0d40 35%, #0d0820 70%, #060412 100%)' }}
      >
        <div
          className="absolute inset-0 opacity-[0.04]"
          style={{
            backgroundImage: 'linear-gradient(rgba(160,120,255,1) 1px, transparent 1px), linear-gradient(90deg, rgba(160,120,255,1) 1px, transparent 1px)',
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

          <div className="flex gap-8 sm:gap-24 mb-14">
            <div className="w-36 sm:w-64 text-center">
              <svg viewBox="0 0 200 120" overflow="visible">
                <defs><clipPath id="eye-clip-r"><path d="M10,60 Q100,-15 190,60 Q100,135 10,60" /></clipPath></defs>
                <g clipPath="url(#eye-clip-r)">
                  <path d="M10,60 Q100,-15 190,60 Q100,135 10,60" fill="#e0f7fa" />
                  <g ref={rivaPupilRef} style={{ transition: 'transform 0.4s cubic-bezier(0.175,0.885,0.32,1.275)' }}>
                    <circle cx="100" cy="60" r="28" fill="#002233" />
                    <circle id="riva-glow-el" cx="100" cy="60" r="10" fill="#00ffff" style={{ filter: 'drop-shadow(0 0 10px #00ffff)', transition: 'r 0.4s' }} />
                  </g>
                  <rect ref={rivaLidRef} width="200" height="120" fill="#1a0d40" style={{ transform: 'translateY(-100%)', transition: 'transform 0.15s ease-in-out' }} />
                </g>
                <path d="M10,60 Q100,-15 190,60" fill="none" stroke="#4db8ff" strokeWidth="2" opacity="0.4" />
              </svg>
            </div>

            {/* clicking the red eye opens the admin modal */}
            <div
              className="w-36 sm:w-64 text-center cursor-pointer select-none"
              onClick={() => setShowAdminModal(true)}
              title=""
            >
              <svg viewBox="0 0 200 120" overflow="visible">
                <defs><clipPath id="eye-clip-c"><path d="M10,60 Q100,-15 190,60 Q100,135 10,60" /></clipPath></defs>
                <g clipPath="url(#eye-clip-c)">
                  <path d="M10,60 Q100,-15 190,60 Q100,135 10,60" fill="#fbe9e7" />
                  <g ref={compPupilRef} style={{ transition: 'transform 0.4s cubic-bezier(0.175,0.885,0.32,1.275)' }}>
                    <circle cx="100" cy="60" r="28" fill="#330000" />
                    <circle id="comp-glow-el" cx="100" cy="60" r="10" fill={isAdmin ? '#ff9900' : '#ff3333'}
                      style={{ filter: `drop-shadow(0 0 10px ${isAdmin ? '#ff9900' : '#ff3333'})`, transition: 'r 0.4s, fill 0.3s' }} />
                  </g>
                  <rect ref={compLidRef} width="200" height="120" fill="#200a1a" style={{ transform: 'translateY(-100%)', transition: 'transform 0.15s ease-in-out' }} />
                </g>
                <path d="M10,60 Q100,-15 190,60" fill="none" stroke={isAdmin ? '#ff9900' : '#ff6b6b'} strokeWidth="2" opacity="0.4" />
              </svg>
            </div>
          </div>

          <div ref={ctaRef} onClick={scrollToInput} className="flex flex-col items-center cursor-pointer select-none" style={{ animation: 'breath 4s infinite ease-in-out' }}>
            <svg viewBox="0 0 200 60" style={{ width: 220, overflow: 'visible' }}>
              <path ref={smileRef} d="M20,10 Q100,50 180,10" fill="none" stroke="#c8aaff" strokeWidth="2.5" strokeLinecap="round" style={{ transition: 'stroke 0.2s' }} />
            </svg>
            <span className="text-xs font-bold tracking-[5px] uppercase mt-2" style={{ color: '#c8aaff', opacity: 0.6 }}>begin</span>
          </div>
        </div>

        <div className="absolute bottom-8 flex flex-col items-center gap-1 opacity-20">
          <div className="w-px h-8 bg-white" style={{ animation: 'pulse 2s infinite' }} />
        </div>
      </section>

      <section
        id="input-section"
        className="min-h-screen flex flex-col items-center justify-center px-4 sm:px-8 py-20"
        style={{ background: 'radial-gradient(ellipse at 50% 50%, #1a1040 0%, #0d0820 50%, #060412 100%)' }}
      >
        <div className="flex gap-1 mb-8 p-1 rounded-xl" style={{ background: 'rgba(0,0,0,0.4)', border: '1px solid rgba(160,100,255,0.15)' }}>
          {(['new', 'history'] as const).map(tab => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              className="px-5 py-2 rounded-lg text-[11px] font-bold tracking-[3px] uppercase transition-all"
              style={{
                background: activeTab === tab ? 'rgba(160,100,255,0.18)' : 'transparent',
                color:      activeTab === tab ? '#c8aaff'               : 'rgba(200,170,255,0.35)',
                border:     activeTab === tab ? '1px solid rgba(160,100,255,0.35)' : '1px solid transparent',
              }}
            >
              {tab === 'new' ? 'New Analysis' : `History${runs.length ? ` (${runs.length})` : ''}`}
            </button>
          ))}
        </div>

        {activeTab === 'new' && (
          <div className="w-full max-w-3xl">
            <p className="text-sm mb-8" style={{ color: 'rgba(200,170,255,0.35)', letterSpacing: '1px' }}>
              Paste two product website links and get autonomous competitive analysis reports and a GTM strategy.
            </p>

            {/* Inline requirements — no popup */}
            <div className="mb-8 rounded-xl p-4" style={{ background: 'rgba(160,100,255,0.05)', border: '1px solid rgba(160,100,255,0.15)' }}>
              <p className="text-[10px] font-bold tracking-[3px] uppercase mb-3" style={{ color: 'rgba(200,170,255,0.4)' }}>
                Before you start
              </p>
              <div className="rounded-lg px-3 py-2.5 mb-3" style={{ background: 'rgba(160,100,255,0.07)', border: '1px solid rgba(160,100,255,0.18)' }}>
                <p className="text-[10px] font-bold mb-1" style={{ color: 'rgba(200,170,255,0.55)' }}>Both sites must be:</p>
                <ul className="text-[10px] space-y-0.5" style={{ color: 'rgba(200,170,255,0.45)' }}>
                  <li>✦ <strong style={{ color: 'rgba(200,170,255,0.65)' }}>Tech / B2B SaaS</strong> products</li>
                  <li>✦ Have a <strong style={{ color: 'rgba(200,170,255,0.65)' }}>public pricing page</strong></li>
                  <li>✦ Have <strong style={{ color: 'rgba(200,170,255,0.65)' }}>public documentation</strong></li>
                </ul>
              </div>
              <div className="flex gap-3">
                <div className="flex-1 rounded-lg px-3 py-2.5" style={{ background: 'rgba(109,192,138,0.06)', border: '1px solid rgba(109,192,138,0.18)' }}>
                  <p className="text-[9px] font-bold tracking-[2px] uppercase mb-1.5" style={{ color: '#6dc08a' }}>Works great</p>
                  <p className="text-[10px] font-bold mb-1" style={{ color: 'rgba(200,170,255,0.5)' }}>vercel.com vs railway.app</p>
                  <ul className="text-[10px] space-y-0.5" style={{ color: 'rgba(200,170,255,0.35)' }}>
                    <li>✓ Clear pricing tiers</li>
                    <li>✓ Public documentation</li>
                    <li>✓ Feature comparison pages</li>
                  </ul>
                </div>
                <div className="flex-1 rounded-lg px-3 py-2.5" style={{ background: 'rgba(212,96,96,0.06)', border: '1px solid rgba(212,96,96,0.18)' }}>
                  <p className="text-[9px] font-bold tracking-[2px] uppercase mb-1.5" style={{ color: '#d46060' }}>Doesn&apos;t work</p>
                  <ul className="text-[10px] space-y-0.5" style={{ color: 'rgba(200,170,255,0.35)' }}>
                    <li>✗ E-commerce / retail</li>
                    <li>✗ News or media sites</li>
                    <li>✗ No public pricing page</li>
                    <li>✗ Login-gated products</li>
                  </ul>
                </div>
              </div>
            </div>

            <form onSubmit={handleSubmit} className="flex flex-col gap-4">
              <div className="flex flex-col sm:flex-row gap-4">
                <div className="flex-1">
                  <div className="mb-1.5 text-[10px] font-bold tracking-[4px] uppercase" style={{ color: '#4db8ff', opacity: 0.6 }}>Your Site</div>
                  <div className="flex items-center border rounded-lg px-4 py-3 gap-3" style={{ borderColor: 'rgba(77,184,255,0.55)', background: 'rgba(0,0,0,0.45)' }}>
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

                <div className="flex-1">
                  <div className="mb-1.5 text-[10px] font-bold tracking-[4px] uppercase flex items-center gap-2" style={{ color: '#ff6b6b', opacity: 0.6 }}>
                    Competing Product
                    <span className="text-white/20 normal-case tracking-normal font-normal">(optional)</span>
                  </div>
                  <div className="flex items-center border rounded-lg px-4 py-3 gap-3" style={{ borderColor: 'rgba(255,107,107,0.55)', background: 'rgba(0,0,0,0.45)' }}>
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
                  disabled={!canSubmit}
                  className="px-8 py-2.5 rounded-lg text-sm font-bold tracking-[4px] uppercase transition-all duration-200 flex items-center gap-3"
                  style={{
                    background: 'linear-gradient(135deg, rgba(160,100,255,0.15) 0%, rgba(160,100,255,0.05) 100%)',
                    border: '1px solid rgba(160,100,255,0.4)',
                    color: '#c8aaff',
                    boxShadow: '0 0 20px rgba(160,100,255,0.1)',
                    opacity: canSubmit ? 1 : 0.4,
                    cursor: canSubmit ? 'pointer' : 'not-allowed',
                  }}
                  onMouseEnter={e => {
                    if (!canSubmit) return;
                    (e.currentTarget as HTMLButtonElement).style.boxShadow = '0 0 30px rgba(160,100,255,0.3)';
                    (e.currentTarget as HTMLButtonElement).style.borderColor = 'rgba(160,100,255,0.8)';
                  }}
                  onMouseLeave={e => {
                    (e.currentTarget as HTMLButtonElement).style.boxShadow = '0 0 20px rgba(160,100,255,0.1)';
                    (e.currentTarget as HTMLButtonElement).style.borderColor = 'rgba(160,100,255,0.4)';
                  }}
                >
                  {submitLoading ? '…' : <span style={{ fontSize: 12 }}>→</span>}
                </button>
              </div>

              {runsLoaded && (
                <div className="flex flex-col items-center gap-2 mt-3">
                  <div className="flex items-center gap-2">
                    <span className="text-[11px]" style={{ color: 'rgba(200,170,255,0.45)', letterSpacing: '0.5px' }}>
                      {runsUsed} / {DAILY_LIMIT}
                    </span>
                    {atLimit && (
                      <span className="text-[10px] font-bold tracking-[2px] uppercase" style={{ color: '#d46060' }}>
                        · limit reached
                      </span>
                    )}
                  </div>
                  <div className="w-48 h-1 rounded-full overflow-hidden" style={{ background: 'rgba(255,255,255,0.08)' }}>
                    <div
                      className="h-full rounded-full transition-all duration-500"
                      style={{ width: `${progressPct}%`, background: progressColor, boxShadow: `0 0 6px ${progressColor}55` }}
                    />
                  </div>
                  {atLimit && (
                    <p className="text-[10px]" style={{ color: 'rgba(200,170,255,0.35)' }}>
                      Resets at midnight UTC
                    </p>
                  )}
                </div>
              )}
            </form>
          </div>
        )}

        {activeTab === 'history' && (
          <HistoryTab
            runs={runs}
            reports={reports}
            onRefresh={() => { fetchLimit(); fetchHistory(); }}
          />
        )}
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
