// Traces tab: every saved research run, replayed step by step: which agent did what, and why.
import { Component, useEffect, useState, type ReactNode } from 'react'
import {
  getTrace, getTraces, type Evidence, type ScoredThesis, type Search, type TraceDetail, type TraceEvent,
  type TraceSummary,
} from './api'

const pct = (v: number) => `${(v * 100).toFixed(1)}%`
const pts = (v: number) => `${v >= 0 ? '+' : ''}${(v * 100).toFixed(1)} pts`
const list = (v: unknown) => (Array.isArray(v) ? (v as string[]) : [])
const clock = (iso: string) => iso.slice(11, 19)

// Which agent each graph step belongs to, and its badge colour.
const AGENT: Record<string, [string, string]> = {
  plan: ['Researcher', 'info'], retrieve: ['Researcher', 'info'], assess: ['Researcher', 'info'],
  answer: ['Researcher', 'info'], revise: ['Critic', 'warn'], verify: ['Critic', 'warn'],
  finalize: ['Researcher', 'info'], bull: ['Bull analyst', 'bull'], bear: ['Bear analyst', 'bear'],
  check: ['Critic (code)', 'warn'], score: ['Critic', 'warn'], send_back: ['Critic → Researcher', 'warn'],
  prune: ['Debate (code)', 'muted'], rebut: ['Bull and Bear', 'muted'], rescore: ['Critic', 'warn'],
  decide: ['Debate (code)', 'muted'],
}

function Step({ at, node, title, children }: { at: string; node: string; title: ReactNode; children?: ReactNode }) {
  const [agent, cls] = AGENT[node] ?? ['Forecaster', 'info']
  return (
    <li className="step">
      <div className="step-head">
        <span className="hint">{clock(at)}</span> <span className={`badge ${cls}`}>{agent}</span> <strong>{title}</strong>
      </div>
      {children && <div className="step-body">{children}</div>}
    </li>
  )
}

// The retriever's drop reasons in plain words (the exact rule shows on hover).
const dropReason = (status: string) =>
  status.includes('distance') ? 'not relevant enough' : status.includes('final_k') ? 'not in the top 5'
    : status.includes('duplicate') ? 'duplicate' : status.includes('news') ? 'news limit reached'
      : status.replace('dropped: ', '')

function Searches({ searches }: { searches: Search[] }) {
  if (searches.length === 0) return <p className="hint">Search log not found for this step.</p>
  return (
    <ul className="plain">
      {searches.map((s, i) => {
        const kept = s.candidates.filter((c) => c.status === 'kept')
        return (
          <li key={i}>
            <details>
              <summary>“{s.query}”: kept {kept.length} of {s.candidates.length} passages</summary>
              <table className="companies wrap small-table">
                <thead><tr><th>Result</th><th className="n">Score</th><th>Passage</th></tr></thead>
                <tbody>{s.candidates.map((c) => (
                  <tr key={c.chunk_id} className="static">
                    <td>{c.status === 'kept' ? <span className="badge bull">kept</span>
                      : <span className="badge muted" title={c.status}>{dropReason(c.status)}</span>}</td>
                    <td className="n">{c.score !== undefined ? c.score.toFixed(2)
                      : <span title="vector distance: lower is closer (search logged before the reranker)">
                        dist {c.distance.toFixed(2)}</span>}</td>
                    <td>{c.citation}<br /><span className="hint">{c.preview}…</span></td>
                  </tr>
                ))}</tbody>
              </table>
            </details>
          </li>
        )
      })}
    </ul>
  )
}

function Thesis({ t, score }: { t: { id: string; side: string; text: string; evidence_ids: string[] }; score?: ReactNode }) {
  return (
    <div className={`thesis ${t.side}`}>
      <p><strong>{t.id}</strong>{score} {t.text} <span className="cite">[{t.evidence_ids.join(', ')}]</span></p>
    </div>
  )
}

