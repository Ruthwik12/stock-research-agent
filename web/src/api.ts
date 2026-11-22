// Types and calls for the FastAPI backend (api/main.py).

export interface Company {
  ticker: string
  name: string | null
  sector: string | null
  price: number | null
  price_date: string | null
  momentum_6m: number | null
  volatility: number | null
  pe_ratio: number | null
  last_surprise_pct: number | null
  last_earnings_date: string | null
}

export interface Claim {
  text: string
  evidence_ids: string[]
  status: 'supported' | 'partially_supported' | 'unverified' | 'unsupported'
  reason: string
}

export interface Source {
  citation: string
  url: string
  chunk_id: string
  distance: number
}

export interface Report {
  question: string
  ticker: string
  as_of: string
  llm: string
  evidence_strength: 'strong' | 'moderate' | 'weak' | 'insufficient'
  claims: Claim[]
  dropped_claims: Claim[]
  gaps: string[]
  missing_risks: string[]
  sources: Record<string, Source>
  disclaimer: string
  trace_file: string
}

const OFFLINE = 'Cannot reach the backend. Start it (uvicorn on port 8000) and refresh the page.'

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init).catch(() => { throw new Error(OFFLINE) })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    // A 502/504 with no JSON detail comes from the dev proxy: the backend isn't running.
    if (!body?.detail && (res.status === 502 || res.status === 504)) throw new Error(OFFLINE)
    throw new Error(body?.detail ?? `Request failed (${res.status})`)
  }
  return res.json() as Promise<T>
}

export interface ScreenRow {
  ticker: string
  name: string | null
  sector: string | null
  score: number | null
  signals: { excess_return_6m: number | null; earnings_yield: number | null; eps_surprise_pct: number | null }
  triggers: string[]
  shortlisted: boolean
  reason: string
  filings: FilingStatus
}

export interface FilingStatus {
  ticker: string
  status: 'ready' | 'queued' | 'running' | 'failed' | 'not_prepared'
  message: string
}

export interface Screen {
  as_of: string | null
  universe_size: number
  rows: ScreenRow[]
}

export const getCompanies = () => request<Company[]>('/api/companies')

export const getScreen = () => request<Screen>('/api/screener')

export interface Forecast {
  ticker: string
  as_of: string
  horizon_days: number
  p_quant: number
  evidence_adjustment: number
  shrink_weight: number
  p_final: number
  rule_adjustment: number
  rules_applied: { rule_id: number; field: string; value: string; delta: number }[]
  needs_review: boolean
  adjustments: { points: number; direction: string; strength: string; reason: string; claims: string[] }[]
  model: { holdout_brier: Record<string, number> | null; calibration_slope: number; auc_by_date?: number | null }
  prediction_id: number
  disclaimer: string
  debate?: Debate
}

export interface DebateThesis {
  id: string
  side: 'bull' | 'bear'
  text: string
  evidence_ids: string[]
  score: number
  first_score?: number
  protected?: boolean
  critic_reason?: string
  rebuttal?: { counter_argument: string; by: string }
  passed?: boolean
  problems?: string[]
  send_backs?: { query: string; added: string[] }[]
  score_before_send_back?: number
}

export interface Debate {
  contested: boolean
  leaning: 'bull' | 'bear' | null
  survivors: DebateThesis[]
  pruned: DebateThesis[]
  checked: DebateThesis[]
  send_back_log: { thesis: string; query: string; added: string[] }[] | null
  trace_file: string
}

export const getForecast = (ticker: string, asOf: string, traceFile: string | null) =>
  request<Forecast>('/api/forecast', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ticker, as_of: asOf || null, trace_file: traceFile }),
  })

export interface PickThesis { side: 'bull' | 'bear'; text: string; score: number; evidence_ids: string[] }

export interface Pick {
  ticker: string
  rank: number | null
  error?: string
  p_final: number
  p_quant: number
  evidence_adjustment: number
  needs_review: boolean
  contested: boolean
  leaning: 'bull' | 'bear' | null
  thesis: PickThesis | null
  main_risk: PickThesis | null
  critic_risks: string[]
  sources: Record<string, { citation: string; url: string }>
  research_trace?: string | null
}

export interface Picks {
  run: { run_id: number; as_of: string; status: 'running' | 'done' | 'failed'; message: string;
         report_path: string | null } | null
  picks: Pick[]
}

