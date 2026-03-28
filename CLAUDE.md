# polyMarketBot — Contexto para Claude

## Qué es esto
Bot de trading automatizado para Polymarket. Tiene 3 sub-bots independientes:
1. **Bot Clima** (`bot.py`) — apuesta en mercados de clima/temperatura
2. **Bot Crypto general** (`crypto_bot.py`) — mercados cripto de mayor plazo
3. **Bot 5 Minutos** (`cryp_signal_5minutes.py`) — el más activo, mercados de 5 min Up/Down en BTC/ETH/SOL/XRP/DOGE/HYPE

El foco actual de desarrollo es el **bot de 5 minutos**. Los otros dos están en segundo plano.

## Cómo correr el bot de 5 minutos
```bash
# Simulación (recomendado hasta tener 200+ trades con WR >= 56%)
PYTHONUTF8=1 PYTHONIOENCODING=utf-8 python cryp_signal_5minutes.py >> logs/bot_sim.log 2>&1

# Dashboard (React + Express)
cd dashboard && npm run dev
# Frontend: http://localhost:3000
# API backend: http://localhost:3001
```

## Arquitectura del bot 5 minutos
```
cryp_signal_5minutes.py   — Runner principal, scheduler cada 5 min
  ├── cryp_5m_scraper.py  — Busca mercados activos en Polymarket Gamma API
  ├── cryp_5m_analyzer.py — Señales técnicas: MACD, RSI, VWAP, CVD, momentum
  └── logs/cryp5m_state.json — Estado persistente (balance, historial, PnL)

dashboard/
  ├── src/App.tsx          — React frontend (tabs: Real / Simulación)
  └── server/index.ts      — Express API en puerto 3001
```

## Modo simulación vs live
- `DRY_RUN=true` → usa precios reales de Polymarket pero NO ejecuta órdenes CLOB
- `DRY_RUN=false` → ejecuta órdenes reales con `py_clob_client`
- Resultados (WON/LOST) siempre se resuelven consultando la Gamma API real
- La simulación es un proxy muy fiel del live (diferencia: spread/slippage del CLOB)

## REGLA CRITICA — nunca ir live sin validar primero
Hubo pérdida real de ~$22 porque `DRY_RUN=false` se activó por duplicate key en .env.
- **NUNCA** poner `DRY_RUN=false` sin antes tener 200+ trades simulados con WR >= 56%
- **NUNCA** duplicar `DRY_RUN=` en el .env — python-dotenv usa el último valor

## Estado del bot (logs/cryp5m_state.json)
```json
{
  "balance": 25.0,        // balance actual simulado
  "initial": 25.0,        // balance inicial
  "bets_today": 0,        // apuestas del día (reset a medianoche)
  "loss_today": 0.0,      // pérdida acumulada hoy
  "total_bets": 0,        // total histórico
  "total_won": 0,         // total ganadas
  "total_pnl": 0.0,       // PnL total
  "history": []           // array de Bet objects
}
```

Cada Bet tiene: `timestamp`, `asset`, `side` (UP/DOWN), `price`, `edge`, `bet_size`,
`status` (PENDING/WON/LOST), `pnl`, `market_id` (real Polymarket condition ID), `question`, `dry_run`.

## Resetear el bot
```bash
python reset_bot.py --balance 25       # reset completo con nuevo balance
python reset_bot.py --resume           # solo resetea contadores diarios
```

## Archivos de datos y APIs usadas
| Fuente | Uso |
|--------|-----|
| `gamma-api.polymarket.com` | Buscar mercados activos, resolver resultados |
| `api.binance.com` | Candles 1m para señales técnicas (spot) |
| `fapi.binance.com` | Candles 1m para HYPE y tokens solo en futuros |
| `logs/cryp5m_state.json` | Estado persistente del bot |
| `logs/bot_sim.log` | Log de ejecución |

## Señales técnicas (cryp_5m_analyzer.py)
- **MACD(3/15/3)** — cruce de líneas, señal más confiable para BTC
- **RSI(14)** — sobrecompra/sobreventa
- **VWAP** — precio vs volumen acumulado de la ventana
- **CVD divergence** — volumen comprador vs vendedor, señal para XRP
- **Window momentum** — la señal más fuerte, peso ±14% en probabilidad
- **Funding rate** — sesgo institucional de futuros perpetuos

## Configuración en .env (ver .env.example)
Variables clave del bot 5m:
- `CRYP5M_BET_SIZE` — tamaño de apuesta por trade (actual: $2.50)
- `CRYP5M_MIN_EDGE` — edge mínimo requerido (actual: 8%)
- `CRYP5M_MAX_PRICE` — precio máximo de entrada en ¢ (actual: 65¢)
- `CRYP5M_MIN_MOMENTUM` — momentum mínimo de ventana (actual: 5%)
- `CRYP5M_SIGNAL_{ASSET}` — filtro de señal por asset: `any|macd_cross|cvd_div|none`

## Dashboard
El dashboard tiene 2 tabs:
- **Polymarket Real** — balance y actividad real de la wallet
- **Simulación** — misma UI pero con datos del JSON simulado

Ambas tabs usan el mismo formato de activity feed estilo Polymarket:
icons ⊕/✓/✗/⏳, badges UP/DOWN, market_id truncado, timestamps relativos.
