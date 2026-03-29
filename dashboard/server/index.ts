import express   from 'express'
import cors      from 'cors'
import fs        from 'fs'
import path      from 'path'
import https     from 'https'
import crypto    from 'crypto'
import { execSync, spawn } from 'child_process'
import type { ChildProcess } from 'child_process'

const app  = express()
const PORT = 3001

const STATE_FILE = path.join(__dirname, '../../logs/cryp5m_state.json')
const GAMMA_API  = 'https://gamma-api.polymarket.com'
const DATA_API   = 'https://data-api.polymarket.com'
const CLOB_API   = 'https://clob.polymarket.com'
const WALLET     = '0xfcC97e9a8AC8Ab85D5141CA69f49035d7Cb5a1a6'

// Cargar .env manualmente
const envPath = path.join(__dirname, '../../.env')
const envVars: Record<string, string> = {}
if (fs.existsSync(envPath)) {
  fs.readFileSync(envPath, 'utf-8').split('\n').forEach(line => {
    const [k, ...v] = line.split('=')
    if (k && !k.startsWith('#')) envVars[k.trim()] = v.join('=').trim()
  })
}
const API_KEY    = envVars['POLYMARKET_API_KEY']    || ''
const API_SECRET = envVars['POLYMARKET_API_SECRET'] || ''
const PASSPHRASE = envVars['POLYMARKET_PASSPHRASE'] || ''

app.use(cors())
app.use(express.json())

// ── Bot process manager ────────────────────────────────────────────────────────
let botLiveProcess: ChildProcess | null = null
let botSimProcess:  ChildProcess | null = null
let botPolyProcess: ChildProcess | null = null
const BOT_SCRIPT      = path.join(__dirname, '../../cryp_signal_5minutes.py')
const BOT_POLY_SCRIPT = path.join(__dirname, '../../poly5m_bot.py')
const BOT_LOG         = path.join(__dirname, '../../logs/bot_live.log')
const BOT_SIM_LOG     = path.join(__dirname, '../../logs/bot_sim.log')
const BOT_POLY_LOG    = path.join(__dirname, '../../logs/poly5m.log')

function isLiveRunning(): boolean { return botLiveProcess !== null && !botLiveProcess.killed }
function isSimRunning():  boolean { return botSimProcess  !== null && !botSimProcess.killed  }
function isPolyRunning(): boolean { return botPolyProcess !== null && !botPolyProcess.killed }