function Timeline({ d }: { d: TraceDetail }) {
  const r = d.research
  const searchesFor = (queries: string[], caller: string) =>
    d.searches.filter((s) => s.caller === caller && queries.includes(s.query))
  const steps: ReactNode[] = []

  // Researcher and critic (research graph).
  let next: string[] = []
  r.events.forEach((e: TraceEvent, i) => {
    const k = `r${i}`
    if (e.node === 'plan') {
      next = list(e.queries)
      steps.push(<Step key={k} at={e.at} node="plan" title={`Planned ${next.length} searches`}>
        <ul>{next.map((q) => <li key={q}>{q}</li>)}</ul></Step>)
    } else if (e.node === 'retrieve') {
      steps.push(<Step key={k} at={e.at} node="retrieve"
        title={`Search round ${e.round}: ${e.evidence_total} passages now in evidence`}>
        <Searches searches={searchesFor(next, 'research_graph')} /></Step>)
    } else if (e.node === 'assess') {
      next = list(e.follow_up)
      steps.push(<Step key={k} at={e.at} node="assess"
        title={e.sufficient ? 'Judged the evidence sufficient' : 'Judged the evidence not enough: searching again'}>
        <p>{String(e.missing ?? '')}</p>
        {!e.sufficient && <p className="hint">Next searches: {next.join(' · ')}</p>}</Step>)
    } else if (e.node === 'answer') {
      steps.push(<Step key={k} at={e.at} node="answer"
        title={e.answerable ? `Drafted ${e.claims} claims, each citing evidence` : 'Judged the question unanswerable from the evidence'}>
        {r.draft.claims.length > 0 && <details><summary>Draft claims</summary><ol>{r.draft.claims.map((c, j) =>
          <li key={j}>{c.text} <span className="cite">[{c.evidence_ids.join(', ')}]</span></li>)}</ol></details>}
      </Step>)
    } else if (e.node === 'verify') {
      const bad = r.checked_claims.filter((c) => c.status !== 'supported')
      steps.push(<Step key={k} at={e.at} node="verify"
        title={`Checked ${e.total} claims against their cited text: ${e.rejected} rejected`}>
        {bad.length > 0 && <ul>{bad.map((c, j) => <li key={j}><span className="badge bear">{c.status.replace('_', ' ')}</span>
          {c.text} <span className="hint">: {c.reason}</span></li>)}</ul>}
        {r.checked_claims.length > 0 && <details><summary>Every claim with the critic's reason</summary><ol>{r.checked_claims.map((c, j) =>
          <li key={j}><span className={`badge ${c.status === 'supported' ? 'bull' : 'bear'}`}>{c.status.replace('_', ' ')}</span>
            {c.text} <span className="hint">: {c.reason}</span></li>)}</ol></details>}
      </Step>)
    } else if (e.node === 'revise') {
      steps.push(<Step key={k} at={e.at} node="revise" title="Too many claims rejected: sent back to the researcher to revise" />)
    } else if (e.node === 'finalize') {
      steps.push(<Step key={k} at={e.at} node="finalize"
        title={`Report: evidence ${e.strength}, ${e.kept} claims kept, ${e.dropped} removed`} />)
    }
  })

  // Bull, bear and critic (debate graph).
  const db = d.debate
  if (db) {
    const res = db.result
    const scored: Record<string, ScoredThesis> = Object.fromEntries(res.scored.map((t) => [t.id, t]))
    db.events.forEach((e, i) => {
      const k = `d${i}`
      if (e.node === 'bull' || e.node === 'bear') {
        steps.push(<Step key={k} at={e.at} node={e.node} title={`Wrote ${e.theses} ${e.node} theses from the same evidence`}>
          {db.theses.filter((t) => t.side === e.node).map((t) => <Thesis key={t.id} t={t} />)}</Step>)
      } else if (e.node === 'check') {
        const failed = res.checked.filter((t) => !t.passed)
        steps.push(<Step key={k} at={e.at} node="check" title={`Checked citations and numbers: ${failed.length} thesis failed`}>
          {failed.length > 0 && <ul>{failed.map((t) => <li key={t.id}><strong>{t.id}</strong>: {t.problems?.join('; ')}</li>)}</ul>}
        </Step>)
      } else if (e.node === 'score' && !e.rescore) {
        steps.push(<Step key={k} at={e.at} node="score" title={`Scored ${e.scored} theses from 0 to 10`}>
          {res.scored.map((t) => (
            <details key={t.id}>
              <summary><span className={t.side === 'bull' ? 'up' : 'down'}>{t.id}</span>:{' '}
                {(t.score_before_send_back ?? t.score).toFixed(1)}</summary>
              <p className="hint">{Object.entries(t.parts ?? {}).map(([p, v]) => `${p.replace('_', ' ')} ${v}`).join(' · ')}</p>
              <p>{t.critic_reason}</p>
            </details>
          ))}
        </Step>)
      } else if (e.node === 'score') {
        steps.push(<Step key={k} at={e.at} node="score" title={`Re-scored ${e.scored} thesis with the new evidence`} />)
      } else if (e.node === 'send_back') {
        const reqs = (e.requests as { thesis: string; query: string; added: string[] }[]) ?? []
        steps.push(<Step key={k} at={e.at} node="send_back"
          title={`Promising but thinly supported: sent ${reqs.length} thesis back for more evidence`}>
          {reqs.map((q, j) => (
            <div key={j}>
              <p><strong>{q.thesis}</strong> ({scored[q.thesis]?.score_before_send_back?.toFixed(1) ?? '?'} before):
                added {q.added.length ? q.added.join(', ') : 'nothing new'}</p>
              <Searches searches={searchesFor([q.query], 'debate_send_back')} />
            </div>
          ))}
        </Step>)
      } else if (e.node === 'prune') {
        steps.push(<Step key={k} at={e.at} node="prune"
          title={`Kept ${list(e.kept).join(', ')}; pruned ${res.pruned.length}`}>
          {res.pruned.map((t) => <Thesis key={t.id} t={t} score={<span className="hint"> (score {t.score?.toFixed(1)}, pruned)</span>} />)}
        </Step>)
      } else if (e.node === 'rebut') {
        steps.push(<Step key={k} at={e.at} node="rebut" title="Each survivor answered by the other side's strongest rebuttal">
          {db.rebuttals.map((b) => <details key={b.thesis_id}><summary>{b.by} rebuts {b.thesis_id}</summary>
            <p>{b.counter_argument}</p></details>)}
        </Step>)
      } else if (e.node === 'rescore') {
        steps.push(<Step key={k} at={e.at} node="rescore" title="Re-scored the survivors after the rebuttals">
          <ul>{res.survivors.map((t) => <li key={t.id}><span className={t.side === 'bull' ? 'up' : 'down'}>{t.id}</span>:{' '}
            {t.first_score?.toFixed(1)} → <strong>{t.score.toFixed(1)}</strong>
            {t.protected && <span className="hint"> (kept as best of its side)</span>}
            {t.critic_reason && <details><summary className="hint">critic's reason</summary><p>{t.critic_reason}</p></details>}
          </li>)}</ul>
        </Step>)
      } else if (e.node === 'decide') {
        steps.push(<Step key={k} at={e.at} node="decide"
          title={e.contested ? 'Contested: strong cases on both sides, adjustment halved'
            : e.leaning ? `Leaning ${e.leaning}` : 'No clear lean'} />)
      }
    })
  }

  // Forecaster.
  const p = d.prediction
  if (p) {
    steps.push(<Step key="f" at={p.created} node="forecast" title={`Forecast ${pct(p.p_final)} to beat the S&P 500 in 90 days`}>
      <table className="breakdown"><tbody>
        <tr><td>Price model</td><td className="n">{pct(p.p_quant)}</td></tr>
        <tr><td>Debate adjustment {pts(p.evidence_adjustment)} × weight {p.shrink_weight.toFixed(2)}</td>
          <td className="n">{pts(p.evidence_adjustment * p.shrink_weight)}</td></tr>
        <tr><td>Correction rules you approved</td><td className="n">{pts(p.rule_adjustment)}</td></tr>
        <tr><td><strong>Final</strong></td><td className="n"><strong>{pct(p.p_final)}</strong></td></tr>
      </tbody></table>
      {p.adjustments.length > 0 && <ul>{p.adjustments.map((a, j) =>
        <li key={j}><span className={a.points >= 0 ? 'up' : 'down'}>{pts(a.points)}</span> {a.reason}</li>)}</ul>}
    </Step>)
  }
  return <ol className="timeline">{steps}</ol>
}

