"""
test_account.py
===============
Verifica balance, ordenes activas y mercados 5min disponibles.
"""
import os
from dotenv import load_dotenv
load_dotenv()

from py_clob_client.client import ClobClient
from py_clob_client.constants import POLYGON
import requests

PRIVATE_KEY = os.getenv("POLYMARKET_PRIVATE_KEY")
API_KEY     = os.getenv("POLYMARKET_API_KEY")
API_SECRET  = os.getenv("POLYMARKET_API_SECRET")
PASSPHRASE  = os.getenv("POLYMARKET_PASSPHRASE")
HOST        = "https://clob.polymarket.com"
GAMMA_API   = "https://gamma-api.polymarket.com"

# ── Init cliente ──────────────────────────────────────────────────────────────
from py_clob_client.clob_types import ApiCreds
client = ClobClient(
    HOST,
    key=PRIVATE_KEY,
    chain_id=POLYGON,
    creds=ApiCreds(
        api_key=API_KEY,
        api_secret=API_SECRET,
        api_passphrase=PASSPHRASE,
    ),
)

print("=" * 55)
print("  POLYMARKET ACCOUNT CHECK")
print("=" * 55)

# ── 1. Balance ────────────────────────────────────────────────────────────────
print("\n[1] BALANCE EN POLYMARKET")
print("-" * 40)
try:
    balance = client.get_balance()
    print(f"  Balance USDC: ${balance}")
except Exception as e:
    print(f"  ERROR: {e}")

# ── 2. Ordenes abiertas ───────────────────────────────────────────────────────
print("\n[2] ORDENES ABIERTAS")
print("-" * 40)
try:
    orders = client.get_orders()
    if not orders:
        print("  Sin ordenes abiertas actualmente.")
    else:
        for o in orders[:10]:
            print(f"  ID: {o.get('id','?')[:12]}...  "
                  f"Side: {o.get('side','?')}  "
                  f"Price: {o.get('price','?')}  "
                  f"Size: {o.get('size','?')}  "
                  f"Status: {o.get('status','?')}")
except Exception as e:
    print(f"  ERROR: {e}")

# ── 3. Trades / historial ─────────────────────────────────────────────────────
print("\n[3] ULTIMOS TRADES (historial)")
print("-" * 40)
try:
    trades = client.get_trades()
    if not trades:
        print("  Sin trades en el historial.")
    else:
        for t in trades[:5]:
            print(f"  {t.get('created_at','?')[:16]}  "
                  f"Side: {t.get('side','?')}  "
                  f"Price: {t.get('price','?')}  "
                  f"Size: {t.get('size','?')}  "
                  f"Status: {t.get('status','?')}")
except Exception as e:
    print(f"  ERROR: {e}")

# ── 4. Mercados 5min disponibles ──────────────────────────────────────────────
print("\n[4] MERCADOS 5MIN ACTIVOS EN POLYMARKET")
print("-" * 40)
import time
ASSETS = ["btc", "eth", "sol", "xrp"]
now_ts  = int(time.time())
win_ts  = (now_ts // 300) * 300

found = 0
for asset in ASSETS:
    for ts in [win_ts, win_ts + 300]:
        slug = f"{asset}-updown-5m-{ts}"
        try:
            resp = requests.get(f"{GAMMA_API}/events", params={"slug": slug, "active": "true"}, timeout=6)
            data = resp.json()
            event = data[0] if isinstance(data, list) and data else None
            if not event:
                continue
            markets = event.get("markets", [])
            for m in markets:
                ops = m.get("outcomePrices", [])
                try:
                    p_up   = float(ops[0]) if len(ops) > 0 else 0.5
                    p_down = float(ops[1]) if len(ops) > 1 else 0.5
                except Exception:
                    p_up = p_down = 0.5

                token_ids = m.get("clobTokenIds") or []
                tid_up   = token_ids[0] if len(token_ids) > 0 else "N/A"
                tid_down = token_ids[1] if len(token_ids) > 1 else "N/A"

                print(f"  [{asset.upper()}]  {m.get('question','?')[:55]}")
                print(f"         Precio UP: {p_up:.2f}  DOWN: {p_down:.2f}  "
                      f"Vol: ${float(m.get('volume') or 0):,.0f}")
                print(f"         TokenID UP  : {str(tid_up)[:20]}...")
                print(f"         TokenID DOWN: {str(tid_down)[:20]}...")
                print(f"         End: {m.get('endDate','?')[:19]}")
                print()
                found += 1
        except Exception as e:
            pass

if found == 0:
    print("  No se encontraron mercados 5min activos ahora mismo.")
    print("  (Puede ser que estemos entre ventanas — intenta en 1-2 min)")

print("=" * 55)
