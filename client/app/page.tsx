'use client'

import { useState, useRef, useCallback, useEffect } from 'react'
import {
  AlertTriangle,
  ArrowUpRight,
  CheckCircle2,
  FileWarning,
  GitBranch,
  ShieldCheck,
  Loader2,
  XCircle,
  RefreshCw,
  ChevronDown,
  ChevronUp,
  ClipboardList,
} from 'lucide-react'

// ---------------------------------------------------------------------------
// Types (mirroring the backend schemas)
// ---------------------------------------------------------------------------

type Severity = 'high' | 'medium' | 'low'

interface Obligation {
  framework: string
  control: string
  control_id: string
  title: string
  exposure: string
}

interface Defect {
  defect_id: string
  path: string
  line_start: number
  severity: Severity
  evidence: string
  fix: string
  obligations: Obligation[]
}

interface AuditResult {
  audit_id: string
  repo: string
  commit: string
  mode: string
  frameworks: string[]
  catalog_hash: string
  score: number
  verdict: string
  files: number
  candidates: number
  findings: unknown[]
  defects: Defect[]
  rejections: unknown[]
}

type JobStatus = 'pending' | 'running' | 'done' | 'failed'

interface AuditJob {
  audit_id: string
  status: JobStatus
  repo: string
  repo_url: string
  frameworks: string[]
  progress: {
    cloning?: number
    cloned?: number
    auditing?: number
    total?: number
    current_file?: string
  }
  result: AuditResult | null
  error: string | null
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000'
const POLL_INTERVAL_MS = 2000

const FRAMEWORK_OPTIONS = ['DPDP', 'SOC2', 'GDPR', 'HIPAA']

const COUNTRY_FRAMEWORK_MAP: Record<string, string[]> = {
  India: ['DPDP'],
  EU: ['GDPR'],
  US: ['SOC2', 'HIPAA'],
}

const VERDICT_COLOR: Record<string, string> = {
  COMPLIANT: '#22c55e',
  'AT RISK': '#eab308',
  'NOT COMPLIANT': '#f97316',
  'CRITICAL EXPOSURE': '#ef4444',
}

const VERDICT_BADGE: Record<string, string> = {
  COMPLIANT: '🟢',
  'AT RISK': '🟡',
  'NOT COMPLIANT': '🟠',
  'CRITICAL EXPOSURE': '🔴',
}

// ---------------------------------------------------------------------------
// Helper components
// ---------------------------------------------------------------------------

function SeverityBadge({ severity }: { severity: Severity }) {
  return (
    <span className={`severity severity-${severity}`}>
      {severity.charAt(0).toUpperCase() + severity.slice(1)}
    </span>
  )
}

function DefectCard({ defect, index }: { defect: Defect; index: number }) {
  const [expanded, setExpanded] = useState(false)

  return (
    <article className="finding defect-card">
      <div className="defect-header" onClick={() => setExpanded((v) => !v)}>
        <SeverityBadge severity={defect.severity} />
        <div className="defect-summary">
          <strong>{defect.obligations[0]?.title ?? 'Compliance violation'}</strong>
          <code className="defect-path">
            {defect.path}:{defect.line_start}
          </code>
        </div>
        <span className="defect-toggle">
          {expanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        </span>
      </div>

      {expanded && (
        <div className="defect-body">
          <pre className="evidence-block">{defect.evidence}</pre>

          {defect.fix && (
            <div className="fix-block">
              <p className="fix-label">Suggested fix</p>
              <pre className="fix-code">{defect.fix}</pre>
            </div>
          )}

          <div className="obligations-list">
            {defect.obligations.map((ob) => (
              <div className="obligation" key={ob.control_id}>
                <FileWarning size={12} />
                <span>
                  <strong>{ob.framework}</strong> · {ob.control} — {ob.exposure}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </article>
  )
}

function ScoreRing({ score, verdict }: { score: number; verdict: string }) {
  const r = 38
  const circ = 2 * Math.PI * r
  const filled = circ * (score / 100)
  const color = VERDICT_COLOR[verdict] ?? '#6b7280'

  return (
    <div className="score-ring-wrap">
      <svg width={96} height={96} viewBox="0 0 96 96">
        <circle cx={48} cy={48} r={r} fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth={8} />
        <circle
          cx={48}
          cy={48}
          r={r}
          fill="none"
          stroke={color}
          strokeWidth={8}
          strokeLinecap="round"
          strokeDasharray={`${filled} ${circ}`}
          transform="rotate(-90 48 48)"
          style={{ transition: 'stroke-dasharray 0.8s ease' }}
        />
      </svg>
      <div className="score-text">
        <span className="score-value">{score}</span>
        <span className="score-label">/ 100</span>
      </div>
    </div>
  )
}

function ProgressBar({ job }: { job: AuditJob }) {
  const { progress, status } = job
  if (status === 'pending') return <p className="progress-msg">Queued…</p>

  if (progress.cloning && !progress.cloned) {
    return <p className="progress-msg">Cloning repository…</p>
  }

  if (progress.cloned && progress.total) {
    const pct = Math.round(((progress.auditing ?? 0) / progress.total) * 100)
    return (
      <div className="progress-wrap">
        <div className="progress-track">
          <div className="progress-fill" style={{ width: `${pct}%` }} />
        </div>
        <p className="progress-msg">
          Auditing file {progress.auditing}/{progress.total}{' '}
          {progress.current_file && (
            <code className="progress-file">{progress.current_file}</code>
          )}
        </p>
      </div>
    )
  }

  return <p className="progress-msg">Preparing…</p>
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function Page() {
  const [repoUrl, setRepoUrl] = useState('')
  const [country, setCountry] = useState('India')
  const [frameworks, setFrameworks] = useState<string[]>(['DPDP', 'SOC2'])

  const [job, setJob] = useState<AuditJob | null>(null)
  const [error, setError] = useState<string | null>(null)

  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // Sync framework suggestions when country changes
  useEffect(() => {
    const suggested = COUNTRY_FRAMEWORK_MAP[country]
    if (suggested) setFrameworks(suggested)
  }, [country])

  // ---------------------------------------------------------------------------
  // Polling
  // ---------------------------------------------------------------------------

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current)
      pollRef.current = null
    }
  }, [])

  const startPolling = useCallback(
    (auditId: string) => {
      stopPolling()
      pollRef.current = setInterval(async () => {
        try {
          const res = await fetch(`${API}/api/audit/${auditId}`)
          if (!res.ok) throw new Error(`HTTP ${res.status}`)
          const data: AuditJob = await res.json()
          setJob(data)
          if (data.status === 'done' || data.status === 'failed') stopPolling()
        } catch (err) {
          setError(String(err))
          stopPolling()
        }
      }, POLL_INTERVAL_MS)
    },
    [stopPolling],
  )

  // Cleanup on unmount
  useEffect(() => () => stopPolling(), [stopPolling])

  // ---------------------------------------------------------------------------
  // Start audit
  // ---------------------------------------------------------------------------

  async function startAudit() {
    if (!repoUrl.trim() || job?.status === 'running' || job?.status === 'pending') return
    setError(null)
    setJob(null)

    try {
      const res = await fetch(`${API}/api/audit`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          repo_url: repoUrl.trim(),
          frameworks,
          max_files: 12,
        }),
      })

      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.detail ?? `HTTP ${res.status}`)
      }

      const data: AuditJob = await res.json()
      setJob(data)
      startPolling(data.audit_id)
    } catch (err) {
      setError(String(err))
    }
  }

  // ---------------------------------------------------------------------------
  // Derived UI state
  // ---------------------------------------------------------------------------

  const isScanning = job?.status === 'pending' || job?.status === 'running'
  const scanDone = job?.status === 'done'
  const scanFailed = job?.status === 'failed'
  const result = job?.result ?? null

  function toggleFramework(fw: string) {
    setFrameworks((prev) =>
      prev.includes(fw) ? prev.filter((f) => f !== fw) : [...prev, fw],
    )
  }

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <main className="dashboard-shell">
      {/* Header */}
      <header className="dashboard-header">
        <div className="brand-mark">
          <span>
            <ShieldCheck size={18} />
          </span>{' '}
          REGION<span>GUARD</span>
        </div>
        <nav className="dashboard-nav" aria-label="Primary navigation">
          <a className="active" href="#new-audit">
            New audit
          </a>
          {result && (
            <a href="#findings" className="">
              Findings
            </a>
          )}
        </nav>
        <div className="header-right">
          <span className="secure-dot" />
          <label className="sr-only" htmlFor="country-select">
            Choose compliance country
          </label>
          <select
            id="country-select"
            className="country-select"
            value={country}
            onChange={(e) => setCountry(e.target.value)}
          >
            <option>India</option>
            <option>EU</option>
            <option>US</option>
          </select>
          <span>region</span>
        </div>
      </header>

      <div className="dashboard-content" id="new-audit">
        {/* Hero heading */}
        <div className="dashboard-heading">
          <div>
            <p className="kicker">
              {country.toUpperCase()} / COMPLIANCE CONTROL
            </p>
            <h1>
              Builds in {country},{' '}
              <em>protected.</em>
            </h1>
            <p className="subtitle">
              Paste a repository and surface application risks against{' '}
              {country === 'India'
                ? "India's digital laws and security rules"
                : country === 'EU'
                ? 'EU privacy and data regulations'
                : 'US compliance and security frameworks'}
              .
            </p>
          </div>
        </div>

        {/* Intake card */}
        <section className="repo-intake repo-intake-stack" aria-labelledby="repo-intake-title">
          <div className="repo-intake-top">
            <div>
              <p className="eyebrow">NEW REPOSITORY</p>
              <h2 id="repo-intake-title">Start a compliance audit</h2>
              <p>We review source code for privacy, security, and data-governance issues.</p>
            </div>
            <div className="audit-scope">
              <CheckCircle2 size={15} /> {frameworks.join(', ') || 'No framework'} policy set
            </div>
          </div>

          {/* Framework picker */}
          <div className="framework-picker">
            {FRAMEWORK_OPTIONS.map((fw) => (
              <button
                key={fw}
                className={`fw-chip${frameworks.includes(fw) ? ' fw-chip-active' : ''}`}
                onClick={() => toggleFramework(fw)}
                type="button"
                aria-pressed={frameworks.includes(fw)}
              >
                {fw}
              </button>
            ))}
          </div>

          {/* Repo input + submit */}
          <div className="repo-form">
            <div className="repo-input-wrap">
              <GitBranch size={17} />
              <input
                id="repo-input"
                value={repoUrl}
                onChange={(e) => {
                  setRepoUrl(e.target.value)
                  setError(null)
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.nativeEvent.isComposing && e.keyCode !== 229) {
                    startAudit()
                  }
                }}
                placeholder="https://github.com/your-team/your-repo"
                aria-label="Repository URL"
                disabled={isScanning}
              />
              <span>.IN</span>
            </div>
            <button className="start-button" onClick={startAudit} disabled={isScanning || !repoUrl.trim()}>
              {isScanning ? (
                <>
                  <Loader2 size={16} className="spin" /> Scanning…
                </>
              ) : (
                <>
                  Start audit <ArrowUpRight size={16} />
                </>
              )}
            </button>
          </div>

          {/* Error banner */}
          {error && (
            <div className="error-banner" role="alert">
              <XCircle size={16} />
              <span>{error}</span>
              {error.includes('fetch') && (
                <em> — Is the backend running? <code>uvicorn api:app --reload --port 8000</code></em>
              )}
            </div>
          )}

          {/* In-progress */}
          {isScanning && job && (
            <div className="scan-progress" aria-live="polite">
              <Loader2 size={14} className="spin" />
              <ProgressBar job={job} />
            </div>
          )}

          {/* Failed */}
          {scanFailed && (
            <div className="error-banner scan-fail" role="alert">
              <XCircle size={16} />
              <span>Audit failed: {job?.error}</span>
              <button className="retry-btn" onClick={startAudit}>
                <RefreshCw size={14} /> Retry
              </button>
            </div>
          )}

          {/* Results */}
          {scanDone && result && (
            <div className="results-section" id="findings" aria-live="polite">
              {/* Score summary */}
              <div className="score-summary">
                <ScoreRing score={result.score} verdict={result.verdict} />
                <div className="score-meta">
                  <p className="score-verdict" style={{ color: VERDICT_COLOR[result.verdict] }}>
                    {VERDICT_BADGE[result.verdict]} {result.verdict}
                  </p>
                  <p className="score-repo">
                    <ClipboardList size={14} /> {result.repo}{' '}
                    <code>@{result.commit.slice(0, 8)}</code>
                  </p>
                  <div className="score-stats">
                    <span>{result.files} files</span>
                    <span>{result.candidates} suspects</span>
                    <span>{result.defects.length} defects</span>
                    <span className="tag-mode">{result.mode}</span>
                  </div>
                </div>
              </div>

              {/* Findings list */}
              <div className="findings-preview" aria-live="polite">
                <div className="findings-heading">
                  <div>
                    <p className="eyebrow">AUDIT COMPLETE</p>
                    <h3>
                      {result.defects.length === 0
                        ? 'No violations found 🎉'
                        : `${result.defects.length} defect${result.defects.length !== 1 ? 's' : ''} found`}
                    </h3>
                  </div>
                  <span className="finding-count">{result.defects.length} findings</span>
                </div>

                <div className="finding-list">
                  {result.defects.map((defect, i) => (
                    <DefectCard key={defect.defect_id} defect={defect} index={i} />
                  ))}
                  {result.defects.length === 0 && (
                    <div className="no-findings">
                      <CheckCircle2 size={32} color="#22c55e" />
                      <p>No confirmed violations in the analysed files.</p>
                    </div>
                  )}
                </div>

                <p className="disclaimer">
                  Checks are guidance for engineering review, not legal advice. Confirm
                  applicability with your privacy and security team.
                </p>
              </div>
            </div>
          )}

          {/* Default "what we check" preview (no scan yet) */}
          {!job && !error && (
            <div className="findings-preview" aria-live="polite">
              <div className="findings-heading">
                <div>
                  <p className="eyebrow">WHAT WE CHECK</p>
                  <h3>Vulnerabilities and regulatory gaps</h3>
                </div>
                <span className="finding-count">Live analysis</span>
              </div>
              <div className="finding-list placeholder-findings">
                {[
                  {
                    severity: 'high' as Severity,
                    title: 'Personal data exposed in logs',
                    detail: 'Email and phone fields appear in debug output.',
                    law: 'DPDP Act 2023 · data minimisation',
                  },
                  {
                    severity: 'medium' as Severity,
                    title: 'Missing consent record',
                    detail: 'Collection flow does not retain a verifiable consent event.',
                    law: 'DPDP Act 2023 · consent obligations',
                  },
                  {
                    severity: 'low' as Severity,
                    title: 'Third-party transfer needs review',
                    detail: 'External analytics endpoint missing a documented purpose.',
                    law: 'CERT-In Directions 2022 · reporting and records',
                  },
                ].map((f) => (
                  <article className="finding" key={f.title}>
                    <SeverityBadge severity={f.severity} />
                    <div>
                      <strong>{f.title}</strong>
                      <p>{f.detail}</p>
                      <small>
                        <FileWarning size={12} /> {f.law}
                      </small>
                    </div>
                    <AlertTriangle size={16} className="finding-icon" />
                  </article>
                ))}
              </div>
              <p className="disclaimer">
                Checks are guidance for engineering review, not legal advice.
              </p>
            </div>
          )}
        </section>
      </div>
    </main>
  )
}