export interface Position {
  position_id: number
  ticker: string
  opened: string
  entry_price: number
  horizon_end: string
  status: 'open' | 'closed'
  flags: string[]
  thesis_status: string | null
  thesis_note: string | null
  thesis: PickThesis | null
  excess_return: number | null
  close_reason: string | null
  source: 'system' | 'mine'
}

export const getPositions = () => request<Position[]>('/api/positions')
export const addHolding = (h: { ticker: string; opened: string; price: number | null; note: string }) =>
  request<{ position_id: number }>('/api/positions', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(h),
  })
export const reviewPositions =() => request<unknown>('/api/positions/review', { method: 'POST' })
export const closePosition = (id: number) => request<unknown>(`/api/positions/${id}/close`, { method: 'POST' })

export interface TrackRecord {
  n: number
  base_rate: number
  brier: number
  brier_always_50: number
  brier_base_rate: number
}

export interface Rule {
  rule_id: number
  field: string
  value: string
  delta: number
  status: 'proposed' | 'active' | 'rejected' | 'retired'
  evidence: { learned_on?: string; n_learn?: number; tested_on?: string; n_test?: number;
              brier_test_before?: number; brier_test_after?: number }
  note: string | null
}

export interface Reflection {
  live: TrackRecord | null
  backtest: TrackRecord | null
  learned_from: string
  segments: { field: string; value: string; n: number; mean_forecast: number; hit_rate: number; gap: number }[]
  candidates_rejected_out_of_sample: number
  pruned_audit: { pruned_theses: number; side_won: number; rate: number } | null
  rules: Rule[]
}

export const getReflection = () => request<Reflection>('/api/reflector')
export const decideRule = (id: number, status: string) =>
  request<unknown>(`/api/rules/${id}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ status }),
  })

export interface TraceSummary {
  name: string
  saved: string
  ticker: string
  question: string
  as_of: string
  strength: Report['evidence_strength']
  kept: number
  dropped: number
  debate: { leaning: 'bull' | 'bear' | null; contested: boolean } | null
  p_final: number | null
  picks_run: number | null
}

export interface TraceEvent { ts: string; at: string; node: string; [key: string]: unknown }
export interface Evidence { eid: string; form_type: string; filing_date: string; section: string; source_url: string; text: string }
// score is absent in searches logged before the reranker (2026-11-19); those were ranked by distance alone.
export interface Candidate { chunk_id: string; status: string; score?: number; distance: number; citation: string; preview: string }
export interface Search { ts: string; caller: string; query: string; candidates: Candidate[] }
export interface ScoredThesis extends DebateThesis {
  parts?: Record<string, number>
  missing_query?: string
}

export interface TraceDetail {
  name: string
  research: {
    question: string; ticker: string; as_of: string
    evidence: Evidence[]
    draft: { claims: { text: string; evidence_ids: string[] }[] }
    checked_claims: Claim[]
    final: Report
    events: TraceEvent[]
  }
  debate: {
    evidence: Evidence[]
    theses: { id: string; side: 'bull' | 'bear'; text: string; evidence_ids: string[] }[]
    rebuttals: { thesis_id: string; counter_argument: string; by: string }[]
    result: { checked: ScoredThesis[]; scored: ScoredThesis[]; survivors: ScoredThesis[]; pruned: ScoredThesis[];
              contested: boolean; leaning: 'bull' | 'bear' | null }
    events: TraceEvent[]
  } | null
  searches: Search[]
  prediction: { created: string; p_quant: number; evidence_adjustment: number; shrink_weight: number;
                rule_adjustment: number; p_final: number; needs_review: number;
                adjustments: { points: number; reason: string }[] } | null
}

export const getTraces = () => request<TraceSummary[]>('/api/traces')
export const getTrace = (name: string) => request<TraceDetail>(`/api/traces/${encodeURIComponent(name)}`)

export const getPicks = () => request<Picks>('/api/picks')

export const startPicks = () => request<{ run_id: number }>('/api/picks/run', { method: 'POST' })

export const prepareFilings = (ticker: string) =>
  request<FilingStatus>(`/api/filings/${ticker}`, { method: 'POST' })

export const runResearch = (ticker: string, question: string, asOf: string) =>
  request<Report>('/api/research', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ticker, question, as_of: asOf || null }),
  })