function EvidenceList({ evidence }: { evidence: Evidence[] }) {
  return (
    <details>
      <summary>Evidence the agents could cite ({evidence.length} passages)</summary>
      <ul className="plain">{evidence.map((e) => (
        <li key={e.eid}><span className="cite">[{e.eid}]</span>{' '}
          <a href={e.source_url} target="_blank" rel="noreferrer">{e.form_type} filed {e.filing_date}, {e.section}</a></li>
      ))}</ul>
    </details>
  )
}

// A trace in an older format must not blank the whole page: show the error in its card instead.
class TraceBoundary extends Component<{ children: ReactNode }, { error: string }> {
  state = { error: '' }
  static getDerivedStateFromError(e: Error) { return { error: e.message } }
  render() {
    return this.state.error
      ? <p className="error">This trace could not be displayed ({this.state.error}). The file is in logs/runs/.</p>
      : this.props.children
  }
}

export default function TracesView({ open }: { open: string | null }) {
  const [rows, setRows] = useState<TraceSummary[] | null>(null)
  const [name, setName] = useState<string | null>(open)
  const [detail, setDetail] = useState<TraceDetail | null>(null)
  const [error, setError] = useState('')

  useEffect(() => { getTraces().then(setRows).catch((e) => setError(String(e))) }, [])
  useEffect(() => { if (open) setName(open) }, [open])
  useEffect(() => {
    if (!name) return
    setDetail(null)
    getTrace(name).then((d) => {
      setDetail(d)
      setTimeout(() => document.getElementById('trace-detail')?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 0)
    }).catch((e) => setError(String(e)))
  }, [name])

  return (
    <>
      <section className="card">
        <h2>Traces</h2>
        <p className="hint">Every research run is saved. Click one to replay it: each agent's step, what it searched,
          what it kept or rejected and why, and how the forecast was built.</p>
        {error && <p className="error">{error}</p>}
        {!rows ? <p className="hint">Loading…</p> : (
          <div className="table-wrap">
            <table className="companies wrap">
              <thead><tr><th>Saved</th><th>Ticker</th><th>Question</th><th>Evidence</th><th className="n">Claims</th>
                <th>Debate</th><th className="n">Forecast</th><th>From</th></tr></thead>
              <tbody>{rows.map((t) => (
                <tr key={t.name} className={t.name === name ? 'selected' : ''} onClick={() => setName(t.name)}>
                  <td>{t.saved.slice(0, 16).replace('T', ' ')}</td>
                  <td><strong>{t.ticker}</strong></td>
                  <td>{t.question}</td>
                  <td><span className={`strength ${t.strength}`}>{t.strength}</span></td>
                  <td className="n">{t.kept}{t.dropped > 0 && <span className="hint"> (−{t.dropped})</span>}</td>
                  <td>{t.debate ? (t.debate.contested ? <span className="badge warn">contested</span>
                    : <span className={`badge ${t.debate.leaning ?? 'muted'}`}>{t.debate.leaning ?? 'no lean'}</span>) : '—'}</td>
                  <td className="n">{t.p_final === null ? '—' : pct(t.p_final)}</td>
                  <td className="hint">{t.picks_run ? `Picks run ${t.picks_run}` : 'Research tab'}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </section>
      {name && (
        <section className="card" id="trace-detail">
          {!detail ? <p className="hint">Loading trace…</p> : (<>
            <h3>{detail.research.ticker}: {detail.research.question}</h3>
            <p className="meta">as of {detail.research.as_of} · {detail.research.final.llm} · {detail.name}</p>
            <TraceBoundary key={detail.name}>
              <Timeline d={detail} />
              <EvidenceList evidence={detail.debate?.evidence ?? detail.research.evidence} />
            </TraceBoundary>
          </>)}
        </section>
      )}
    </>
  )
}
