import os
from dotenv import load_dotenv

load_dotenv()

# ─── MODE ────────────────────────────────────────────────────────────────────
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"

# ─── BANKROLL ─────────────────────────────────────────────────────────────────
INITIAL_BALANCE   = float(os.getenv("INITIAL_BALANCE", "20.00"))
BET_SIZE          = float(os.getenv("BET_SIZE",        "0.50"))   # apuesta fija $0.50
MIN_EDGE          = float(os.getenv("MIN_EDGE",        "0.08"))   # 8% edge mínimo
MAX_DAILY_BETS    = int(os.getenv("MAX_DAILY_BETS",    "10"))
DAILY_STOP_LOSS   = float(os.getenv("DAILY_STOP_LOSS", "3.00"))   # max perder $3/día

# ─── POLYMARKET APIs ─────────────────────────────────────────────────────────
GAMMA_API     = "https://gamma-api.polymarket.com"
CLOB_API      = "https://clob.polymarket.com"
DATA_API      = "https://data-api.polymarket.com"

# ─── WEATHER ─────────────────────────────────────────────────────────────────
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

# ─── TOP TRADERS TO COPY ─────────────────────────────────────────────────────
# Agrega aquí las wallet addresses de los mejores traders de clima
# sakula1 y similares - se buscan automáticamente vía leaderboard
TOP_TRADER_ADDRESSES = [
    addr.strip()
    for addr in os.getenv("TOP_TRADERS", "").split(",")
    if addr.strip() and addr.strip() != "0x0000000000000000000000000000000000000000"
]

# ─── CONFIG BOT CRYPTO ───────────────────────────────────────────────────────
CRYPTO_INITIAL_BALANCE = float(os.getenv("CRYPTO_INITIAL_BALANCE", "200.00"))
CRYPTO_BET_SIZE        = float(os.getenv("CRYPTO_BET_SIZE",        "5.00"))
CRYPTO_MIN_EDGE        = float(os.getenv("CRYPTO_MIN_EDGE",        "0.07"))
CRYPTO_MAX_DAILY_BETS  = int(os.getenv("CRYPTO_MAX_DAILY_BETS",    "20"))
CRYPTO_DAILY_STOP_LOSS = float(os.getenv("CRYPTO_DAILY_STOP_LOSS", "30.00"))

# ─── CONFIG BOT 5-MINUTOS BTC/ETH/SOL/XRP ────────────────────────────────────
CRYP5M_INITIAL_BALANCE  = float(os.getenv("CRYP5M_INITIAL_BALANCE",  "50.00"))
CRYP5M_BET_SIZE         = float(os.getenv("CRYP5M_BET_SIZE",         "1.00"))
CRYP5M_MIN_EDGE         = float(os.getenv("CRYP5M_MIN_EDGE",         "0.04"))
CRYP5M_MAX_DAILY_BETS   = int(os.getenv("CRYP5M_MAX_DAILY_BETS",     "100"))
CRYP5M_DAILY_STOP_LOSS  = float(os.getenv("CRYP5M_DAILY_STOP_LOSS",  "15.00"))
CRYP5M_MAX_BETS_PER_CYCLE = int(os.getenv("CRYP5M_MAX_BETS_PER_CYCLE", "2"))

# ─── TELEGRAM ────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN",   "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ─── CREDENCIALES ────────────────────────────────────────────────────────────
PRIVATE_KEY   = os.getenv("POLYMARKET_PRIVATE_KEY",  "")
API_KEY       = os.getenv("POLYMARKET_API_KEY",       "")
API_SECRET    = os.getenv("POLYMARKET_API_SECRET",    "")
PASSPHRASE    = os.getenv("POLYMARKET_PASSPHRASE",    "")
