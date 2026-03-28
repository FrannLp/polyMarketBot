"""Obtiene trades y órdenes reales via py-clob-client."""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '../../.env'))

from py_clob_client.client import ClobClient
from py_clob_client.constants import POLYGON
from py_clob_client.clob_types import ApiCreds

PROXY_WALLET = os.getenv('POLYMARKET_PROXY_WALLET', '').lower()

client = ClobClient(
    'https://clob.polymarket.com',
    key=os.getenv('POLYMARKET_PRIVATE_KEY'),
    chain_id=POLYGON,
    creds=ApiCreds(
        api_key=os.getenv('POLYMARKET_API_KEY'),
        api_secret=os.getenv('POLYMARKET_API_SECRET'),
        api_passphrase=os.getenv('POLYMARKET_PASSPHRASE'),
    )
)

try:
    raw_trades = client.get_trades() or []
except Exception:
    raw_trades = []

# Resolve our real side: when we are a maker the top-level outcome is the
# taker's side, so look for our address in maker_orders instead.
trades = []
for t in raw_trades:
    my_maker = next(
        (m for m in (t.get('maker_orders') or [])
         if (m.get('maker_address') or '').lower() == PROXY_WALLET),
        None
    )
    outcome = my_maker['outcome'] if my_maker else t.get('outcome', '')
    t['my_outcome'] = outcome   # pre-resolved field for the frontend
    trades.append(t)

try:
    orders = client.get_orders() or []
except Exception:
    orders = []

print(json.dumps({'trades': trades[:20], 'orders': orders[:20]}))
