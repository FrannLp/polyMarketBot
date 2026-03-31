# PolyMarket Bot

Automated trading bot suite for [Polymarket](https://polymarket.com). Each bot trades different markets using its own strategy.

---

## Bots

### 1. Weather Bot — `bot.py`
Trades **temperature and weather** markets (e.g. "Will it be above X°F in New York?").

- Signals: weather forecast analysis vs market price
- Config: `BOT_NAME`, `BET_SIZE`, `MIN_EDGE`, `MAX_DAILY_BETS`, `DAILY_STOP_LOSS` in `.env`
- Log: `logs/bot_sim.log`
- State: `logs/state.json`

```bash
python bot.py
```

---

### 2. General Crypto Bot — `crypto_bot.py`
Trades **longer-duration** crypto markets (e.g. "Will BTC be above $90k on Friday?").

- Signals: MACD, RSI, VWAP, CVD, momentum on Binance candles
- Config: `CRYPTO_*` variables in `.env`
- Log: `logs/crypto_bot.log`
- State: `logs/crypto_state.json`

```bash
python crypto_bot.py
```

---

### 3. 5-Minute Bot (Binance) — `cryp_signal_5minutes.py`
Trades **5-minute UP/DOWN** markets using Binance-based technical signals (BTC, ETH, SOL, XRP, DOGE, HYPE).

- Signals: MACD(3/15/3), RSI(14), VWAP, CVD divergence, window momentum, funding rate
- Config: `CRYP5M_*` variables in `.env`
- Cycle: every 5 minutes
- Log: `logs/bot_sim.log`
- State: `logs/cryp5m_state.json`

```bash
python cryp_signal_5minutes.py
```

---

### 4. Poly-5M Bot (LIVE) — `poly5m_bot.py` ⭐ active
Trades **5-minute UP/DOWN** markets using **Polymarket CLOB data only** (no Binance).

- Signals: demand imbalance (UP bids vs DOWN bids), price deviation
- Auto-claim: automatically claims winning positions through `redeemPositions` on Polygon
- Notifications: Telegram on each bet, result, and 10-minute summary
- Config: `POLY5M_*` variables in `.env`
- Cycle: bets every 5 minutes, resolution checks every 30 seconds
- Log: `logs/poly5m_bot.log`
- State: `logs/poly5m_state.json`

```bash
PYTHONUTF8=1 PYTHONIOENCODING=utf-8 python poly5m_bot.py >> logs/poly5m_bot.log 2>&1
```

---

## Dashboard

Web UI to monitor activity in real time.

```bash
cd dashboard && npm run dev
# Frontend: http://localhost:3000
# API:      http://localhost:3001
```

Tabs:
- **Polymarket Real** — wallet balance and LIVE bets
- **Simulation** — same UI with dry-run bot data

---

## Configuration (`.env`)

Copy `.env.example` and fill your credentials:

| Variable | Description |
|----------|-------------|
| `POLYMARKET_PRIVATE_KEY` | Your wallet private key |
| `POLYMARKET_PROXY_WALLET` | Your Polymarket proxy wallet address |
| `POLYMARKET_API_KEY` / `SECRET` / `PASSPHRASE` | CLOB API credentials |
| `TELEGRAM_TOKEN` / `TELEGRAM_CHAT_ID` | Telegram notifications |
| `POLY5M_DRY_RUN` | `true` = simulation, `false` = LIVE |
| `POLY5M_BET_SIZE` | Bet size in USDC |
| `POLY5M_MIN_EDGE` | Minimum required edge (e.g. `0.06` = 6%) |

> ⚠️ **NEVER** set `DRY_RUN=false` before validating 200+ simulated trades with WR ≥ 56%.

---

## Reset the Bot

```bash
python reset_bot.py --balance 25      # full reset
python reset_bot.py --resume          # reset daily counters only
```
