"""
test_login.py
=============
Verifica conexion con Polymarket CLOB API usando las credenciales del .env
"""
import os
from dotenv import load_dotenv
load_dotenv()

from py_clob_client.client import ClobClient
from py_clob_client.constants import POLYGON

PRIVATE_KEY = os.getenv("POLYMARKET_PRIVATE_KEY", "")
HOST        = "https://clob.polymarket.com"
CHAIN_ID    = POLYGON  # 137

print("=" * 50)
print("  POLYMARKET LOGIN TEST")
print("=" * 50)
print(f"  Host     : {HOST}")
print(f"  Chain ID : {CHAIN_ID}")
print(f"  Key      : {PRIVATE_KEY[:8]}...{PRIVATE_KEY[-4:]}")
print()

try:
    # Paso 1: Conectar con private key (L1)
    print("[1/3] Conectando con private key...")
    client = ClobClient(HOST, key=PRIVATE_KEY, chain_id=CHAIN_ID)
    print("      OK — cliente inicializado")

    # Paso 2: Derivar/obtener credenciales L2
    print("[2/3] Derivando credenciales L2 (API key)...")
    creds = client.create_or_derive_api_creds()
    print(f"      OK — API Key: {creds.api_key[:8]}...")
    print(f"           Secret : {creds.api_secret[:8]}...")
    print(f"           Pass   : {creds.api_passphrase[:8]}...")

    # Paso 3: Verificar con llamada autenticada
    print("[3/3] Verificando sesion autenticada...")
    client.set_api_creds(creds)
    ok = client.get_ok()
    print(f"      Respuesta: {ok}")

    print()
    print("=" * 50)
    print("  CONECTADO EXITOSAMENTE")
    print("=" * 50)

    # Guardar credenciales derivadas en pantalla para el .env
    print()
    print("Credenciales L2 derivadas:")
    print(f"  POLYMARKET_API_KEY    = {creds.api_key}")
    print(f"  POLYMARKET_API_SECRET = {creds.api_secret}")
    print(f"  POLYMARKET_PASSPHRASE = {creds.api_passphrase}")

except Exception as e:
    print()
    print(f"ERROR: {e}")
    print()
    print("Verifica que la POLYMARKET_PRIVATE_KEY en .env sea correcta.")
