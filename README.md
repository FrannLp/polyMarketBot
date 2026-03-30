# PolyMarket Bot

Suite de bots de trading automatizado para [Polymarket](https://polymarket.com). Cada bot opera en mercados distintos con su propia estrategia.

---

## Bots

### 1. Bot Clima — `bot.py`
Apuesta en mercados de **temperatura y clima** (ej. "¿Estará por encima de X°F en Nueva York?").

- Señales: análisis de pronósticos meteorológicos vs precio de mercado
- Configuración: variables `BOT_NAME`, `BET_SIZE`, `MIN_EDGE`, `MAX_DAILY_BETS`, `DAILY_STOP_LOSS` en `.env`
- Log: `logs/bot_sim.log`
- Estado: `logs/state.json`

```bash
python bot.py
```

---

### 2. Bot Crypto General — `crypto_bot.py`
Apuesta en mercados cripto de **mayor plazo** (ej. "¿BTC por encima de $90k el viernes?").

- Señales: MACD, RSI, VWAP, CVD, momentum sobre candles de Binance
- Configuración: variables `CRYPTO_*` en `.env`
- Log: `logs/crypto_bot.log`
- Estado: `logs/crypto_state.json`

```bash
python crypto_bot.py
```

---

### 3. Bot 5 Minutos (Binance) — `cryp_signal_5minutes.py`
Apuesta en mercados **UP/DOWN de 5 minutos** usando señales técnicas de Binance (BTC, ETH, SOL, XRP, DOGE, HYPE).

- Señales: MACD(3/15/3), RSI(14), VWAP, CVD divergence, window momentum, funding rate
- Configuración: variables `CRYP5M_*` en `.env`
- Ciclo: cada 5 minutos
- Log: `logs/bot_sim.log`
- Estado: `logs/cryp5m_state.json`

```bash
python cryp_signal_5minutes.py
```

---

### 4. Bot Poly-5M (LIVE) — `poly5m_bot.py` ⭐ activo
Apuesta en mercados **UP/DOWN de 5 minutos** usando **solo datos del CLOB de Polymarket** (sin Binance).

- Señales: demand imbalance (bids UP vs bids DOWN), price deviation
- Auto-claim: reclama automáticamente las posiciones ganadoras vía `redeemPositions` en Polygon
- Notificaciones: Telegram en cada apuesta, resultado y resumen cada 10 min
- Configuración: variables `POLY5M_*` en `.env`
- Ciclo: apuestas cada 5 min, resoluciones cada 30 seg
- Log: `logs/poly5m_bot.log`
- Estado: `logs/poly5m_state.json`

```bash
PYTHONUTF8=1 PYTHONIOENCODING=utf-8 python poly5m_bot.py >> logs/poly5m_bot.log 2>&1
```

---

## Dashboard

Interfaz web para monitorear actividad en tiempo real.

```bash
cd dashboard && npm run dev
# Frontend: http://localhost:3000
# API:      http://localhost:3001
```

Tabs:
- **Polymarket Real** — balance y bets LIVE de la wallet
- **Simulación** — misma UI con datos del bot en modo dry-run

---

## Configuración (`.env`)

Copia `.env.example` y rellena tus credenciales:

| Variable | Descripción |
|----------|-------------|
| `POLYMARKET_PRIVATE_KEY` | Clave privada de tu wallet |
| `POLYMARKET_PROXY_WALLET` | Dirección del proxy wallet de Polymarket |
| `POLYMARKET_API_KEY` / `SECRET` / `PASSPHRASE` | Credenciales CLOB API |
| `TELEGRAM_TOKEN` / `TELEGRAM_CHAT_ID` | Notificaciones Telegram |
| `POLY5M_DRY_RUN` | `true` = simulación, `false` = LIVE |
| `POLY5M_BET_SIZE` | Tamaño de apuesta en USDC |
| `POLY5M_MIN_EDGE` | Edge mínimo requerido (ej. `0.06` = 6%) |

> ⚠️ **NUNCA** pongas `DRY_RUN=false` sin antes validar 200+ trades simulados con WR ≥ 56%.

---

## Reset del bot

```bash
python reset_bot.py --balance 25      # reset completo
python reset_bot.py --resume          # solo resetea contadores diarios
```
