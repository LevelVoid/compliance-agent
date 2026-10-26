'use client';

import { useState } from 'react';

export default function Home() {
  const [repoUrl, setRepoUrl] = useState('');
  const [loading, setLoading] = useState(false);
  const [auditData, setAuditData] = useState<any>(null);
  const [error, setError] = useState('');

  const handleAudit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError('');
    setAuditData(null);

    try {
      const res = await fetch('/api/audit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ repoUrl }),
      });

      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Audit failed');

      setAuditData(data);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <main className="min-h-screen bg-slate-950 text-slate-100 p-8 font-sans">
      <div className="max-w-4xl mx-auto space-y-8">
        
        {/* Header */}
        <div className="text-center space-y-3">
          <span className="bg-emerald-500/10 text-emerald-400 text-xs font-semibold px-3 py-1 rounded-full border border-emerald-500/20">
            Powered by Local Gemma & DPDP Act 2023
          </span>
          <h1 className="text-4xl font-extrabold tracking-tight">RepoGuard: DPDP Code Linter</h1>
          <p className="text-slate-400 max-w-xl mx-auto">
            Audit any public GitHub repository for privacy compliance, data deletion gaps, and consent tracking locally and securely.
          </p>
        </div>

        {/* Input Form */}
        <form onSubmit={handleAudit} className="flex gap-3 bg-slate-900 p-3 rounded-xl border border-slate-800 shadow-lg">
          <input
            type="url"
            required
            placeholder="https://github.com/username/repository"
            value={repoUrl}
            onChange={(e) => setRepoUrl(e.target.value)}
            className="flex-1 bg-slate-950 border border-slate-700 rounded-lg px-4 py-3 text-sm focus:outline-none focus:border-emerald-500 transition-colors"
          />
          <button
            type="submit"
            disabled={loading}
            className="bg-emerald-600 hover:bg-emerald-500 font-semibold px-6 py-3 rounded-lg text-sm transition-colors disabled:opacity-50 cursor-pointer"
          >
            {loading ? 'Scanning Repo...' : 'Run Compliance Audit'}
          </button>
        </form>

        {error && (
          <div className="bg-red-500/10 border border-red-500/20 text-red-400 p-4 rounded-xl text-sm">
            {error}
          </div>
        )}

        {/* Audit Results Dashboard */}
        {auditData && (
          <div className="space-y-6 bg-slate-900/50 border border-slate-800 p-6 rounded-2xl">
            {/* Score Banner */}
            <div className="flex items-center justify-between border-b border-slate-800 pb-6">
              <div>
                <h2 className="text-xl font-bold">Audit Report</h2>
                <p className="text-sm text-slate-400 mt-1">{auditData.summary}</p>
              </div>
              <div className="text-right">
                <span className="text-3xl font-black text-emerald-400">{auditData.complianceScore}%</span>
                <p className="text-xs text-slate-500 uppercase tracking-wider font-semibold">DPDP Compliance</p>
              </div>
            </div>

            {/* Violations List */}
            <div className="space-y-4">
              <h3 className="text-lg font-semibold text-red-400">Identified Gaps & Violations</h3>
              {auditData.violations?.length === 0 ? (
                <p className="text-sm text-slate-400">No major compliance violations detected in the scanned code.</p>
              ) : (
                auditData.violations.map((v: any, index: number) => (
                  <div key={index} className="bg-slate-900 border border-slate-800 p-4 rounded-xl space-y-2">
                    <div className="flex justify-between items-center">
                      <span className="font-semibold text-sm text-slate-200">{v.pillar}</span>
                      <span className={`text-xs px-2 py-0.5 rounded font-bold ${
                        v.riskLevel === 'High' ? 'bg-red-500/20 text-red-400' : 'bg-amber-500/20 text-amber-400'
                      }`}>
                        {v.riskLevel} Risk
                      </span>
                    </div>
                    <p className="text-sm text-slate-400"><strong className="text-slate-300">Issue:</strong> {v.description}</p>
                    <p className="text-sm text-emerald-400"><strong className="text-slate-300">Fix:</strong> {v.recommendation}</p>
                  </div>
                ))
              )}
            </div>

            {/* Passed Checks */}
            <div className="space-y-2 pt-4 border-t border-slate-800">
              <h3 className="text-md font-semibold text-emerald-400">Passed Checks</h3>
              <ul className="list-disc list-inside text-sm text-slate-400 space-y-1">
                {auditData.passedChecks?.map((check: string, idx: number) => (
                  <li key={idx}>{check}</li>
                ))}
              </ul>
            </div>

          </div>
        )}

      </div>
    </main>
  );
}