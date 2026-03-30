import { useEffect, useState, useCallback } from 'react'

interface BotState {
  balance: number; initial: number; total_pnl: number
  total_bets: number; total_won: number; bets_today: number
  loss_today: number; history: Bet[]
}
interface Bet {
  timestamp: string; asset: string; side: string; price: number
  edge: number; bet_size: number; status: 'PENDING' | 'WON' | 'LOST'
  pnl: number | null; end_date: string | null; dry_run: boolean
  question: string; market_id?: string
  fill_price?: number | null; slippage?: number | null
}
interface Market {
  asset: string; question: string; price_up: number; price_down: number
  volume: number; end_date: string | null
}
interface Account {
  wallet: string; balance: number
  trades: any[]; orders: any[]; positions: any[]
}
interface BotStatus {
  running: boolean; pid: number | null
  live: { running: boolean; pid: number | null }
  sim:  { running: boolean; pid: number | null }
  poly: { running: boolean; pid: number | null }
}

const fmt  = (n: number) => `$${Math.abs(n).toFixed(2)}`
const sign = (n: number) => n >= 0 ? '+' : '-'

function timeLeft(end_date: string | null): string {
  if (!end_date) return '—'
  const diff = new Date(end_date).getTime() - Date.now()
  if (diff <= 0) return 'cerrado'
  const m = Math.floor(diff / 60000), s = Math.floor((diff % 60000) / 1000)
  return `${m}m ${s}s`
}
function formatTs(ts: string): string {
  try { return new Date(ts).toLocaleString('es-MX', { month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit' }) }
  catch { return ts.slice(0, 16) }
}

// ── Markets Table ─────────────────────────────────────────────────────────────
function MarketsTable({ markets }: { markets: Market[] }) {
  const latest = Object.values(
    markets.reduce((acc, m) => { if (!acc[m.asset]) acc[m.asset] = m; return acc }, {} as Record<string, Market>)
  )
  return (
    <div className="section">
      <div className="section-title">Mercados 5min activos</div>
      <div className="table-wrap">
        <table>
          <thead><tr><th>Asset</th><th>UP</th><th>DOWN</th><th>Volumen</th><th>Cierra en</th><th>Mercado</th></tr></thead>
          <tbody>
            {latest.length === 0
              ? <tr><td colSpan={6} className="loading">Sin mercados activos ahora mismo</td></tr>
              : latest.map(m => (
                <tr key={m.asset}>
                  <td><strong>{m.asset}</strong></td>
                  <td><span className={`pill ${m.price_up <= 0.55 ? 'pill-green' : 'pill-red'}`}>{m.price_up.toFixed(2)}</span></td>
                  <td><span className={`pill ${m.price_down <= 0.55 ? 'pill-green' : 'pill-red'}`}>{m.price_down.toFixed(2)}</span></td>
                  <td>${m.volume.toLocaleString()}</td>
                  <td style={{ color: '#facc15' }}>{timeLeft(m.end_date)}</td>
                  <td style={{ color: '#64748b', fontSize: 11 }}>{m.question.slice(0, 48)}...</td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── Helpers ───────────────────────────────────────────────────────────────────
const ASSET_COLOR: Record<string, string> = {
  BTC: '#f7931a', ETH: '#627eea', SOL: '#9945ff', XRP: '#00aae4',
  DOGE: '#c2a633', HYPE: '#e040fb',
}
// Convert ET time range in question string to browser local time
// e.g. "BTC Up or Down - March 29, 8:55PM-9:00PM ET" → "6:55PM-7:00PM MDT"
function etRangeToLocal(question: string): string {
  const m = question.match(/(\w+\s+\d+),\s*(\d+:\d+(?:AM|PM))-(\d+:\d+(?:AM|PM))\s*ET/i)
  if (!m) return ''
  const [, datePart, startET, endET] = m
  const toLocal = (t: string) => {
    try {
      const d = new Date(`${datePart}, 2026 ${t} GMT-0400`) // EDT = UTC-4
      return d.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', timeZoneName: 'short' })
    } catch { return '' }
  }
  const s = toLocal(startET), e = toLocal(endET)
  return s && e ? `${s} – ${e}` : ''
}

function timeAgo(ts: string): string {
  const diff = Date.now() - new Date(ts).getTime()
  const m = Math.floor(diff / 60000)
  const h = Math.floor(m / 60)
  const d = Math.floor(h / 24)
  if (d > 0) return `${d}d`
  if (h > 0) return `${h}h ${m % 60}m`
  return `${m}m`
}

// ── Tab Real ──────────────────────────────────────────────────────────────────
function posStatus(p: any): 'WON' | 'LOST' | 'OPEN' {
  const cp = typeof p.curPrice === 'number' ? p.curPrice : parseFloat(p.curPrice ?? '-1')
  if (cp >= 0.99) return 'WON'
  if (cp <= 0.01 && p.redeemable) return 'LOST'
  return 'OPEN'
}

function TabReal({ account, markets, state, polyState }: { account: Account | null; markets: Market[]; state: BotState | null; polyState: BotState | null }) {
  const [actPage, setActPage] = useState(0)
  const PAGE_SIZE = 10

  const bal = account?.balance ?? 0
  const positions = account?.positions ?? []
  const open = positions.filter(p => posStatus(p) === 'OPEN').length

  // Win rate and PnL from LIVE poly5m bets (dry_run=false in poly5m_state.json)
  const realHistory  = (polyState?.history ?? []).filter(b => !b.dry_run)
  const realResolved = realHistory.filter(b => b.status === 'WON' || b.status === 'LOST')
  const totalBets = realResolved.length
  const totalWon  = realResolved.filter(b => b.status === 'WON').length
  const winRate   = totalBets > 0 ? Math.round((totalWon / totalBets) * 100) : null
  const totalPnl  = realHistory.reduce((s, b) => s + (b.pnl ?? 0), 0)

  return (
    <>
      <div className="cards">
        <div className="card">
          <div className="label">Balance Polymarket</div>
          <div className="value yellow">{fmt(bal)}</div>
          <div className="sub" style={{ fontSize: 10, wordBreak: 'break-all' }}>{account?.wallet?.slice(0, 22)}...</div>
        </div>
        <div className="card">
          <div className="label">Win Rate (histórico)</div>
          <div className={`value ${winRate === null ? '' : winRate >= 50 ? 'green' : 'red'}`}>
            {winRate !== null ? `${winRate}%` : '—'}
          </div>
          <div className="sub">{totalWon} ganadas / {totalBets} totales</div>
        </div>
        <div className="card">
          <div className="label">PnL total bot</div>
          <div className={`value ${totalPnl >= 0 ? 'green' : 'red'}`}>
            {totalPnl >= 0 ? '+' : ''}{fmt(totalPnl)}
          </div>
          <div className="sub">vs balance inicial</div>
        </div>
        <div className="card">
          <div className="label">Posiciones activas</div>
          <div className="value">{open}</div>
          <div className="sub">mercados aún abiertos</div>
        </div>
      </div>

      {bal === 0 && (
        <div style={{ background: '#422006', border: '1px solid #f97316', borderRadius: 12, padding: 16, marginBottom: 16, color: '#fed7aa' }}>
          ⚠ <strong>Balance en $0</strong> — Deposita USDC en polymarket.com → Deposit para operar.
        </div>
      )}

      {/* ── Claim banner ── */}
      {(() => {
        const claimable = (account?.positions ?? []).filter((p: any) => p.redeemable && (p.currentValue ?? 0) > 0.01)
        const totalClaim = claimable.reduce((s: number, p: any) => s + (p.currentValue ?? 0), 0)
        if (claimable.length === 0) return null
        return (
          <div style={{ background: '#14532d', border: '1px solid #4ade80', borderRadius: 12, padding: '14px 18px', marginBottom: 16, display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
            <div>
              <div style={{ color: '#4ade80', fontWeight: 700, fontSize: 16 }}>
                🎉 Tienes <strong>${totalClaim.toFixed(2)}</strong> para reclamar en {claimable.length} posición{claimable.length > 1 ? 'es' : ''}
              </div>
              <div style={{ color: '#86efac', fontSize: 12, marginTop: 3 }}>
                {claimable.map((p: any) => `${(p.title || '').slice(0, 30)}… +$${(p.currentValue ?? 0).toFixed(2)}`).join(' · ')}
              </div>
            </div>
            <a href="https://polymarket.com/portfolio" target="_blank" rel="noreferrer"
              style={{ background: '#4ade80', color: '#052e16', fontWeight: 700, fontSize: 13, padding: '8px 18px', borderRadius: 8, textDecoration: 'none', whiteSpace: 'nowrap', flexShrink: 0 }}>
              Reclamar →
            </a>
          </div>
        )
      })()}

      {/* ── Activity feed ── */}
      {(() => {
        // Build flat items array newest-first
        type AItem = { key: string; node: React.ReactNode; ts: number }
        const items: AItem[] = []

        // 1 fila por bet: ✓ WON | ✗ LOST | ⏳ PENDING — sin duplicar compra+pago
        const pendingMarkets = new Set(
          (polyState?.history ?? []).filter(b => !b.dry_run && b.status === 'PENDING').map(b => b.market_id)
        )

        ;[...(polyState?.history ?? [])].filter(b => !b.dry_run).reverse().forEach((b, i) => {
          const color  = ASSET_COLOR[b.asset] ?? '#64748b'
          const isUp   = b.side === 'UP'
          const cents  = Math.round((b.price ?? 0) * 100)
          const shares = b.price > 0 ? (b.bet_size / b.price).toFixed(1) : '—'
          const payout = b.pnl != null ? b.pnl + b.bet_size : b.bet_size / (b.price ?? 1)
          const shortQ   = (b.question || `${b.asset} Up or Down`).slice(0, 60)
          const localTime = etRangeToLocal(b.question || '')
          const ts     = new Date(b.timestamp).getTime()
          const badge  = <span className={`act-badge ${isUp ? 'up' : 'down'}`}>{isUp ? '▲' : '▼'} {b.side} {cents}¢</span>

          if (b.status === 'WON') {
            items.push({ key: `win-${i}`, ts, node: (
              <div className="activity-row">
                <div className="act-icon claimed">✓</div>
                <div className="asset-icon" style={{ background: color }}>{b.asset[0]}</div>
                <div className="act-info">
                  <div className="act-name">{shortQ}</div>
                  {localTime && <div style={{ fontSize: 10, color: '#64748b', marginTop: 1 }}>{localTime}</div>}
                  <div className="act-meta">{badge}<span className="act-shares">{shares} acciones</span></div>
                </div>
                <div className="act-right">
                  <div className="act-value pos">+{fmt(payout)}</div>
                  <div className="act-time">{timeAgo(b.timestamp)}</div>
                </div>
              </div>
            )})
          } else if (b.status === 'LOST') {
            items.push({ key: `lost-${i}`, ts, node: (
              <div className="activity-row">
                <div className="act-icon lost">✗</div>
                <div className="asset-icon" style={{ background: color }}>{b.asset[0]}</div>
                <div className="act-info">
                  <div className="act-name">{shortQ}</div>
                  {localTime && <div style={{ fontSize: 10, color: '#64748b', marginTop: 1 }}>{localTime}</div>}
                  <div className="act-meta">{badge}<span className="act-shares">{shares} acciones</span></div>
                </div>
                <div className="act-right">
                  <div className="act-value neg">-{fmt(b.bet_size ?? 0)}</div>
                  <div className="act-time">{timeAgo(b.timestamp)}</div>
                </div>
              </div>
            )})
          } else {
            // PENDING
            items.push({ key: `pend-${i}`, ts, node: (
              <div className="activity-row">
                <div className="act-icon pending">⏳</div>
                <div className="asset-icon" style={{ background: color }}>{b.asset[0]}</div>
                <div className="act-info">
                  <div className="act-name">{shortQ}</div>
                  {localTime && <div style={{ fontSize: 10, color: '#64748b', marginTop: 1 }}>{localTime}</div>}
                  <div className="act-meta">{badge}<span className="act-shares">{shares} acciones</span></div>
                </div>
                <div className="act-right">
                  <div className="act-value neu">En curso</div>
                  <div className="act-time">{timeAgo(b.timestamp)}</div>
                </div>
              </div>
            )})
          }
        })

        // Add CLOB trades not already covered by state.history (e.g. manual test bets)
        // Only show entries with a recognizable title (Bitcoin/Ethereum/Solana/XRP) to avoid
        // confusing CLOB fill artifacts with inflated sizes from maker orders.
        const stateMarkets = new Set((polyState?.history ?? []).filter(b => !b.dry_run).map(b => b.market_id))
        const seenClob     = new Set<string>()
        const cutoff24h    = Date.now() - 24 * 60 * 60 * 1000
        ;(account?.trades ?? []).forEach((t: any) => {
          if (stateMarkets.has(t.market)) return   // already in state
          if (seenClob.has(t.market))    return   // deduplicate fills
          // solo mostrar trades de las últimas 24 horas
          const rawTs = t.match_time || ''
          const tsMs  = /^\d+$/.test(rawTs) ? parseInt(rawTs) * 1000 : 0
          if (tsMs < cutoff24h) return
          seenClob.add(t.market)
          // detect asset from title — skip if not a recognized market
          const matchPos = (account?.positions ?? []).find((p: any) => p.conditionId === t.market)
          const titleM   = (matchPos?.title || '').match(/Bitcoin|Ethereum|Solana|XRP/i)?.[0] || ''
          if (!titleM) return  // skip unknown/hash-only markets
          const outcome  = t.my_outcome || t.outcome || '?'
          const isUp     = outcome === 'Up'
          const side     = isUp ? 'UP' : 'DOWN'
          const price    = parseFloat(t.price || '0')
          const tsIso    = new Date(tsMs).toISOString()
          const cents    = Math.round(price * 100)
          // determine status from positions cross-reference
          const st       = matchPos ? posStatus(matchPos) : null
          const icon     = st === 'WON' ? 'claimed' : st === 'LOST' ? 'lost' : 'bought'
          const symbol   = st === 'WON' ? '✓' : st === 'LOST' ? '✗' : '⊕'
          const assetKey = titleM.startsWith('Bit') ? 'BTC' : titleM.startsWith('Eth') ? 'ETH' : titleM.startsWith('Sol') ? 'SOL' : 'XRP'
          const color    = ASSET_COLOR[assetKey] ?? '#64748b'
          const label    = matchPos?.title?.slice(0, 60) || t.market?.slice(0, 20)
          items.push({ key: `clob-${t.market}`, ts: tsMs, node: (
            <div className="activity-row">
              <div className={`act-icon ${icon}`}>{symbol}</div>
              <div className="asset-icon" style={{ background: color }}>{assetKey[0]}</div>
              <div className="act-info">
                <div className="act-name">{label}</div>
                <div className="act-meta">
                  <span className={`act-badge ${isUp ? 'up' : 'down'}`}>{isUp ? '▲' : '▼'} {side} {cents}¢</span>
                </div>
              </div>
              <div className="act-right">
                <div className={`act-value ${st === 'WON' ? 'pos' : 'neg'}`}>
                  {st === 'WON' ? 'Ganado' : '—'}
                </div>
                <div className="act-time">{timeAgo(tsIso)}</div>
              </div>
            </div>
          )})
        })

        // Sort newest first
        items.sort((a, b) => b.ts - a.ts)

        const totalPages = Math.max(1, Math.ceil(items.length / PAGE_SIZE))
        const safePage   = Math.min(actPage, totalPages - 1)
        const pageItems  = items.slice(safePage * PAGE_SIZE, (safePage + 1) * PAGE_SIZE)

        return (
          <div className="section">
            <div className="section-title" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span>Actividad</span>
              <span style={{ fontSize: 11, color: '#475569', fontWeight: 400, textTransform: 'none', letterSpacing: 0 }}>
                {items.length} eventos · pág {safePage + 1}/{totalPages}
              </span>
            </div>
            <div className="activity-feed">
              {items.length === 0
                ? <div className="loading">Sin actividad aún</div>
                : pageItems.map((item, idx) => <div key={item.key + idx}>{item.node}</div>)
              }
            </div>
            {totalPages > 1 && (
              <div style={{ display: 'flex', justifyContent: 'center', gap: 8, marginTop: 10 }}>
                <button className="refresh-btn" onClick={() => setActPage(p => Math.max(0, p - 1))} disabled={safePage === 0}>← Anterior</button>
                <button className="refresh-btn" onClick={() => setActPage(p => Math.min(totalPages - 1, p + 1))} disabled={safePage === totalPages - 1}>Siguiente →</button>
              </div>
            )}
          </div>
        )
      })()}

      <MarketsTable markets={markets} />
    </>
  )
}

// ── Tab Simulación ────────────────────────────────────────────────────────────
function TabSim({ state, markets }: { state: BotState | null; markets: Market[] }) {
  const [actPage, setActPage] = useState(0)
  const PAGE_SIZE = 10

  if (!state) return <div className="loading">Sin datos del bot</div>

  // Only simulation bets (dry_run=true)
  const simHistory = state.history.filter(b => b.dry_run)
  const simResolved  = simHistory.filter(b => b.status === 'WON' || b.status === 'LOST')
  const simTotalBets = simResolved.length
  const simTotalWon  = simResolved.filter(b => b.status === 'WON').length
  const simTotalPnl  = simHistory.reduce((s, b) => s + (b.pnl ?? 0), 0)
  const winRate   = simTotalBets > 0 ? Math.round((simTotalWon / simTotalBets) * 100) : null
  const pnlColor  = simTotalPnl >= 0 ? 'green' : 'red'
  const pctVsInit = state.initial > 0 ? (((state.balance - state.initial) / state.initial) * 100).toFixed(1) : '0'
  const openBets  = simHistory.filter(b => b.status === 'PENDING').length

  // Claim-style banner: profitable WON bets not yet "reclaimed"
  const wonBets     = simHistory.filter(b => b.status === 'WON')
  const totalWonPnl = wonBets.reduce((s, b) => s + (b.pnl ?? 0), 0)

  // Build activity items (same shape as TabReal)
  type AItem = { key: string; node: React.ReactNode; ts: number }
  const items: AItem[] = []

  ;[...simHistory].reverse().forEach((b, i) => {
    const color   = ASSET_COLOR[b.asset] ?? '#64748b'
    const isUp    = b.side === 'UP'
    const cents   = Math.round((b.price ?? 0) * 100)
    const shares  = b.price > 0 ? (b.bet_size / b.price).toFixed(1) : '—'
    const payout  = b.pnl != null ? b.pnl + b.bet_size : b.bet_size / (b.price ?? 1)
    const shortQ  = (b.question || `${b.asset} Up or Down in 5min`).slice(0, 60)
    const shortId = b.market_id ? b.market_id.slice(0, 12) + '…' : `SIM-${b.asset}-${i}`
    const ts      = new Date(b.timestamp).getTime()

    const icon   = b.status === 'WON' ? 'claimed' : b.status === 'LOST' ? 'lost' : 'pending'
    const symbol = b.status === 'WON' ? '✓' : b.status === 'LOST' ? '✗' : '⏳'
    const valueNode = b.status === 'WON'
      ? <div className="act-value pos">+{fmt(payout)}</div>
      : b.status === 'PENDING'
        ? <div className="act-value neu">En curso</div>
        : <div className="act-value neg">-{fmt(b.bet_size ?? 0)}</div>
    const metaExtra = b.status === 'PENDING'
      ? <span className="act-shares">{shares} acciones · {timeLeft(b.end_date)}</span>
      : <span className="act-shares">{shares} acciones · edge {b.edge ? `${(b.edge * 100).toFixed(1)}%` : '—'}</span>

    items.push({ key: `sim-${i}`, ts, node: (
      <div className="activity-row">
        <div className={`act-icon ${icon}`}>{symbol}</div>
        <div className="asset-icon" style={{ background: color }}>{b.asset[0]}</div>
        <div className="act-info">
          <div className="act-name">{shortQ}</div>
          <div className="act-meta">
            <span className={`act-badge ${isUp ? 'up' : 'down'}`}>{isUp ? '▲' : '▼'} {b.side} {cents}¢</span>
            {metaExtra}
          </div>
        </div>
        <div className="act-right">
          {valueNode}
          <div className="act-time">{timeAgo(b.timestamp)}</div>
        </div>
      </div>
    )})
  })

  items.sort((a, b) => b.ts - a.ts)
  const totalPages = Math.max(1, Math.ceil(items.length / PAGE_SIZE))
  const safePage   = Math.min(actPage, totalPages - 1)
  const pageItems  = items.slice(safePage * PAGE_SIZE, (safePage + 1) * PAGE_SIZE)

  return (
    <>
      {/* ── Cards ── */}
      <div className="cards">
        <div className="card">
          <div className="label">Balance Simulado</div>
          <div className="value yellow">{fmt(state.balance)}</div>
          <div className="sub">Inicial: {fmt(state.initial)} · {pctVsInit}%</div>
        </div>
        <div className="card">
          <div className="label">Win Rate (simulado)</div>
          <div className={`value ${winRate === null ? '' : winRate >= 50 ? 'green' : 'red'}`}>
            {winRate !== null ? `${winRate}%` : '—'}
          </div>
          <div className="sub">{simTotalWon} ganadas / {simTotalBets} totales</div>
        </div>
        <div className="card">
          <div className="label">PnL total bot</div>
          <div className={`value ${pnlColor}`}>
            {simTotalPnl >= 0 ? '+' : ''}{fmt(simTotalPnl)}
          </div>
          <div className="sub">vs balance inicial</div>
        </div>
        <div className="card">
          <div className="label">Posiciones abiertas</div>
          <div className="value">{openBets}</div>
          <div className="sub">Hoy: {state.bets_today}/20 · stop ${state.loss_today.toFixed(2)}</div>
        </div>
      </div>

      {/* ── "Reclaim" banner para ganadas ── */}
      {wonBets.length > 0 && (
        <div style={{ background: '#14532d', border: '1px solid #4ade80', borderRadius: 12, padding: '14px 18px', marginBottom: 16, display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
          <div>
            <div style={{ color: '#4ade80', fontWeight: 700, fontSize: 16 }}>
              Ganaste <strong>{fmt(totalWonPnl)}</strong> en {wonBets.length} apuesta{wonBets.length > 1 ? 's' : ''} simulada{wonBets.length > 1 ? 's' : ''}
            </div>
            <div style={{ color: '#86efac', fontSize: 12, marginTop: 3 }}>
              {wonBets.slice(0, 3).map(b => `${b.asset} ${b.side} +${fmt(b.pnl ?? 0)}`).join(' · ')}{wonBets.length > 3 ? ` · +${wonBets.length - 3} más` : ''}
            </div>
          </div>
          <div style={{ background: '#4ade80', color: '#052e16', fontWeight: 700, fontSize: 13, padding: '8px 18px', borderRadius: 8, whiteSpace: 'nowrap', flexShrink: 0 }}>
            SIM — no real
          </div>
        </div>
      )}

      {/* ── Activity feed ── */}
      <div className="section">
        <div className="section-title" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span>Actividad (Simulación)</span>
          <span style={{ fontSize: 11, color: '#475569', fontWeight: 400, textTransform: 'none', letterSpacing: 0 }}>
            {items.length} eventos · pág {safePage + 1}/{totalPages}
          </span>
        </div>
        <div className="activity-feed">
          {items.length === 0
            ? <div className="loading">Sin apuestas aún — esperando señal con edge &gt;= 8%</div>
            : pageItems.map((item, idx) => <div key={item.key + idx}>{item.node}</div>)
          }
        </div>
        {totalPages > 1 && (
          <div style={{ display: 'flex', justifyContent: 'center', gap: 8, marginTop: 10 }}>
            <button className="refresh-btn" onClick={() => setActPage(p => Math.max(0, p - 1))} disabled={safePage === 0}>← Anterior</button>
            <button className="refresh-btn" onClick={() => setActPage(p => Math.min(totalPages - 1, p + 1))} disabled={safePage === totalPages - 1}>Siguiente →</button>
          </div>
        )}
      </div>

      <MarketsTable markets={markets} />
    </>
  )
}

// ── Tab PM Signal (CLOB-only bot) ─────────────────────────────────────────────
function TabPoly({ state, markets }: { state: BotState | null; markets: Market[] }) {
  const [actPage, setActPage] = useState(0)
  const PAGE_SIZE = 10

  if (!state) return <div className="loading">Sin datos del bot PM Signal</div>

  const history     = state.history  // poly5m_state.json tiene su propio archivo, no mezcla
  const resolved    = history.filter(b => b.status === 'WON' || b.status === 'LOST')
  const totalBets   = resolved.length
  const totalWon    = resolved.filter(b => b.status === 'WON').length
  const totalPnl    = history.reduce((s, b) => s + (b.pnl ?? 0), 0)
  const winRate     = totalBets > 0 ? Math.round((totalWon / totalBets) * 100) : null
  const pnlColor    = totalPnl >= 0 ? 'green' : 'red'
  const pctVsInit   = state.initial > 0 ? (((state.balance - state.initial) / state.initial) * 100).toFixed(1) : '0'
  const openBets    = history.filter(b => b.status === 'PENDING').length
  const wonBets     = history.filter(b => b.status === 'WON')
  const totalWonPnl = wonBets.reduce((s, b) => s + (b.pnl ?? 0), 0)

  type AItem = { key: string; node: React.ReactNode; ts: number }
  const items: AItem[] = []

  ;[...history].reverse().forEach((b, i) => {
    const color   = ASSET_COLOR[b.asset] ?? '#64748b'
    const isUp    = b.side === 'UP'
    const cents   = Math.round((b.price ?? 0) * 100)
    const shares  = b.price > 0 ? (b.bet_size / b.price).toFixed(1) : '—'
    const payout  = b.pnl != null ? b.pnl + b.bet_size : b.bet_size / (b.price ?? 1)
    const shortQ  = (b.question || `${b.asset} Up or Down 5min`).slice(0, 60)
    const ts      = new Date(b.timestamp).getTime()

    const icon   = b.status === 'WON' ? 'claimed' : b.status === 'LOST' ? 'lost' : 'pending'
    const symbol = b.status === 'WON' ? '✓' : b.status === 'LOST' ? '✗' : '⏳'
    const valueNode = b.status === 'WON'
      ? <div className="act-value pos">+{fmt(payout)}</div>
      : b.status === 'PENDING'
        ? <div className="act-value neu">En curso</div>
        : <div className="act-value neg">-{fmt(b.bet_size ?? 0)}</div>
    const metaExtra = b.status === 'PENDING'
      ? <span className="act-shares">{shares} acciones · {timeLeft((b as any).end_date)}</span>
      : <span className="act-shares">{shares} acciones · edge {b.edge ? `${(b.edge * 100).toFixed(1)}%` : '—'}</span>

    items.push({ key: `poly-${i}`, ts, node: (
      <div className="activity-row">
        <div className={`act-icon ${icon}`}>{symbol}</div>
        <div className="asset-icon" style={{ background: color }}>{b.asset[0]}</div>
        <div className="act-info">
          <div className="act-name">{shortQ}</div>
          <div className="act-meta">
            <span className={`act-badge ${isUp ? 'up' : 'down'}`}>{isUp ? '▲' : '▼'} {b.side} {cents}¢</span>
            {metaExtra}
          </div>
        </div>
        <div className="act-right">
          {valueNode}
          <div className="act-time">{timeAgo(b.timestamp)}</div>
        </div>
      </div>
    )})
  })

  items.sort((a, b) => b.ts - a.ts)
  const totalPages = Math.max(1, Math.ceil(items.length / PAGE_SIZE))
  const safePage   = Math.min(actPage, totalPages - 1)
  const pageItems  = items.slice(safePage * PAGE_SIZE, (safePage + 1) * PAGE_SIZE)

  return (
    <>
      <div style={{ background: '#0f1729', border: '1px solid #3b5bdb', borderRadius: 10, padding: '10px 16px', marginBottom: 16, fontSize: 12, color: '#93c5fd' }}>
        <strong style={{ color: '#60a5fa' }}>PM Signal (CLOB)</strong> — Señales derivadas del orderbook de Polymarket.
        Sin Binance. Edge calculado por imbalance bids/asks + desviación de precio vs 50¢.
      </div>

      <div className="cards">
        <div className="card">
          <div className="label">Balance PM Signal</div>
          <div className="value" style={{ color: '#60a5fa' }}>{fmt(state.balance)}</div>
          <div className="sub">Inicial: {fmt(state.initial)} · {pctVsInit}%</div>
        </div>
        <div className="card">
          <div className="label">Win Rate</div>
          <div className={`value ${winRate === null ? '' : winRate >= 50 ? 'green' : 'red'}`}>
            {winRate !== null ? `${winRate}%` : '—'}
          </div>
          <div className="sub">{totalWon} ganadas / {totalBets} totales</div>
        </div>
        <div className="card">
          <div className="label">PnL total</div>
          <div className={`value ${pnlColor}`}>{totalPnl >= 0 ? '+' : ''}{fmt(totalPnl)}</div>
          <div className="sub">vs balance inicial</div>
        </div>
        <div className="card">
          <div className="label">Posiciones abiertas</div>
          <div className="value">{openBets}</div>
          <div className="sub">Hoy: {state.bets_today} · stop ${state.loss_today.toFixed(2)}</div>
        </div>
      </div>

      {wonBets.length > 0 && (
        <div style={{ background: '#0c1a3a', border: '1px solid #3b82f6', borderRadius: 12, padding: '14px 18px', marginBottom: 16, display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
          <div>
            <div style={{ color: '#60a5fa', fontWeight: 700, fontSize: 16 }}>
              Ganaste <strong>{fmt(totalWonPnl)}</strong> en {wonBets.length} apuesta{wonBets.length > 1 ? 's' : ''} PM Signal
            </div>
            <div style={{ color: '#93c5fd', fontSize: 12, marginTop: 3 }}>
              {wonBets.slice(0, 3).map(b => `${b.asset} ${b.side} +${fmt(b.pnl ?? 0)}`).join(' · ')}
            </div>
          </div>
          <div style={{ background: '#3b82f6', color: '#fff', fontWeight: 700, fontSize: 13, padding: '8px 18px', borderRadius: 8, whiteSpace: 'nowrap', flexShrink: 0 }}>
            SIM — no real
          </div>
        </div>
      )}

      <div className="section">
        <div className="section-title" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span>Actividad (PM Signal)</span>
          <span style={{ fontSize: 11, color: '#475569', fontWeight: 400, textTransform: 'none', letterSpacing: 0 }}>
            {items.length} eventos · pág {safePage + 1}/{totalPages}
          </span>
        </div>
        <div className="activity-feed">
          {items.length === 0
            ? <div className="loading">Sin apuestas aun — bot CLOB esperando edge &gt;= 6%</div>
            : pageItems.map((item, idx) => <div key={item.key + idx}>{item.node}</div>)
          }
        </div>
        {totalPages > 1 && (
          <div style={{ display: 'flex', justifyContent: 'center', gap: 8, marginTop: 10 }}>
            <button className="refresh-btn" onClick={() => setActPage(p => Math.max(0, p - 1))} disabled={safePage === 0}>← Anterior</button>
            <button className="refresh-btn" onClick={() => setActPage(p => Math.min(totalPages - 1, p + 1))} disabled={safePage === totalPages - 1}>Siguiente →</button>
          </div>
        )}
      </div>

      <MarketsTable markets={markets} />
    </>
  )
}

// ── Tab Spread Analysis ───────────────────────────────────────────────────────
function TabSpread({ state }: { state: BotState | null }) {
  if (!state) return <div className="loading">Sin datos PM Signal</div>

  const resolved = state.history.filter(b => b.status === 'WON' || b.status === 'LOST')

  // PnL calculado con fill_price (ask) en vez de mid_price
  const pnlFill = (b: Bet): number => {
    if (b.status === 'LOST') return -(b.bet_size ?? 2.5)
    const fp = b.fill_price
    if (!fp || fp <= 0) return b.pnl ?? 0
    return (b.bet_size ?? 2.5) / fp - (b.bet_size ?? 2.5)
  }

  const totalPnlMid  = resolved.reduce((s, b) => s + (b.pnl ?? 0), 0)
  const totalPnlFill = resolved.reduce((s, b) => s + pnlFill(b), 0)
  const spreadCost   = totalPnlMid - totalPnlFill

  const slippages = state.history
    .filter(b => b.slippage != null)
    .map(b => b.slippage as number)
  const avgSlip = slippages.length > 0
    ? slippages.reduce((a, b) => a + b, 0) / slippages.length
    : null

  // Per-asset summary
  const assets = Array.from(new Set(resolved.map(b => b.asset)))
  const assetStats = assets.map(asset => {
    const bets  = resolved.filter(b => b.asset === asset)
    const won   = bets.filter(b => b.status === 'WON').length
    const pMid  = bets.reduce((s, b) => s + (b.pnl ?? 0), 0)
    const pFill = bets.reduce((s, b) => s + pnlFill(b), 0)
    const slips = bets.filter(b => b.slippage != null).map(b => b.slippage as number)
    const avgS  = slips.length > 0 ? slips.reduce((a, b) => a + b, 0) / slips.length : null
    return { asset, total: bets.length, won, pMid, pFill, avgSlip: avgS }
  }).sort((a, b) => b.total - a.total)

  const exportCSV = () => {
    const headers = ['Fecha', 'Asset', 'Lado', 'Mid Price', 'Fill Price', 'Slippage', 'Slippage%', 'Edge', 'Apuesta', 'PnL Mid', 'PnL Fill', 'Diferencia', 'Status']
    const rows = [...resolved].reverse().map(b => {
      const fp   = b.fill_price ?? null
      const sl   = b.slippage ?? null
      const pf   = pnlFill(b)
      const diff = pf - (b.pnl ?? 0)
      return [
        new Date(b.timestamp).toLocaleString('es-MX'),
        b.asset, b.side,
        b.price?.toFixed(4),
        fp != null ? fp.toFixed(4) : '',
        sl != null ? sl.toFixed(4) : '',
        sl != null ? ((sl / (b.price || 1)) * 100).toFixed(2) + '%' : '',
        b.edge ? (b.edge * 100).toFixed(2) + '%' : '',
        (b.bet_size ?? 0).toFixed(2),
        (b.pnl ?? 0).toFixed(4),
        pf.toFixed(4),
        diff.toFixed(4),
        b.status,
      ]
    })
    const csv = [headers, ...rows].map(r => r.map(v => `"${v}"`).join(',')).join('\n')
    const blob = new Blob(['\ufeff' + csv], { type: 'text/csv;charset=utf-8;' })
    const url  = URL.createObjectURL(blob)
    const a    = document.createElement('a')
    a.href = url; a.download = `poly5m_spread_${new Date().toISOString().slice(0, 10)}.csv`
    a.click(); URL.revokeObjectURL(url)
  }

  return (
    <>
      {/* ── Info banner ── */}
      <div style={{ background: '#0f1729', border: '1px solid #6366f1', borderRadius: 10, padding: '10px 16px', marginBottom: 16, fontSize: 12, color: '#a5b4fc' }}>
        <strong style={{ color: '#818cf8' }}>Análisis Mid vs Fill</strong> — Mid Price = precio usado en simulación.
        Fill Price = ask real del CLOB (lo que pagarías en LIVE). La diferencia es el costo del spread.
      </div>

      {/* ── Cards ── */}
      <div className="cards">
        <div className="card">
          <div className="label">PnL (Mid — SIM actual)</div>
          <div className={`value ${totalPnlMid >= 0 ? 'green' : 'red'}`}>
            {totalPnlMid >= 0 ? '+' : ''}{fmt(totalPnlMid)}
          </div>
          <div className="sub">{resolved.length} apuestas resueltas</div>
        </div>
        <div className="card">
          <div className="label">PnL (Fill — si fuera LIVE)</div>
          <div className={`value ${totalPnlFill >= 0 ? 'green' : 'red'}`}>
            {totalPnlFill >= 0 ? '+' : ''}{fmt(totalPnlFill)}
          </div>
          <div className="sub">Usando ask price real</div>
        </div>
        <div className="card">
          <div className="label">Costo spread acumulado</div>
          <div className="value" style={{ color: spreadCost > 0 ? '#f87171' : '#4ade80' }}>
            -{fmt(spreadCost)}
          </div>
          <div className="sub">Mid − Fill (lo que "pierde" vs LIVE)</div>
        </div>
        <div className="card">
          <div className="label">Spread promedio</div>
          <div className="value" style={{ color: avgSlip != null ? (avgSlip < 0.02 ? '#4ade80' : '#facc15') : '#94a3b8' }}>
            {avgSlip != null ? `+${(avgSlip * 100).toFixed(3)}%` : '—'}
          </div>
          <div className="sub">{slippages.length} apuestas con fill_price</div>
        </div>
      </div>

      {/* ── Per-asset summary ── */}
      <div className="section">
        <div className="section-title">Resumen por asset</div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Asset</th><th>Apuestas</th><th>WR%</th><th>PnL Mid</th><th>PnL Fill</th><th>Spread avg</th><th>Diferencia</th></tr>
            </thead>
            <tbody>
              {assetStats.map(s => {
                const wr   = s.total > 0 ? Math.round((s.won / s.total) * 100) : 0
                const diff = s.pFill - s.pMid
                return (
                  <tr key={s.asset}>
                    <td><strong style={{ color: ASSET_COLOR[s.asset] ?? '#fff' }}>{s.asset}</strong></td>
                    <td>{s.total}</td>
                    <td style={{ color: wr >= 56 ? '#4ade80' : wr >= 50 ? '#facc15' : '#f87171' }}>{wr}%</td>
                    <td style={{ color: s.pMid  >= 0 ? '#4ade80' : '#f87171' }}>{s.pMid  >= 0 ? '+' : ''}{s.pMid.toFixed(2)}</td>
                    <td style={{ color: s.pFill >= 0 ? '#4ade80' : '#f87171' }}>{s.pFill >= 0 ? '+' : ''}{s.pFill.toFixed(2)}</td>
                    <td style={{ color: '#94a3b8' }}>{s.avgSlip != null ? `+${(s.avgSlip * 100).toFixed(3)}%` : '—'}</td>
                    <td style={{ color: diff >= 0 ? '#4ade80' : '#f87171' }}>{diff >= 0 ? '+' : ''}{diff.toFixed(2)}</td>
                  </tr>
                )
              })}
              {assetStats.length === 0 && <tr><td colSpan={7} className="loading">Sin datos aún</td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      {/* ── Bet-by-bet table ── */}
      <div className="section">
        <div className="section-title" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span>Detalle apuesta por apuesta ({resolved.length} resueltas)</span>
          <button className="refresh-btn" onClick={exportCSV} style={{ background: '#4338ca', color: '#fff', border: 'none' }}>
            ⬇ Exportar CSV
          </button>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Fecha</th><th>Asset</th><th>Lado</th>
                <th title="Precio mid del CLOB (SIM usa este)">Mid</th>
                <th title="Ask real del CLOB al momento de la apuesta">Fill</th>
                <th title="Fill − Mid">Slip</th>
                <th>Edge</th>
                <th title="PnL usando mid-price (simulación actual)">PnL Mid</th>
                <th title="PnL usando fill-price (equivalente LIVE)">PnL Fill</th>
                <th title="PnL Fill − PnL Mid">Dif</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {resolved.length === 0
                ? <tr><td colSpan={11} className="loading">Sin apuestas resueltas aún</td></tr>
                : [...resolved].reverse().map((b, i) => {
                  const fp   = b.fill_price
                  const sl   = b.slippage
                  const pf   = pnlFill(b)
                  const diff = pf - (b.pnl ?? 0)
                  const isUp = b.side === 'UP'
                  return (
                    <tr key={i}>
                      <td style={{ fontSize: 11, color: '#94a3b8' }}>{formatTs(b.timestamp)}</td>
                      <td><strong style={{ color: ASSET_COLOR[b.asset] ?? '#fff' }}>{b.asset}</strong></td>
                      <td><span className={`act-badge ${isUp ? 'up' : 'down'}`}>{isUp ? '▲' : '▼'} {b.side}</span></td>
                      <td style={{ fontFamily: 'monospace' }}>{b.price?.toFixed(3)}</td>
                      <td style={{ fontFamily: 'monospace', color: fp != null ? '#facc15' : '#475569' }}>
                        {fp != null ? fp.toFixed(3) : '—'}
                      </td>
                      <td style={{ fontFamily: 'monospace', color: sl != null ? (sl > 0.02 ? '#f87171' : '#86efac') : '#475569' }}>
                        {sl != null ? `+${(sl * 100).toFixed(2)}%` : '—'}
                      </td>
                      <td style={{ color: '#94a3b8', fontSize: 11 }}>
                        {b.edge ? `${(b.edge * 100).toFixed(1)}%` : '—'}
                      </td>
                      <td style={{ fontFamily: 'monospace', color: (b.pnl ?? 0) >= 0 ? '#4ade80' : '#f87171' }}>
                        {(b.pnl ?? 0) >= 0 ? '+' : ''}{(b.pnl ?? 0).toFixed(3)}
                      </td>
                      <td style={{ fontFamily: 'monospace', color: pf >= 0 ? '#4ade80' : '#f87171' }}>
                        {pf >= 0 ? '+' : ''}{pf.toFixed(3)}
                      </td>
                      <td style={{ fontFamily: 'monospace', color: diff >= 0 ? '#94a3b8' : '#f87171', fontSize: 11 }}>
                        {diff >= 0 ? '+' : ''}{diff.toFixed(3)}
                      </td>
                      <td>
                        <span style={{ color: b.status === 'WON' ? '#4ade80' : '#f87171', fontSize: 12 }}>
                          {b.status === 'WON' ? '✓ WON' : '✗ LOST'}
                        </span>
                      </td>
                    </tr>
                  )
                })
              }
            </tbody>
          </table>
        </div>
      </div>
    </>
  )
}

// ── App ───────────────────────────────────────────────────────────────────────
export default function App() {
  const [tab,        setTab]       = useState<'real' | 'sim' | 'poly' | 'spread'>('real')
  const [state,      setState]     = useState<BotState | null>(null)
  const [polyState,  setPolyState] = useState<BotState | null>(null)
  const [markets,    setMarkets]   = useState<Market[]>([])
  const [account,    setAccount]   = useState<Account | null>(null)
  const [botStatus,  setBotStatus] = useState<BotStatus>({ running: false, pid: null, live: { running: false, pid: null }, sim: { running: false, pid: null }, poly: { running: false, pid: null } })
  const [lastSync,   setLastSync]  = useState('')
  const [loading,    setLoading]   = useState(true)
  const [refreshing, setRefreshing]= useState(false)
  const [togglingLive, setTogglingLive] = useState(false)
  const [togglingSim,  setTogglingSim]  = useState(false)
  const [togglingPoly, setTogglingPoly] = useState(false)
  const [killingAll,   setKillingAll]   = useState(false)

  const fetchAll = useCallback(async (manual = false) => {
    if (manual) setRefreshing(true)
    try {
      const [s, m, a, bs, ps] = await Promise.all([
        fetch('/api/state').then(r => r.json()),
        fetch('/api/markets').then(r => r.json()),
        fetch('/api/account').then(r => r.json()),
        fetch('/api/bot/status').then(r => r.json()),
        fetch('/api/poly/state').then(r => r.json()),
      ])
      setState(s); setMarkets(Array.isArray(m) ? m : [])
      setAccount(a); setBotStatus(bs); setPolyState(ps)
      setLastSync(new Date().toLocaleTimeString('es-MX'))
    } catch (e) { console.error(e) }
    finally { setLoading(false); setRefreshing(false) }
  }, [])

  const toggleLive = async () => {
    setTogglingLive(true)
    try {
      const action = botStatus.live?.running ? 'stop' : 'start'
      const r = await fetch(`/api/bot/${action}`, { method: 'POST' })
      const d = await r.json()
      if (!d.ok) alert(d.msg)
      await fetchAll()
    } catch (e) { console.error(e) }
    finally { setTogglingLive(false) }
  }

  const toggleSim = async () => {
    setTogglingSim(true)
    try {
      const action = botStatus.sim?.running ? 'stop-sim' : 'start-sim'
      const r = await fetch(`/api/bot/${action}`, { method: 'POST' })
      const d = await r.json()
      if (!d.ok) alert(d.msg)
      await fetchAll()
    } catch (e) { console.error(e) }
    finally { setTogglingSim(false) }
  }

  const togglePoly = async () => {
    setTogglingPoly(true)
    try {
      const action = botStatus.poly?.running ? 'stop-poly' : 'start-poly'
      const r = await fetch(`/api/bot/${action}`, { method: 'POST' })
      const d = await r.json()
      if (!d.ok) alert(d.msg)
      await fetchAll()
    } catch (e) { console.error(e) }
    finally { setTogglingPoly(false) }
  }

  const killAll = async () => {
    if (!confirm('¿Detener todos los bots y resetear límites diarios?')) return
    setKillingAll(true)
    try {
      const r = await fetch('/api/bot/kill-all', { method: 'POST' })
      const d = await r.json()
      alert(d.msg)
      await fetchAll()
    } catch (e) { console.error(e) }
    finally { setKillingAll(false) }
  }

  useEffect(() => {
    fetchAll()
    const id = setInterval(() => fetchAll(), 30_000)
    return () => clearInterval(id)
  }, [fetchAll])

  if (loading) return <div className="loading" style={{ paddingTop: 80, fontSize: 16 }}>Cargando dashboard...</div>

  return (
    <div className="app">

      {/* ── Header ── */}
      <div className="header">
        <h1>Poly<span>Market</span> Bot</h1>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span className="refresh-info">Sync: {lastSync} · auto 30s</span>
          <button className="refresh-btn" onClick={() => fetchAll(true)} disabled={refreshing}>
            {refreshing ? '⟳ ...' : '⟳ Actualizar'}
          </button>
          <button
            className={`bot-toggle ${botStatus.sim?.running ? 'bot-sim-on' : 'bot-sim-off'}`}
            onClick={toggleSim}
            disabled={togglingSim}
            title={botStatus.sim?.running ? `SIM corriendo (PID ${botStatus.sim.pid})` : 'Iniciar bot simulación Binance'}
          >
            {togglingSim ? '...' : botStatus.sim?.running ? `⏹ SIM (PID ${botStatus.sim.pid})` : '▶ Iniciar SIM'}
          </button>
          <button
            className={`bot-toggle ${botStatus.poly?.running ? 'bot-poly-on' : 'bot-poly-off'}`}
            onClick={togglePoly}
            disabled={togglingPoly}
            title={botStatus.poly?.running ? `PM Signal corriendo (PID ${botStatus.poly.pid})` : 'Iniciar bot PM Signal (CLOB)'}
          >
            {togglingPoly ? '...' : botStatus.poly?.running ? `⏹ PM Signal (PID ${botStatus.poly.pid})` : '▶ PM Signal'}
          </button>
          <button
            className={`bot-toggle ${botStatus.live?.running ? 'bot-on' : 'bot-off'}`}
            onClick={toggleLive}
            disabled={togglingLive}
            title={botStatus.live?.running ? `LIVE corriendo (PID ${botStatus.live.pid})` : 'Iniciar bot LIVE con dinero real'}
          >
            {togglingLive ? '...' : botStatus.live?.running ? `⏹ LIVE (PID ${botStatus.live.pid})` : '▶ Iniciar Bot LIVE'}
          </button>
          <button
            className="bot-toggle bot-kill"
            onClick={killAll}
            disabled={killingAll}
            title="Detener todos los bots y resetear límite diario"
          >
            {killingAll ? '...' : '⏏ Reset'}
          </button>
        </div>
      </div>

      {/* ── Tabs ── */}
      <div className="tabs">
        <button className={`tab-btn ${tab === 'real' ? 'active-real' : ''}`} onClick={() => setTab('real')}>
          🟢 Polymarket Real
        </button>
        <button className={`tab-btn ${tab === 'sim'  ? 'active-sim'  : ''}`} onClick={() => setTab('sim')}>
          🟡 Simulación (Binance)
        </button>
        <button className={`tab-btn ${tab === 'poly' ? 'active-poly' : ''}`} onClick={() => setTab('poly')}>
          🔵 PM Signal (CLOB)
        </button>
        <button className={`tab-btn ${tab === 'spread' ? 'active-poly' : ''}`} onClick={() => setTab('spread')}>
          📊 Mid vs Fill
        </button>
      </div>

      {tab === 'real'   && <TabReal   account={account} markets={markets} state={state} polyState={polyState} />}
      {tab === 'sim'    && <TabSim    state={state}      markets={markets} />}
      {tab === 'poly'   && <TabPoly   state={polyState}  markets={markets} />}
      {tab === 'spread' && <TabSpread state={polyState} />}

      <div style={{ textAlign: 'center', color: '#1e2330', fontSize: 11, marginTop: 24 }}>
        PolyMarket Bot Dashboard · {new Date().getFullYear()}
      </div>
    </div>
  )
}
