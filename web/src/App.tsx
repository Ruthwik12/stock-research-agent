import { useEffect, useRef, useState } from 'react'
import {
  addHolding, closePosition, decideRule, getCompanies, getForecast, getPicks, getPositions, getReflection, getScreen,
  prepareFilings, reviewPositions, runResearch, startPicks,
  type Company, type Debate, type Forecast, type Picks, type Position, type Reflection, type Report, type Screen,
  type ScreenRow, type TrackRecord,
} from './api'
import TracesView from './Traces'

const pct = (v: number | null) => (v === null ? '—' : `${(v * 100).toFixed(1)}%`)
const num = (v: number | null, digits = 2) => (v === null ? '—' : v.toFixed(digits))
const sign = (v: number | null) => (v === null ? '' : v >= 0 ? 'up' : 'down')

function CompanyTable({ companies, selected, onSelect }: {
  companies: Company[]
  selected: string
  onSelect: (ticker: string) => void
}) {
  return (
    <table className="companies">
      <thead>
        <tr>
          <th>Ticker</th><th>Company</th><th>Sector</th><th className="n">Price</th>
          <th className="n">6-month return</th><th className="n">Volatility</th>
          <th className="n">P/E</th><th className="n">Last EPS surprise</th>
        </tr>
      </thead>
      <tbody>
        {companies.map((c) => (
          <tr key={c.ticker} className={c.ticker === selected ? 'selected' : ''} onClick={() => onSelect(c.ticker)}>
            <td><strong>{c.ticker}</strong></td>
            <td>{c.name ?? '—'}</td>
            <td>{c.sector ?? '—'}</td>
            <td className="n">{num(c.price)}</td>
            <td className={`n ${sign(c.momentum_6m)}`}>{pct(c.momentum_6m)}</td>
            <td className="n">{pct(c.volatility)}</td>
            <td className="n">{num(c.pe_ratio, 1)}</td>
            <td className={`n ${sign(c.last_surprise_pct)}`}>
              {c.last_surprise_pct === null ? '—' : `${c.last_surprise_pct.toFixed(1)}%`}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function PicksView({ data, onRun, onTrace }: { data: Picks; onRun: () => void; onTrace: (file: string) => void }) {
  const run = data.run
  const running = run?.status === 'running'
  return (
    <>
      <p className="row">
        <button onClick={onRun} disabled={running}>{running ? 'Running…' : "Run today's picks"}</button>{' '}
        <span className="hint">
          {running ? `${run.message}. About 2-3 minutes per stock.`
            : run ? `Last run ${run.as_of}: ${run.message}.` : 'No run yet.'}{' '}
          Researches, debates and forecasts up to 5 shortlisted stocks (uses Claude credit).
        </span>
      </p>
      <p className="hint">
        "Model" is the price-signal model: it only ranks stocks against each other (a small out-of-sample edge) and
        centers near the ~48% base rate. The debate adjustment carries the stock-specific judgment.
      </p>
      {data.picks.map((p) => p.error ? (
        <p key={p.ticker} className="error">{p.ticker}: failed ({p.error})</p>
      ) : (
        <div key={p.ticker} className={`pick ${p.needs_review ? 'review-pick' : ''}`}>
          <p>
            <strong>{p.rank ? `${p.rank}. ` : 'Human review: '}{p.ticker}</strong>{' '}
            <span className="big">{(p.p_final * 100).toFixed(1)}%</span>{' '}
            <span className="hint">to beat the S&amp;P 500 in 90 days</span>{' '}
            <span className={`badge ${p.leaning ?? 'muted'}`}>{p.leaning ? `leaning ${p.leaning}` : 'no lean'}</span>
            {p.contested && <span className="badge warn">contested</span>}
            {p.needs_review && <span className="badge bear">outside 35–65%</span>}
          </p>
          {p.thesis && <p><strong>Thesis</strong> ({p.thesis.side}, {p.thesis.score.toFixed(1)}): {p.thesis.text}{' '}
            <span className="cite">[{p.thesis.evidence_ids.join(', ')}]</span></p>}
          {p.main_risk && <p><strong>Main risk</strong> ({p.main_risk.side}, {p.main_risk.score.toFixed(1)}): {p.main_risk.text}{' '}
            <span className="cite">[{p.main_risk.evidence_ids.join(', ')}]</span></p>}
          <p className="hint">
            {Object.entries(p.sources).map(([id, s]) => (
              <span key={id}><a href={s.url} target="_blank" rel="noreferrer">[{id}] {s.citation}</a>{' '}</span>
            ))}
          </p>
          {p.research_trace && <p><button className="small secondary" onClick={() => onTrace(p.research_trace!)}>
            How this pick was made (trace)</button></p>}
        </div>
      ))}
    </>
  )
}

const FLAG_TEXT: Record<string, string> = {
  stop_loss: 'trailing the S&P 500 by 10+ points',
  horizon_reached: '90 days reached: close and score',
  new_filing: 'new filing since entry',
  thesis_weakened: 'thesis weakened',
  thesis_broken: 'thesis broken',
  no_price: 'no price data',
}

const THESIS_BADGE: Record<string, string> = { intact: 'bull', weakened: 'warn', broken: 'bear' }

function PortfolioSummary({ rows }: { rows: Position[] }) {
  const open = rows.filter((p) => p.status === 'open')
  const priced = open.filter((p) => p.excess_return !== null)
  const avg = priced.length ? priced.reduce((s, p) => s + (p.excess_return ?? 0), 0) / priced.length : null
  const beating = priced.filter((p) => (p.excess_return ?? 0) > 0).length
  const flagged = open.filter((p) => p.flags.length > 0).length
  const mine = open.filter((p) => p.source === 'mine').length
  return (
    <div className="tiles">
      <div className="tile"><div className="tile-label">Open positions</div><div className="tile-value">{open.length}</div>
        <div className="hint">{open.length - mine} system · {mine} mine</div></div>
      <div className="tile"><div className="tile-label">Average vs S&amp;P since entry</div>
        <div className={`tile-value ${sign(avg)}`}>{pct(avg)}</div><div className="hint">open positions, equal weight</div></div>
      <div className="tile"><div className="tile-label">Beating the S&amp;P</div>
        <div className="tile-value">{beating} of {priced.length}</div><div className="hint">since each entry</div></div>
      <div className="tile"><div className="tile-label">Need attention</div>
        <div className={`tile-value ${flagged ? 'down' : ''}`}>{flagged}</div><div className="hint">open positions with flags</div></div>
    </div>
  )
}

function PositionsView({ rows, onReview, onClose, reviewing }: {
  rows: Position[]
  onReview: () => void
  onClose: (p: Position) => void
  reviewing: boolean
}) {
  if (rows.length === 0) return <p className="hint">No paper positions yet. Ranked picks above 50% open one.</p>
  return (
    <>
      <PortfolioSummary rows={rows} />
      <p className="row">
        <button onClick={onReview} disabled={reviewing}>{reviewing ? 'Reviewing…' : 'Review open positions'}</button>{' '}
        <span className="hint">Checks stop-loss and horizon, and re-checks each thesis against filings since entry
          (uses Claude credit only when there are new filings). Closing is always your decision.</span>
      </p>
      <div className="table-wrap">
        <table className="companies">
          <thead>
            <tr><th>Ticker</th><th>Opened</th><th className="n">Entry</th><th className="n">vs S&amp;P since entry</th>
              <th>Horizon ends</th><th>Flags</th><th>Thesis</th><th></th></tr>
          </thead>
          <tbody>
            {rows.map((p) => (
              <tr key={p.position_id} className={`static ${p.status === 'closed' ? 'closed' : ''}`}>
                <td><strong>{p.ticker}</strong>{p.source === 'mine' && <span className="badge muted">mine</span>}</td>
                <td>{p.opened}</td>
                <td className="n">{num(p.entry_price)}</td>
                <td className={`n ${sign(p.excess_return)}`}>{pct(p.excess_return)}</td>
                <td>{p.horizon_end}</td>
                <td>
                  {p.status === 'closed' ? <span className="badge muted">closed</span>
                    : p.flags.length ? p.flags.map((f) => <span key={f} className="badge bear">{FLAG_TEXT[f] ?? f}</span>)
                      : <span className="hint">none</span>}
                </td>
                <td title={p.thesis_note ?? ''}>
                  <span className={`badge ${THESIS_BADGE[p.thesis_status ?? ''] ?? 'muted'}`}>{p.thesis_status ?? '—'}</span>
                </td>
                <td>{p.status === 'open' && (
                  <button className="small" onClick={() => onClose(p)}>Close (paper)</button>
                )}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}

function AddHoldingForm({ onAdded }: { onAdded: () => void }) {
  const [ticker, setTicker] = useState('')
  const [opened, setOpened] = useState('')
  const [price, setPrice] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true); setMsg('')
    try {
      await addHolding({ ticker, opened, price: price ? Number(price) : null, note })
      setTicker(''); setOpened(''); setPrice(''); setNote('')
      onAdded()
    } catch (err) {
      setMsg((err as Error).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <form onSubmit={submit} className="ask">
      <h3>Add my holding</h3>
      <p className="hint">A stock you bought yourself. It is tracked against the S&amp;P 500 and reviewed like the
        positions above, marked "mine", and kept out of the system's track record.</p>
      <div className="row">
        <label>Ticker <input value={ticker} onChange={(e) => setTicker(e.target.value)} required size={6}
          placeholder="UNH" /></label>
        <label>Bought on <input type="date" value={opened} onChange={(e) => setOpened(e.target.value)} required /></label>
        <label>Price paid <input type="number" step="0.01" min="0.01" value={price} size={8}
          onChange={(e) => setPrice(e.target.value)} placeholder="that day's close" /></label>
      </div>
      <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2} maxLength={1000}
        placeholder="Optional: why you bought it. Review open positions re-checks this reason against new filings." />
      <div className="row">
        <button type="submit" disabled={busy}>{busy ? 'Adding…' : 'Add holding'}</button>
        {msg && <span className="error">{msg}</span>}
      </div>
    </form>
  )
}

function TrackRecordLine({ label, t }: { label: string; t: TrackRecord | null }) {
  if (!t) return <p className="hint">{label}: no resolved predictions yet.</p>
  return (
    <p>
      <strong>{label}</strong> ({t.n.toLocaleString()} resolved): stocks beat the S&amp;P 500 {pct(t.base_rate)} of
      the time. Brier score {t.brier.toFixed(4)} vs {t.brier_always_50.toFixed(4)} for always 50% and{' '}
      {t.brier_base_rate.toFixed(4)} for the base rate (lower is better).
    </p>
  )
}

function ReflectorView({ r, onDecide }: { r: Reflection; onDecide: (id: number, status: string) => void }) {
  const label = (f: string, v: string) => (f === 'all' ? 'all forecasts' : `${f.replace('_', ' ')} = ${v}`)
  return (
    <>
      <TrackRecordLine label="Live forecasts" t={r.live} />
      <TrackRecordLine label="Backtest (price model only)" t={r.backtest} />
      {r.pruned_audit && (
        <p>Pruned-thesis audit: {r.pruned_audit.side_won} of {r.pruned_audit.pruned_theses} pruned theses
          ({pct(r.pruned_audit.rate)}) were on the side that turned out right.</p>
      )}
      <h4>Where forecasts miss most (learning from {r.learned_from}, segments with 100+ cases)</h4>
      <div className="table-wrap">
        <table className="companies">
          <thead><tr><th>Segment</th><th className="n">Cases</th><th className="n">Mean forecast</th>
            <th className="n">Actually beat S&amp;P</th><th className="n">Gap</th></tr></thead>
          <tbody>{r.segments.slice(0, 8).map((s) => (
            <tr key={s.field + s.value} className="static">
              <td>{label(s.field, s.value)}</td><td className="n">{s.n}</td><td className="n">{pct(s.mean_forecast)}</td>
              <td className="n">{pct(s.hit_rate)}</td><td className={`n ${sign(s.gap)}`}>{pct(s.gap)}</td>
            </tr>))}</tbody>
        </table>
      </div>
      <h4>Correction rules ({r.candidates_rejected_out_of_sample} more candidates failed the out-of-sample test)</h4>
      {r.rules.length === 0 ? <p className="hint">No rules yet.</p> : (
        <div className="table-wrap">
          <table className="companies wrap">
            <thead><tr><th>Rule</th><th className="n">Change</th><th>Evidence (held-out test)</th><th>Status</th><th></th></tr></thead>
            <tbody>{r.rules.map((x) => (
              <tr key={x.rule_id} className="static">
                <td>{label(x.field, x.value)}</td>
                <td className={`n ${sign(x.delta)}`}>{pts(x.delta)}</td>
                <td className="hint">
                  learned on {x.evidence.learned_on} ({x.evidence.n_learn}); on {x.evidence.tested_on} ({x.evidence.n_test})
                  Brier {x.evidence.brier_test_before?.toFixed(4)} → {x.evidence.brier_test_after?.toFixed(4)}
                  {x.note && <><br />{x.note}</>}
                </td>
                <td><span className={`badge ${RULE_BADGE[x.status] ?? 'muted'}`}>{x.status}</span></td>
                <td>
                  {x.status === 'proposed' && (<>
                    <button className="small" onClick={() => onDecide(x.rule_id, 'active')}>Approve</button>{' '}
                    <button className="small" onClick={() => onDecide(x.rule_id, 'rejected')}>Reject</button></>)}
                  {x.status === 'active' && (
                    <button className="small" onClick={() => onDecide(x.rule_id, 'retired')}>Retire</button>)}
                </td>
              </tr>))}</tbody>
          </table>
        </div>
      )}
    </>
  )
}

const RULE_BADGE: Record<string, string> = { active: 'bull', proposed: 'warn', rejected: 'muted', retired: 'muted' }

function FilingsCell({ row, onPrepare }: { row: ScreenRow; onPrepare: (ticker: string) => void }) {
  const { status, message } = row.filings
  if (status === 'ready') return <span className="badge bull">Ready: click row</span>
  if (status === 'queued' || status === 'running') return <span className="badge warn">Preparing… (several minutes)</span>
  return (
    <>
      <button className="small" onClick={(e) => { e.stopPropagation(); onPrepare(row.ticker) }}>Prepare filings</button>
      {status === 'failed' && <span className="down" title={message}> failed, retry</span>}
    </>
  )
}

function ShortlistTable({ screen, onSelect, onPrepare }: {
  screen: Screen
  onSelect: (ticker: string) => void
  onPrepare: (ticker: string) => void
}) {
  if (!screen.as_of) return <p className="hint">No screen has run yet.</p>
  return (
    <>
      <p className="hint">
        Screened {screen.universe_size} S&amp;P 500 companies as of {screen.as_of}. Score is the average percentile
        of 6-month return vs the S&amp;P 500, earnings yield and earnings surprise. Click a ready row to research it.
      </p>
      <div className="table-wrap">
        <table className="companies">
          <thead>
            <tr>
              <th>Ticker</th><th>Company</th><th>Sector</th><th className="n">Score</th>
              <th className="n">6-month vs S&amp;P</th><th className="n">Earnings yield</th>
              <th className="n">EPS surprise</th><th>Why</th><th>Filings</th>
            </tr>
          </thead>
          <tbody>
            {screen.rows.map((r) => (
              <tr key={r.ticker} className={r.filings.status === 'ready' ? '' : 'static'}
                onClick={() => r.filings.status === 'ready' && onSelect(r.ticker)}
                title={r.filings.status === 'ready' ? 'Click to research' : 'Prepare its filings before researching'}>
                <td><strong>{r.ticker}</strong></td>
                <td>{r.name ?? '—'}</td>
                <td>{r.sector ?? '—'}</td>
                <td className="n">{num(r.score)}</td>
                <td className={`n ${sign(r.signals.excess_return_6m)}`}>{pct(r.signals.excess_return_6m)}</td>
                <td className="n">{pct(r.signals.earnings_yield)}</td>
                <td className={`n ${sign(r.signals.eps_surprise_pct)}`}>
                  {r.signals.eps_surprise_pct === null ? '—' : `${r.signals.eps_surprise_pct.toFixed(1)}%`}
                </td>
                <td className={r.shortlisted ? '' : 'review'}>{r.reason}</td>
                <td><FilingsCell row={r} onPrepare={onPrepare} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}

const pts = (v: number) => `${v >= 0 ? '+' : ''}${(v * 100).toFixed(1)} pts`

function DebateView({ d }: { d: Debate }) {
  const failed = d.checked.filter((t) => !t.passed).length
  const column = (side: 'bull' | 'bear') => (
    <div className="side">
      <h4 className={side === 'bull' ? 'up' : 'down'}>{side === 'bull' ? 'Bull case' : 'Bear case'}</h4>
      {d.survivors.filter((t) => t.side === side).map((t) => (
        <div key={t.id} className={`thesis ${side}`}>
          <p>
            <strong>{t.score.toFixed(1)}</strong>
            {t.first_score !== undefined && t.first_score !== t.score && (
              <span className="hint"> (was {t.first_score.toFixed(1)} before rebuttal)</span>
            )}
            {t.protected && <span className="hint"> · kept as best of its side</span>}
          </p>
          <p>{t.text} <span className="cite">[{t.evidence_ids.join(', ')}]</span></p>
          {t.send_backs?.map((s, i) => (
            <p key={i} className="hint">
              Critic sent this back for more evidence: "{s.query}" (+{s.added.length} passage{s.added.length === 1 ? '' : 's'}
              {s.added.length > 0 && `: ${s.added.join(', ')}`}
              {t.score_before_send_back !== undefined && `; score was ${t.score_before_send_back.toFixed(1)}`})
            </p>
          ))}
          {t.rebuttal && <p className="hint">Rebuttal ({t.rebuttal.by}): {t.rebuttal.counter_argument}</p>}
        </div>
      ))}
    </div>
  )
  return (
    <>
      <h4>
        Bull vs bear{' '}
        {d.contested ? <span className="badge warn">contested: both sides strong, adjustment halved</span>
          : d.leaning && <span className={`badge ${d.leaning}`}>leaning {d.leaning}</span>}
      </h4>
      <div className="debate">{column('bull')}{column('bear')}</div>
      <p className="hint">
        {d.pruned.length} thesis{d.pruned.length === 1 ? '' : 'es'} pruned by score; {failed} failed the evidence
        checks (unknown citation or a number not in the cited text); the critic sent{' '}
        {d.send_back_log?.length ?? 0} request{(d.send_back_log?.length ?? 0) === 1 ? '' : 's'} back to the researcher.
      </p>
    </>
  )
}

function ForecastCard({ f }: { f: Forecast }) {
  const noSkill = f.model.calibration_slope === 0
  return (
    <section className="report forecast">
      <p className="meta">Forecast #{f.prediction_id} · {f.ticker} · as of {f.as_of}</p>
      <p className="prob">
        <strong>{(f.p_final * 100).toFixed(1)}%</strong> chance of beating the S&amp;P 500 over the next {f.horizon_days} days
        {f.needs_review && <span className="down"> · outside 35–65%: needs human review</span>}
      </p>
      <table className="breakdown">
        <tbody>
          <tr><td>Price-signal model (calibrated)</td><td className="n">{(f.p_quant * 100).toFixed(1)}%</td></tr>
          <tr><td>Evidence adjustment from {f.debate ? 'the bull/bear debate' : 'research'} (capped ±15)</td>
            <td className="n">{pts(f.evidence_adjustment)}</td></tr>
          <tr><td>Share applied while the track record is short</td><td className="n">{(f.shrink_weight * 100).toFixed(0)}%</td></tr>
          {f.rule_adjustment !== 0 && (
            <tr><td>Approved correction rules ({f.rules_applied.map((r) => `${r.field}=${r.value}`).join(', ')})</td>
              <td className="n">{pts(f.rule_adjustment)}</td></tr>)}
          <tr><td><strong>Final</strong></td><td className="n"><strong>{(f.p_final * 100).toFixed(1)}%</strong></td></tr>
        </tbody>
      </table>
      {noSkill ? (
        <p className="hint">
          The price signals showed no stable skill on held-out past data (Brier{' '}
          {f.model.holdout_brier?.['model, uncalibrated']?.toFixed(3)} vs 0.250 for always 50%), so the model
          uses the observed base rate.
        </p>
      ) : f.model.auc_by_date != null && (
        <p className="hint">
          The price-signal model only ranks stocks against each other: a small edge on 2018–2026 out-of-sample
          data (ranking skill {f.model.auc_by_date.toFixed(2)}, where 0.50 is none), and its probabilities are
          no more accurate than the base rate. Stock-specific judgment comes from the research and debate.
        </p>
      )}
      {f.debate && <DebateView d={f.debate} />}
      {f.adjustments.length > 0 ? (
        <><h4>Adjustments</h4>
          <ul>{f.adjustments.map((a, i) => (
            <li key={i}><span className={a.points >= 0 ? 'up' : 'down'}>{pts(a.points)}</span> {a.reason}
              <span className="cite"> (from: {a.claims.join(' | ')})</span></li>
          ))}</ul></>
      ) : <p className="hint">No evidence adjustments.</p>}
      <p className="disclaimer">{f.disclaimer}</p>
    </section>
  )
}

function ReportView({ report }: { report: Report }) {
  return (
    <section className="report">
      <p className="meta">
        {report.ticker} · as of {report.as_of} · {report.llm} ·{' '}
        <span className={`strength ${report.evidence_strength}`}>Evidence: {report.evidence_strength}</span>
      </p>
      {report.claims.length === 0 ? (
        <p>Insufficient evidence in the indexed filings to answer this question.</p>
      ) : (
        <ul className="claims">
          {report.claims.map((c, i) => (
            <li key={i}>
              {c.text} <span className="cite">[{c.evidence_ids.join(', ')}]</span>
              {c.status !== 'supported' && <em className="flag"> {c.status.replace('_', ' ')}</em>}
            </li>
          ))}
        </ul>
      )}
      {report.gaps.length > 0 && (<><h4>Not covered by the evidence</h4><ul>{report.gaps.map((g, i) => <li key={i}>{g}</li>)}</ul></>)}
      {report.missing_risks.length > 0 && (<><h4>Risks the critic flagged</h4><ul>{report.missing_risks.map((r, i) => <li key={i}>{r}</li>)}</ul></>)}
      {report.dropped_claims.length > 0 && (
        <><h4>Removed by verification</h4>
          <ul className="dropped">{report.dropped_claims.map((c, i) => <li key={i}><s>{c.text}</s> — {c.reason}</li>)}</ul></>
      )}
      <h4>Sources</h4>
      <ul className="sources">
        {Object.entries(report.sources).map(([id, s]) => (
          <li key={id}><span className="cite">[{id}]</span> <a href={s.url} target="_blank" rel="noreferrer">{s.citation}</a></li>
        ))}
      </ul>
      <p className="disclaimer">{report.disclaimer}</p>
    </section>
  )
}

// Which known companies a question names: tickers (3+ letters any case, 2 letters only in capitals; 1-letter
// tickers are skipped) and company names without suffixes like "Inc." ("Best Buy", "Amazon", "JPMorgan").
const ALIASES: Record<string, string[]> = { GOOGL: ['google'], META: ['facebook'] }
const SUFFIX = /[\s,]+(inc\.?|corporation|corp\.?|co\.?|company|holdings|group|platforms|ltd\.?|plc|& co\.?)$/i
const escapeRe = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

function nameTerms(name: string | null): string[] {
  if (!name) return []
  let n = name.replace(/\.com\b/i, '').trim()
  while (SUFFIX.test(n)) n = n.replace(SUFFIX, '').trim()
  const words = n.split(/\s+/)
  return words.length > 1 && words[0].length >= 7 ? [n, words[0]] : [n]
}

function mentions(question: string, companies: { ticker: string; name: string | null }[]): string[] {
  const found = new Set<string>()
  for (const c of companies) {
    const t = c.ticker
    const byTicker = t.length >= 3 ? new RegExp(`\\b${escapeRe(t)}\\b`, 'i').test(question)
      : t.length === 2 && new RegExp(`\\b${escapeRe(t)}\\b`).test(question)
    const byName = [...nameTerms(c.name), ...(ALIASES[t] ?? [])]
      .some((term) => new RegExp(`\\b${escapeRe(term)}\\b`, 'i').test(question))
    if (byTicker || byName) found.add(t)
  }
  return [...found]
}

type Tab = 'picks' | 'positions' | 'shortlist' | 'research' | 'traces' | 'reflector'
const TABS: { id: Tab; label: string }[] = [
  { id: 'picks', label: "Today's picks" }, { id: 'positions', label: 'Positions' },
  { id: 'shortlist', label: 'Shortlist' }, { id: 'research', label: 'Research' }, { id: 'traces', label: 'Traces' },
  { id: 'reflector', label: 'Reflector' },
]
const tabFromHash = (): Tab => (TABS.find((t) => `#${t.id}` === window.location.hash)?.id ?? 'picks')

export default function App() {
  const [tab, setTab] = useState<Tab>(tabFromHash)
  const [companies, setCompanies] = useState<Company[]>([])
  const [screen, setScreen] = useState<Screen | null>(null)
  const [picks, setPicks] = useState<Picks | null>(null)
  const [positions, setPositions] = useState<Position[]>([])
  const [reviewing, setReviewing] = useState(false)
  const [reflection, setReflection] = useState<Reflection | null>(null)
  const [selected, setSelected] = useState('')
  const [question, setQuestion] = useState('')
  const [asOf, setAsOf] = useState('')
  const [report, setReport] = useState<Report | null>(null)
  const [forecast, setForecast] = useState<Forecast | null>(null)
  const [forecasting, setForecasting] = useState(false)
  const [loading, setLoading] = useState(false)
  const [mismatch, setMismatch] = useState<string[]>([])
  const [error, setError] = useState('')

  useEffect(() => {
    getCompanies()
      .then((rows) => { setCompanies(rows); setSelected(rows[0]?.ticker ?? '') })
      .catch((e: Error) => setError(e.message))
    getScreen().then(setScreen).catch((e: Error) => setError(e.message))
    getPicks().then(setPicks).catch((e: Error) => setError(e.message))
    getPositions().then(setPositions).catch((e: Error) => setError(e.message))
    getReflection().then(setReflection).catch((e: Error) => setError(e.message))
  }, [])

  const [traceFile, setTraceFile] = useState<string | null>(null)
  const openTrace = (file: string) => {
    setTraceFile(file.replace(/\\/g, '/').split('/').pop() ?? null); showTab('traces')
  }

  const showTab = (t: Tab) => {
    setTab(t)
    window.history.replaceState(null, '', `#${t}`)   // a refresh stays on the same tab
  }

  const decide = async (id: number, status: string) => {
    const verb = status === 'active' ? 'Approve' : status === 'rejected' ? 'Reject' : 'Retire'
    if (!window.confirm(`${verb} this rule? Approved rules change future forecasts.`)) return
    try {
      await decideRule(id, status)
      setReflection(await getReflection())
    } catch (err) {
      setError((err as Error).message)
    }
  }

  const review = async () => {
    setReviewing(true); setError('')
    try {
      await reviewPositions()
      setPositions(await getPositions())
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setReviewing(false)
    }
  }

  const close = async (p: Position) => {
    if (!window.confirm(`Close the paper position in ${p.ticker} at the latest price?`)) return
    try {
      await closePosition(p.position_id)
      setPositions(await getPositions())
    } catch (err) {
      setError((err as Error).message)
    }
  }

  // While a daily run is in progress, refresh its status and picks every 15 seconds.
  const picksRunning = picks?.run?.status === 'running'
  useEffect(() => {
    if (!picksRunning) return
    const id = setInterval(() => getPicks().then(setPicks).catch(() => {}), 15_000)
    return () => clearInterval(id)
  }, [picksRunning])

  const runPicks = async () => {
    try {
      await startPicks()
      setPicks(await getPicks())
    } catch (err) {
      setError((err as Error).message)
    }
  }

  // While any company's filings are being prepared, refresh the shortlist every 10 seconds.
  const preparing = screen?.rows.some((r) => r.filings.status === 'queued' || r.filings.status === 'running')
  useEffect(() => {
    if (!preparing) return
    const id = setInterval(() => getScreen().then(setScreen).catch(() => {}), 10_000)
    return () => clearInterval(id)
  }, [preparing])

  const runForecast = async () => {
    if (!report) return
    setForecasting(true); setError('')
    try {
      setForecast(await getForecast(report.ticker, report.as_of, report.trace_file))
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setForecasting(false)
    }
  }

  // Picking a company: drop the other company's report, open the Research tab with the question box in view.
  const researchRef = useRef<HTMLDivElement>(null)
  const select = (ticker: string) => {
    if (ticker !== selected) { setReport(null); setForecast(null) }
    setSelected(ticker); setMismatch([]); showTab('research')
    setTimeout(() => researchRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 0)
  }

  const prepare = async (ticker: string) => {
    try {
      await prepareFilings(ticker)
      setScreen(await getScreen())
    } catch (err) {
      setError((err as Error).message)
    }
  }

  const ask = async (ticker: string) => {
    setLoading(true); setError(''); setReport(null); setForecast(null); setMismatch([])
    try {
      setReport(await runResearch(ticker, question, asOf))
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setLoading(false)
    }
  }

  const known = [...companies.map((c) => ({ ticker: c.ticker, name: c.name })),
    ...(screen?.rows ?? []).filter((r) => !companies.some((c) => c.ticker === r.ticker))
      .map((r) => ({ ticker: r.ticker, name: r.name }))]
  const ready = [...companies.map((c) => c.ticker),
    ...(screen?.rows ?? []).filter((r) => r.filings.status === 'ready' && !companies.some((c) => c.ticker === r.ticker))
      .map((r) => r.ticker)]

  const submit = (e: React.FormEvent) => {
    e.preventDefault()
    const named = mentions(question, known)
    if (named.length && !named.includes(selected)) { setMismatch(named); return }   // the question is about another company
    ask(selected)
  }

  const switchAndAsk = (ticker: string) => {
    setSelected(ticker)
    ask(ticker)
  }

  const openCount = positions.filter((p) => p.status === 'open').length
  const flaggedCount = positions.filter((p) => p.status === 'open' && p.flags.length > 0).length

  return (
    <>
      <header className="topbar">
        <div className="topbar-inner">
          <div>
            <h1>Stock Research Agent</h1>
            <p className="sub">Decision support only, not financial advice. Paper trading only.</p>
          </div>
          <nav className="tabs">
            {TABS.map((t) => (
              <button key={t.id} className={`tab ${tab === t.id ? 'active' : ''}`} onClick={() => showTab(t.id)}>
                {t.label}
                {t.id === 'positions' && openCount > 0 && <span className="count">{openCount}</span>}
                {t.id === 'positions' && flaggedCount > 0 && <span className="count alert">{flaggedCount} flagged</span>}
              </button>
            ))}
          </nav>
        </div>
      </header>

      <main>
        {error && <p className="error">{error}</p>}

        {tab === 'picks' && (
          <section className="card">
            <h2>Today's picks</h2>
            {picks && <PicksView data={picks} onRun={runPicks} onTrace={openTrace} />}
          </section>
        )}

        {tab === 'positions' && (<>
          <section className="card">
            <h2>Paper positions</h2>
            <PositionsView rows={positions} onReview={review} onClose={close} reviewing={reviewing} />
          </section>
          <section className="card">
            <AddHoldingForm onAdded={() => getPositions().then(setPositions).catch((e: Error) => setError(e.message))} />
          </section>
        </>)}

        {tab === 'shortlist' && (
          <section className="card">
            <h2>Today's shortlist</h2>
            {screen && <ShortlistTable screen={screen} onSelect={select} onPrepare={prepare} />}
          </section>
        )}

        {tab === 'research' && (<>
          <section className="card" ref={researchRef}>
            <h2>Research</h2>
            <form onSubmit={submit} className="ask">
              <div className="row">
                <label>Company{' '}
                  <select value={selected} onChange={(e) => select(e.target.value)}>
                    {ready.map((t) => <option key={t} value={t}>{t} · {known.find((k) => k.ticker === t)?.name ?? ''}</option>)}
                  </select>
                </label>
                <span className="hint">Answers use only this company's filings. Other shortlist stocks appear here once
                  their filings are prepared.</span>
              </div>
              <textarea value={question} onChange={(e) => { setQuestion(e.target.value); setMismatch([]) }} rows={3}
                required minLength={5}
                placeholder={`e.g. What drove ${selected || 'the company'}'s revenue in its latest quarter, and what risks did it highlight?`} />
              <div className="row">
                <label>As of <input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} /></label>
                <button type="submit" disabled={loading || !selected}>{loading ? 'Researching…' : `Research ${selected}`}</button>
                {loading && <span className="hint">The agents search filings, draft and verify claims; this takes about a minute.</span>}
              </div>
            </form>
            {mismatch.length > 0 && (
              <div className="notice">
                Your question mentions <strong>{mismatch.join(', ')}</strong>, but you are researching{' '}
                <strong>{selected}</strong>, so only {selected}'s filings would be searched.
                <div className="row">
                  {mismatch.map((t) => ready.includes(t)
                    ? <button key={t} onClick={() => switchAndAsk(t)}>Switch to {t} and ask</button>
                    : <span key={t} className="hint">{t}'s filings aren't prepared yet: use Prepare filings on the
                      Shortlist tab.</span>)}
                  <button className="secondary" onClick={() => ask(selected)}>Ask about {selected} anyway</button>
                </div>
              </div>
            )}
            {report && <ReportView report={report} />}
            {report && <p><button className="small secondary" onClick={() => openTrace(report.trace_file)}>
              How this answer was built (trace)</button></p>}
            {report && !forecast && (
              <p><button onClick={runForecast} disabled={forecasting}>
                {forecasting ? 'Bull and bear are debating…' : 'Debate and forecast'}
              </button></p>
            )}
            {forecast && <ForecastCard f={forecast} />}
          </section>
          <section className="card">
            <h2>Core companies <span className="hint">(always indexed; click one to research it)</span></h2>
            <div className="table-wrap">
              <CompanyTable companies={companies} selected={selected} onSelect={select} />
            </div>
          </section>
        </>)}

        {tab === 'traces' && <TracesView open={traceFile} />}

        {tab === 'reflector' && (
          <section className="card">
            <h2>Reflector: track record and corrections</h2>
            {reflection && <ReflectorView r={reflection} onDecide={decide} />}
          </section>
        )}
      </main>
    </>
  )
}
