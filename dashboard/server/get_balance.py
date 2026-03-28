"""Obtiene el balance real de Polymarket via py-clob-client."""
import sys, os, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), '../../.env'))

from py_clob_client.client import ClobClient
from py_clob_client.constants import POLYGON
from py_clob_client.clob_types import ApiCreds, BalanceAllowanceParams, AssetType

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

b = client.get_balance_allowance(BalanceAllowanceParams(asset_type=AssetType.COLLATERAL, signature_type=1))
balance_usdc = int(b.get('balance', '0')) / 1e6
print(json.dumps({'balance': balance_usdc}))
