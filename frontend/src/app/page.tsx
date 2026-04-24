'use client';

import { useEffect, useState } from 'react';
import { Shield, Activity, Globe, Zap } from 'lucide-react';

export default function Home() {
  const [status, setStatus] = useState<{ status: string; message: string } | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch('http://localhost:8000/')
      .then((res) => res.json())
      .then((data) => {
        setStatus(data);
        setLoading(false);
      })
      .catch((err) => {
        console.error('Failed to connect to backend:', err);
        setLoading(false);
      });
  }, []);

  return (
    <main className="min-h-screen bg-[#050a15] text-white p-8 font-[family-name:var(--font-geist-sans)]">
      {/* Header */}
      <div className="max-w-7xl mx-auto flex justify-between items-center mb-12">
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-full bg-cyan-500 shadow-[0_0_15px_rgba(6,182,212,0.5)] flex items-center justify-center">
            <Shield size={18} className="text-black" />
          </div>
          <h1 className="text-2xl font-bold tracking-widest uppercase">RIVA</h1>
        </div>
        
        <div className="flex items-center gap-4 text-sm font-mono">
          <div className="flex items-center gap-2">
            <div className={`w-2 h-2 rounded-full ${loading ? 'bg-yellow-500' : status ? 'bg-green-500 animate-pulse' : 'bg-red-500'}`} />
            <span className="opacity-70">Backend:</span>
            <span className={status ? 'text-green-400' : 'text-red-400'}>
              {loading ? 'Connecting...' : status ? 'Online' : 'Offline'}
            </span>
          </div>
        </div>
      </div>

      {/* Main Grid */}
      <div className="max-w-7xl mx-auto grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
        <StatusCard 
          icon={<Globe className="text-cyan-400" />} 
          title="Browser Engine" 
          status="Idle" 
          description="Waiting for URL input" 
        />
        <StatusCard 
          icon={<Activity className="text-purple-400" />} 
          title="Agents SDK" 
          status="Standby" 
          description="AIChatAgent ready" 
        />
        <StatusCard 
          icon={<Zap className="text-yellow-400" />} 
          title="Memory Facets" 
          status="Clean" 
          description="Durable Objects active" 
        />
        <StatusCard 
          icon={<Shield className="text-green-400" />} 
          title="Validation" 
          status="Strict" 
          description="Pydantic enforcement active" 
        />
      </div>

      {/* Welcome Message */}
      <div className="max-w-7xl mx-auto mt-12 p-8 border border-white/10 rounded-2xl bg-white/5 backdrop-blur-xl">
        <h2 className="text-xl font-semibold mb-4">Welcome to Riva Agentic AI</h2>
        <p className="text-white/60 mb-6 max-w-2xl">
          The autonomous competitive intelligence platform is now initialized. 
          Connect your documentation URLs or upload PDFs to begin the synthesis loop.
        </p>
        <div className="flex gap-4">
          <button className="px-6 py-2 bg-cyan-600 hover:bg-cyan-500 transition-colors rounded-lg font-bold text-sm uppercase tracking-wider">
            New Analysis
          </button>
          <button className="px-6 py-2 border border-white/20 hover:bg-white/5 transition-colors rounded-lg font-bold text-sm uppercase tracking-wider">
            View Docs
          </button>
        </div>
      </div>
    </main>
  );
}

function StatusCard({ icon, title, status, description }: { icon: React.ReactNode, title: string, status: string, description: string }) {
  return (
    <div className="p-6 border border-white/10 rounded-2xl bg-white/5 hover:bg-white/10 transition-colors group">
      <div className="mb-4">{icon}</div>
      <h3 className="text-sm font-bold opacity-50 uppercase tracking-tighter mb-1">{title}</h3>
      <div className="text-lg font-semibold mb-2">{status}</div>
      <p className="text-xs text-white/40 leading-relaxed">{description}</p>
    </div>
  );
}