function spawnBot(script: string, env: Record<string, string>, logFile: string): ChildProcess {
  fs.mkdirSync(path.dirname(logFile), { recursive: true })
  const logStream = fs.createWriteStream(logFile, { flags: 'a' })
  const proc = spawn('python', [script], {
    cwd: path.join(__dirname, '../..'),
    detached: false,
    stdio: ['ignore', 'pipe', 'pipe'],
    env: { ...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8', ...env },
  })
  proc.stdout?.pipe(logStream)
  proc.stderr?.pipe(logStream)
  return proc
}

// GET /api/bot/status
app.get('/api/bot/status', (_req, res) => {
  res.json({
    running: isLiveRunning(),   // backwards compat
    pid:     botLiveProcess?.pid ?? null,
    live: { running: isLiveRunning(), pid: botLiveProcess?.pid ?? null },
    sim:  { running: isSimRunning(),  pid: botSimProcess?.pid  ?? null },
    poly: { running: isPolyRunning(), pid: botPolyProcess?.pid ?? null },
  })
})

// POST /api/bot/start  (LIVE)
app.post('/api/bot/start', (_req, res) => {
  if (isLiveRunning()) return res.json({ ok: false, msg: 'Bot LIVE ya está corriendo' })
  try {
    botLiveProcess = spawnBot(BOT_SCRIPT, { DRY_RUN: 'false', BOT_NAME: 'PROD' }, BOT_LOG)
    botLiveProcess.on('exit', (code) => { console.log(`Bot LIVE terminó con código ${code}`); botLiveProcess = null })
    res.json({ ok: true, msg: `Bot LIVE iniciado (PID ${botLiveProcess.pid})`, pid: botLiveProcess.pid })
  } catch (e: any) { res.status(500).json({ ok: false, msg: e.message }) }
})

// POST /api/bot/stop  (LIVE)
app.post('/api/bot/stop', (_req, res) => {
  if (!isLiveRunning()) return res.json({ ok: false, msg: 'Bot LIVE no está corriendo' })
  try { botLiveProcess!.kill('SIGTERM'); botLiveProcess = null; res.json({ ok: true, msg: 'Bot LIVE detenido' }) }
  catch (e: any) { res.status(500).json({ ok: false, msg: e.message }) }
})

// POST /api/bot/start-sim  (SIM)
app.post('/api/bot/start-sim', (_req, res) => {
  if (isSimRunning()) return res.json({ ok: false, msg: 'Bot SIM ya está corriendo' })
  try {
    botSimProcess = spawnBot(BOT_SCRIPT, { DRY_RUN: 'true', BOT_NAME: 'SIM' }, BOT_SIM_LOG)
    botSimProcess.on('exit', (code) => { console.log(`Bot SIM terminó con código ${code}`); botSimProcess = null })
    res.json({ ok: true, msg: `Bot SIM iniciado (PID ${botSimProcess.pid})`, pid: botSimProcess.pid })
  } catch (e: any) { res.status(500).json({ ok: false, msg: e.message }) }
})

// POST /api/bot/stop-sim  (SIM)
app.post('/api/bot/stop-sim', (_req, res) => {
  if (!isSimRunning()) return res.json({ ok: false, msg: 'Bot SIM no está corriendo' })
  try { botSimProcess!.kill('SIGTERM'); botSimProcess = null; res.json({ ok: true, msg: 'Bot SIM detenido' }) }
  catch (e: any) { res.status(500).json({ ok: false, msg: e.message }) }
})

// POST /api/bot/start-poly  (PM Signal CLOB)
app.post('/api/bot/start-poly', (_req, res) => {
  if (isPolyRunning()) return res.json({ ok: false, msg: 'Bot PM Signal ya está corriendo' })
  try {
    botPolyProcess = spawnBot(BOT_POLY_SCRIPT, { DRY_RUN: 'true', POLY5M_BOT_NAME: 'SIM' }, BOT_POLY_LOG)
    botPolyProcess.on('exit', (code) => { console.log(`Bot PM Signal terminó con código ${code}`); botPolyProcess = null })
    res.json({ ok: true, msg: `Bot PM Signal iniciado (PID ${botPolyProcess.pid})`, pid: botPolyProcess.pid })
  } catch (e: any) { res.status(500).json({ ok: false, msg: e.message }) }
})

// POST /api/bot/stop-poly  (PM Signal CLOB)
app.post('/api/bot/stop-poly', (_req, res) => {
  if (!isPolyRunning()) return res.json({ ok: false, msg: 'Bot PM Signal no está corriendo' })
  try { botPolyProcess!.kill('SIGTERM'); botPolyProcess = null; res.json({ ok: true, msg: 'Bot PM Signal detenido' }) }
  catch (e: any) { res.status(500).json({ ok: false, msg: e.message }) }
})

// POST /api/bot/kill-all  — mata todos los procesos Python del bot y resetea límites diarios
app.post('/api/bot/kill-all', (_req, res) => {
  try {
    // Kill tracked processes
    try { botLiveProcess?.kill('SIGTERM') } catch {}
    try { botSimProcess?.kill('SIGTERM')  } catch {}
    try { botPolyProcess?.kill('SIGTERM') } catch {}
    botLiveProcess = null
    botSimProcess  = null
    botPolyProcess = null
    // Kill any rogue Python processes running the bot script
    try { execSync('taskkill /F /IM python3.11.exe 2>nul || taskkill /F /IM python.exe 2>nul', { timeout: 5000 }) } catch {}
    // Reset daily counters in state file
    if (fs.existsSync(STATE_FILE)) {
      const state = JSON.parse(fs.readFileSync(STATE_FILE, 'utf-8'))
      state.bets_today   = 0
      state.loss_today   = 0
      state._last_stop_reason = ''
      fs.writeFileSync(STATE_FILE, JSON.stringify(state, null, 2))
    }
    res.json({ ok: true, msg: 'Todos los bots detenidos y límites reseteados' })
  } catch (e: any) { res.status(500).json({ ok: false, msg: e.message }) }
})

// ── Helper: HTTPS GET ──────────────────────────────────────────────────────────
function fetchJson(url: string, headers: Record<string, string> = {}): Promise<any> {
  return new Promise((resolve, reject) => {
    const opts = { headers: { 'Content-Type': 'application/json', ...headers } }
    https.get(url, opts, (res) => {
      let data = ''
      res.on('data', c => data += c)
      res.on('end', () => { try { resolve(JSON.parse(data)) } catch { resolve(null) } })
    }).on('error', reject)
  })
}

// ── Helper: Polymarket L2 HMAC headers ────────────────────────────────────────
function buildL2Headers(method: string, reqPath: string, body = ''): Record<string, string> {
  const ts  = Math.floor(Date.now() / 1000).toString()
  const msg = ts + method + reqPath + body
  const sig = crypto.createHmac('sha256', Buffer.from(API_SECRET, 'base64'))
                    .update(msg).digest('base64')
  return {
    'POLY-API-KEY':    API_KEY,
    'POLY-SIGNATURE':  sig,
    'POLY-TIMESTAMP':  ts,
    'POLY-PASSPHRASE': PASSPHRASE,
    'POLY-ADDRESS':    WALLET,
    'Content-Type':    'application/json',
  }
}

// ── GET /api/state ─────────────────────────────────────────────────────────────
app.get('/api/state', (_req, res) => {
  try {
    if (!fs.existsSync(STATE_FILE)) {
      return res.json({ balance: 20, initial: 20, total_pnl: 0, total_bets: 0, total_won: 0, bets_today: 0, loss_today: 0, history: [] })
    }
    res.json(JSON.parse(fs.readFileSync(STATE_FILE, 'utf-8')))
  } catch { res.status(500).json({ error: 'Error leyendo estado bot' }) }
})

// ── GET /api/poly/state — estado del bot Polymarket-only ──────────────────────
const POLY_STATE_FILE = path.join(__dirname, '../../logs/poly5m_state.json')
app.get('/api/poly/state', (_req, res) => {
  try {
    if (!fs.existsSync(POLY_STATE_FILE)) {
      return res.json({ balance: 25, initial: 25, total_pnl: 0, total_bets: 0, total_won: 0, bets_today: 0, loss_today: 0, history: [] })
    }
    res.json(JSON.parse(fs.readFileSync(POLY_STATE_FILE, 'utf-8')))
  } catch { res.status(500).json({ error: 'Error leyendo estado poly bot' }) }
})

// ── GET /api/markets ───────────────────────────────────────────────────────────
app.get('/api/markets', async (_req, res) => {
  const ASSETS  = ['btc', 'eth', 'sol', 'xrp']
  const now_ts  = Math.floor(Date.now() / 1000)
  const win_ts  = Math.floor(now_ts / 300) * 300
  const results: any[] = []
  const seen = new Set<string>()

  for (const asset of ASSETS) {
    for (const ts of [win_ts, win_ts + 300]) {
      try {
        const data  = await fetchJson(`${GAMMA_API}/events?slug=${asset}-updown-5m-${ts}&active=true`)
        const event = Array.isArray(data) ? data[0] : null
        if (!event) continue

        for (const m of event.markets || []) {
          const mid = m.conditionId || m.id
          if (!mid || seen.has(mid)) continue
          seen.add(mid)

          const ops    = m.outcomePrices || []
          const p_up   = parseFloat(ops[0]) || 0.5
          const p_down = parseFloat(ops[1]) || 0.5

          let token_ids: string[] = []
          try { token_ids = typeof m.clobTokenIds === 'string' ? JSON.parse(m.clobTokenIds) : (m.clobTokenIds || []) } catch {}

          results.push({
            asset: asset.toUpperCase(), market_id: mid,
            question: m.question || `${asset.toUpperCase()} Up or Down 5m`,
            price_up: p_up, price_down: p_down,
            token_up: token_ids[0] || null, token_down: token_ids[1] || null,
            end_date: m.endDate || null,
            volume: parseFloat(m.volume || '0'),
          })
        }
      } catch {}
    }
  }

  results.sort((a, b) => (a.end_date || '').localeCompare(b.end_date || ''))
  res.json(results)
})

// ── GET /api/account ── balance real + trades + orders ────────────────────────
app.get('/api/account', async (_req, res) => {
  try {
    // Balance real via Python script dedicado
    let balance = 0
    try {
      const scriptPath = path.join(__dirname, 'get_balance.py')
      const out  = execSync(`python "${scriptPath}"`, { timeout: 10000 }).toString().trim()
      const data = JSON.parse(out)
      balance    = data.balance ?? 0
    } catch (e: any) { console.error('Balance error:', e.message) }

    // Trades y órdenes vía Python script (usa py_clob_client con auth L2 correcta)
    let trades: any[] = [], orders: any[] = []
    try {
      const tradesScript = path.join(__dirname, 'get_trades.py')
      const out2 = execSync(`python "${tradesScript}"`, { timeout: 12000 }).toString().trim()
      const td = JSON.parse(out2)
      trades = td.trades ?? []
      orders = td.orders ?? []
    } catch (e: any) { console.error('Trades error:', e.message) }

    const [positionsData] = await Promise.allSettled([
      fetchJson(`${DATA_API}/positions?user=${WALLET}&limit=20`),
    ])
    const positions= positionsData.status=== 'fulfilled' ? (Array.isArray(positionsData.value)? positionsData.value: []) : []

    res.json({ wallet: WALLET, balance, trades: trades.slice(0, 20), orders, positions })
  } catch (e: any) {
    res.status(500).json({ error: e.message })
  }
})

app.listen(PORT, () => console.log(`API server en http://localhost:${PORT}`))
