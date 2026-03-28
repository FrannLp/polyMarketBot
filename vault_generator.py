"""
vault_generator.py
==================
Convierte los JSON logs de los bots en un vault de Obsidian con backlinks.

Crea una red visual de trades, assets, señales y outcomes que puedes ver
en el Graph View de Obsidian (Ctrl+G).

Cómo usar:
    python vault_generator.py

Luego abre la carpeta 'vault/' como vault en Obsidian.
En Graph View verás los nodos conectados: assets → señales → outcomes.

Estructura generada:
    vault/
    ├── trades/          ← un .md por apuesta (auto-generado)
    ├── assets/          ← hubs: BTC.md, ETH.md, Seoul.md, etc.
    ├── signals/         ← hubs: high-edge.md, copy-aligned.md, etc.
    ├── daily/           ← tus notas manuales (no tocadas por este script)
    ├── .claude/commands/ ← comandos slash para Claude Code
    └── README.md        ← índice principal
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

# ── Rutas ─────────────────────────────────────────────────────────────────────

VAULT        = Path("vault")
TRADES_DIR   = VAULT / "trades"
ASSETS_DIR   = VAULT / "assets"
SIGNALS_DIR  = VAULT / "signals"
DAILY_DIR    = VAULT / "daily"
COMMANDS_DIR = VAULT / ".claude" / "commands"

LOG_5M   = Path("logs/cryp5m_state.json")
LOG_TEMP = Path("logs/state.json")


# ── Helpers ───────────────────────────────────────────────────────────────────

def ensure_dirs() -> None:
    for d in [TRADES_DIR, ASSETS_DIR, SIGNALS_DIR, DAILY_DIR, COMMANDS_DIR]:
        d.mkdir(parents=True, exist_ok=True)


def load_json(path: Path) -> dict:
    if not path.exists():
        return {"history": [], "total_pnl": 0.0, "total_bets": 0, "total_won": 0, "balance": 0}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"history": [], "total_pnl": 0.0, "total_bets": 0, "total_won": 0, "balance": 0}


def slug(ts: str) -> str:
    """Convert ISO timestamp to safe filename slug: 2026-03-26_1620."""
    return ts[:16].replace("T", "_").replace(":", "").replace("-", "-", 2).replace("-", "-")


def pnl_str(pnl) -> str:
    if pnl is None:
        return "pendiente"
    return f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"


def win_rate(wins: int, total: int) -> str:
    if total == 0:
        return "sin datos"
    return f"{wins/total*100:.0f}% ({wins}/{total})"


# ── Señales por tipo de bot ────────────────────────────────────────────────────

def signals_5m(bet: dict) -> list[str]:
    """Etiquetas de señal inferidas de los datos de la apuesta 5m."""
    tags = []

    price = bet.get("price", 0.5)
    if price < 0.50:
        tags.append("token-barato")       # precio debajo de fair value teórico
    elif price > 0.60:
        tags.append("token-caro")         # riesgo EV negativo

    edge = bet.get("edge", 0)
    if edge >= 0.08:
        tags.append("edge-alto")
    elif edge >= 0.04:
        tags.append("edge-moderado")
    else:
        tags.append("edge-bajo")

    prob = bet.get("prob_win", 0.5)
    if prob >= 0.65:
        tags.append("confianza-alta")
    elif prob >= 0.55:
        tags.append("confianza-media")
    else:
        tags.append("confianza-baja")

    return tags


def signals_temp(bet: dict) -> list[str]:
    """Etiquetas de señal para el bot de temperatura."""
    tags = []

    conf = bet.get("confidence", "MEDIUM")
    if conf == "HIGH":
        tags.append("confianza-alta")
    elif conf == "MEDIUM":
        tags.append("confianza-media")
    else:
        tags.append("confianza-baja")

    if bet.get("copy_aligned"):
        tags.append("copy-aligned")
    else:
        tags.append("no-copy-aligned")

    edge = bet.get("edge", 0)
    if edge >= 0.10:
        tags.append("edge-alto")
    elif edge >= 0.05:
        tags.append("edge-moderado")
    else:
        tags.append("edge-bajo")

    return tags


# ── Escritura de trade notes ───────────────────────────────────────────────────

def write_5m_trade(bet: dict, idx: int) -> Path:
    """Genera un .md por apuesta del bot de 5 minutos."""
    ts     = bet.get("timestamp", "")
    asset  = bet.get("asset", "UNKNOWN")
    side   = bet.get("side", "UP")
    status = bet.get("status", "PENDING")
    price  = bet.get("price", 0)
    edge   = bet.get("edge", 0)
    prob   = bet.get("prob_win", 0)
    pnl    = bet.get("pnl")
    q      = bet.get("question", "")

    # Nombre de archivo único
    ts_slug  = slug(ts)
    fname    = f"{ts_slug}_{asset}_{side}_{idx:03d}.md"
    fpath    = TRADES_DIR / fname

    tags      = signals_5m(bet)
    tag_links = "  ".join(f"[[{t}]]" for t in tags)
    date_str  = ts[:10]

    # Extraer rango horario del question (e.g. "12:25PM-12:30PM ET")
    m = re.search(r',\s*(\d+:\d+[AP]M-\d+:\d+[AP]M\s*ET)', q)
    time_range = m.group(1) if m else ""

    content = f"""---
date: {date_str}
asset: {asset}
side: {side}
price: {price:.4f}
edge: {edge:.4f}
prob_win: {prob:.4f}
status: {status}
pnl: {pnl_str(pnl)}
bot: 5m-crypto
---

## [[{asset}]] {side} — {date_str} {time_range}

| Campo | Valor |
|-------|-------|
| Resultado | [[{status}]] |
| PnL | {pnl_str(pnl)} |
| Precio entrada | {price:.4f} |
| Edge | {edge*100:.1f}% |
| Prob estimada | {prob*100:.1f}% |
| Bot | [[5m-crypto-bot]] |

**Señales activas:** {tag_links}

> {q}

---
*Auto-generado — no editar*
"""
    fpath.write_text(content, encoding="utf-8")
    return fpath


def write_temp_trade(bet: dict, idx: int) -> Path:
    """Genera un .md por apuesta del bot de temperatura."""
    ts     = bet.get("timestamp", "")
    city   = bet.get("city", "UNKNOWN")
    side   = bet.get("side", "YES")
    status = bet.get("status", "PENDING")
    price  = bet.get("price", 0)
    edge   = bet.get("edge", 0)
    prob   = bet.get("prob_win", 0)
    pnl    = bet.get("pnl")
    conf   = bet.get("confidence", "?")
    copy_a = bet.get("copy_aligned", False)
    q      = bet.get("question", "")

    city_safe = re.sub(r'[^\w\-]', '-', city)
    ts_slug   = slug(ts)
    fname     = f"{ts_slug}_{city_safe}_{side}_{idx:03d}.md"
    fpath     = TRADES_DIR / fname

    tags      = signals_temp(bet)
    tag_links = "  ".join(f"[[{t}]]" for t in tags)
    date_str  = ts[:10]

    content = f"""---
date: {date_str}
city: {city}
side: {side}
price: {price:.4f}
edge: {edge:.4f}
prob_win: {prob:.4f}
confidence: {conf}
copy_aligned: {copy_a}
status: {status}
pnl: {pnl_str(pnl)}
bot: temperatura
---

## [[{city}]] {side} — {date_str}

| Campo | Valor |
|-------|-------|
| Resultado | [[{status}]] |
| PnL | {pnl_str(pnl)} |
| Precio entrada | {price:.4f} |
| Edge | {edge*100:.1f}% |
| Confianza | {conf} |
| Copy aligned | {"✅ Sí" if copy_a else "❌ No"} |
| Bot | [[temperatura-bot]] |

**Señales activas:** {tag_links}

> {q}

---
*Auto-generado — no editar*
"""
    fpath.write_text(content, encoding="utf-8")
    return fpath


# ── Hub notes ─────────────────────────────────────────────────────────────────

def write_asset_hub(name: str, trades: list[dict], bot_type: str) -> None:
    """Hub note por asset/ciudad — nodo central en el Graph View."""
    fpath = ASSETS_DIR / f"{name}.md"

    total   = len(trades)
    wins    = sum(1 for t in trades if t.get("status") == "WON")
    losses  = sum(1 for t in trades if t.get("status") == "LOST")
    pending = sum(1 for t in trades if t.get("status") == "PENDING")
    total_pnl = sum(t.get("pnl") or 0 for t in trades)

    # Últimos 5 trades como links
    recent = []
    for i, t in enumerate(reversed(trades[-5:])):
        ts    = t.get("timestamp", "")
        side  = t.get("side", "?")
        st    = t.get("status", "?")
        p     = pnl_str(t.get("pnl"))
        idx   = total - i - 1

        if bot_type == "5m-crypto":
            asset = t.get("asset", name)
            link_name = f"{slug(ts)}_{asset}_{side}_{idx:03d}"
        else:
            city_safe = re.sub(r'[^\w\-]', '-', name)
            link_name = f"{slug(ts)}_{city_safe}_{side}_{idx:03d}"

        recent.append(f"- [[{link_name}]] — {st} | {p}")

    recent_block = "\n".join(recent) if recent else "_Sin trades aún_"

    wr = win_rate(wins, wins + losses)

    content = f"""# {name}

**Bot:** [[{bot_type}-bot]]
**Total trades:** {total}  |  **Win rate:** {wr}
**PnL acumulado:** {pnl_str(total_pnl)}
**Pendientes:** {pending}

## Trades recientes
{recent_block}

---
*Actualizado: {datetime.now().strftime('%Y-%m-%d %H:%M')}*
"""
    fpath.write_text(content, encoding="utf-8")


def write_signal_hub(signal: str, trades: list[dict]) -> None:
    """Hub note por señal — muestra si realmente predice el resultado."""
    fpath = SIGNALS_DIR / f"{signal}.md"

    total   = len(trades)
    wins    = sum(1 for t in trades if t.get("status") == "WON")
    losses  = sum(1 for t in trades if t.get("status") == "LOST")
    pending = sum(1 for t in trades if t.get("status") == "PENDING")
    total_pnl = sum(t.get("pnl") or 0 for t in trades)

    resolved = wins + losses
    wr = win_rate(wins, resolved)

    if resolved >= 10:
        verdict = "✅ Significativa (≥10 muestras resueltas)"
        edge_verdict = "✅ Tiene edge real" if wins / resolved > 0.55 else "❌ Sin edge real"
    elif resolved >= 5:
        verdict = "⚠️ Pocas muestras (5-9 resueltas)"
        edge_verdict = "— Insuficiente para concluir"
    else:
        verdict = "🔄 Sin datos suficientes"
        edge_verdict = "— Pendiente de más trades"

    content = f"""# Señal: {signal}

**Veces activa:** {total}  |  **Resueltas:** {resolved}  |  **Pendientes:** {pending}
**Win rate:** {wr}
**PnL generado:** {pnl_str(total_pnl)}

## ¿Tiene edge real?
{edge_verdict}
{verdict}

---
*Actualizado: {datetime.now().strftime('%Y-%m-%d %H:%M')}*
"""
    fpath.write_text(content, encoding="utf-8")


# ── Bot hub notes ──────────────────────────────────────────────────────────────

def write_bot_hubs(state_5m: dict, state_temp: dict) -> None:
    h5m   = state_5m.get("history", [])
    htemp = state_temp.get("history", [])

    total_5m  = len(h5m)
    won_5m    = state_5m.get("total_won", 0)
    pnl_5m    = state_5m.get("total_pnl", 0.0)
    bal_5m    = state_5m.get("balance", 0)

    total_t   = len(htemp)
    won_t     = state_temp.get("total_won", 0)
    pnl_t     = state_temp.get("total_pnl", 0.0)
    bal_t     = state_temp.get("balance", 0)

    content_5m = f"""# 5m-crypto-bot

Bot UP/DOWN en ventanas de 5 minutos para [[BTC]], [[ETH]], [[SOL]], [[XRP]].

**Parámetros actuales:**
- MIN_EDGE = 4%  |  MAX_PRICE = 0.65  |  MIN_MOMENTUM = 0.05%
- Ciclo: cada 5 minutos (:30s tras apertura de ventana)

**Performance:**
- Total apuestas: {total_5m}
- Win rate: {win_rate(won_5m, total_5m)}
- PnL: {pnl_str(pnl_5m)}
- Balance actual: ${bal_5m:.2f}

## Assets activos
[[BTC]]  [[ETH]]  [[SOL]]  [[XRP]]
"""
    (ASSETS_DIR / "5m-crypto-bot.md").write_text(content_5m, encoding="utf-8")

    content_t = f"""# temperatura-bot

Bot de mercados de temperatura en Polymarket.
Combina análisis climático + copy de mejores traders.

**Parámetros actuales:**
- BET_SIZE = $0.50  |  Ciclo: cada 30 minutos

**Performance:**
- Total apuestas: {total_t}
- Win rate: {win_rate(won_t, total_t)}
- PnL: {pnl_str(pnl_t)}
- Balance actual: ${bal_t:.2f}
"""
    (ASSETS_DIR / "temperatura-bot.md").write_text(content_t, encoding="utf-8")

    # Outcome hub notes
    for outcome in ["WON", "LOST", "PENDING"]:
        (SIGNALS_DIR / f"{outcome}.md").write_text(
            f"# {outcome}\n\nTodos los trades con resultado [[{outcome}]].\n"
            f"Ver Graph View para visualizar qué señales/assets conectan a este nodo.\n",
            encoding="utf-8",
        )


# ── Comandos Claude ────────────────────────────────────────────────────────────

def write_commands() -> None:
    cmds = {
        "edge.md": """\
Análisis de edge real en mis bots de Polymarket.

Lee todos los archivos en vault/trades/, vault/assets/ y vault/signals/.
Cruza con los JSON en logs/cryp5m_state.json y logs/state.json.

Analiza:
1. ¿Qué asset (BTC/ETH/SOL/XRP) tiene mejor win rate real?
2. ¿Qué ciudades del bot de temperatura tienen edge positivo?
3. ¿Qué señales (signals/) correlacionan con wins vs losses?
4. ¿Hay sesgo direccional? (más wins en UP que DOWN, o YES que NO)
5. ¿El edge mínimo actual (4% crypto, variable temp) es correcto o debería ajustarse?
6. ¿Qué patrón se repite en las pérdidas?
7. ¿Hay algún horario o condición donde gano más?

Dame conclusiones concretas y accionables. Qué parámetros cambiaría.
""",

        "briefing.md": """\
Briefing diario de mis bots de Polymarket.

Lee vault/assets/5m-crypto-bot.md y vault/assets/temperatura-bot.md.
Lee los últimos trades en vault/trades/ (ordenados por fecha desc).
Lee logs/cryp5m_state.json y logs/state.json para los números actuales.

Dame en menos de 15 líneas:
- PnL de ayer vs hoy por bot
- Win rate actual de cada bot
- Mejor y peor asset/ciudad de la semana
- ¿Algún bot debería pausarse?
- Una alerta si hay patrón preocupante en los últimos 10 trades

Directo al punto. Sin fluff.
""",

        "signals.md": """\
Análisis de señales — ¿cuáles realmente predicen el resultado?

Lee todos los archivos en vault/signals/.
Cruza con vault/trades/ para ver correlaciones reales.

Para cada señal con ≥5 trades resueltos:
- Win rate cuando activa
- PnL total generado por esa señal
- ¿Es estadísticamente significativa?
- Veredicto: MANTENER / ELIMINAR / SUBIR PESO

Output: tabla markdown con columnas: señal | muestras | win rate | PnL | veredicto
""",

        "postmortem.md": """\
Post-mortem de una apuesta específica. Te diré cuál.

Busca el trade en vault/trades/ por asset/ciudad + fecha.
Lee el hub del asset en vault/assets/.
Lee las señales del trade en vault/signals/.

Analiza:
1. ¿La señal era correcta o era ruido?
2. ¿El precio de entrada tenía EV positivo real?
3. ¿Qué salió mal / bien comparado con trades similares?
4. ¿Qué cambiarías en los parámetros para evitar este error?

Sé brutalmente honesto. Sin justificaciones.
""",

        "compare-bots.md": """\
Comparación entre el bot de 5 minutos y el bot de temperatura.

Lee logs/cryp5m_state.json y logs/state.json.
Lee vault/assets/5m-crypto-bot.md y vault/assets/temperatura-bot.md.

Dime:
- ¿Cuál bot tiene mejor risk-adjusted return? (PnL / capital en riesgo)
- ¿Cuál tiene win rate más estable?
- ¿Cuál tiene más drawdown diario?
- ¿Qué capital debería asignar a cada uno?
- Si tuviera que pausar uno, ¿cuál y por qué?

Dame una recomendación de allocación concreta.
""",
    }

    for fname, content in cmds.items():
        (COMMANDS_DIR / fname).write_text(content, encoding="utf-8")


# ── README / índice ────────────────────────────────────────────────────────────

def write_readme(state_5m: dict, state_temp: dict) -> None:
    h5m       = state_5m.get("history", [])
    htemp     = state_temp.get("history", [])
    total     = len(h5m) + len(htemp)
    pnl_total = state_5m.get("total_pnl", 0) + state_temp.get("total_pnl", 0)

    content = f"""# Polymarket Trading Brain

**Actualizado:** {datetime.now().strftime('%Y-%m-%d %H:%M')}
**Total trades:** {total}  |  **PnL combinado:** {pnl_str(pnl_total)}

---

## Bots activos

| Bot | Archivo | Trades | PnL |
|-----|---------|--------|-----|
| Crypto 5M | [[5m-crypto-bot]] | {len(h5m)} | {pnl_str(state_5m.get('total_pnl', 0))} |
| Temperatura | [[temperatura-bot]] | {len(htemp)} | {pnl_str(state_temp.get('total_pnl', 0))} |

## Assets crypto
[[BTC]]  ·  [[ETH]]  ·  [[SOL]]  ·  [[XRP]]

## Outcomes
[[WON]]  ·  [[LOST]]  ·  [[PENDING]]

## Señales de calidad
[[edge-alto]]  ·  [[edge-moderado]]  ·  [[confianza-alta]]  ·  [[copy-aligned]]

## Comandos Claude Code disponibles
Abre terminal en esta carpeta y ejecuta `claude`, luego:

| Comando | Para qué |
|---------|----------|
| `/edge` | Patrones reales en el historial completo |
| `/briefing` | Resumen diario de performance |
| `/signals` | ¿Qué señales tienen edge real? |
| `/postmortem` | Análisis de una apuesta específica |
| `/compare-bots` | Cuál bot es más rentable |

## Graph View
Abre **Ctrl+G** en Obsidian para ver la red de trading.
Los nodos más conectados revelan tus patrones reales.

---
> **Regla:** Tú escribes en `daily/`. Los bots escriben en `trades/`.
> Claude lee todo. Claude no escribe aquí.
"""
    (VAULT / "README.md").write_text(content, encoding="utf-8")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print("Generando Obsidian vault...")
    ensure_dirs()

    state_5m   = load_json(LOG_5M)
    state_temp = load_json(LOG_TEMP)

    h5m   = state_5m.get("history", [])
    htemp = state_temp.get("history", [])

    # Mapas para hubs
    asset_trades:  dict[str, list] = {}
    signal_trades: dict[str, list] = {}

    # ── Trades del bot 5m ────────────────────────────────────────────────────
    print(f"  5m-crypto: {len(h5m)} trades")
    for i, bet in enumerate(h5m):
        write_5m_trade(bet, i)
        asset = bet.get("asset", "UNKNOWN")
        asset_trades.setdefault(asset, []).append({"bot": "5m", **bet})
        for sig in signals_5m(bet):
            signal_trades.setdefault(sig, []).append(bet)

    # ── Trades del bot temperatura ───────────────────────────────────────────
    print(f"  temperatura: {len(htemp)} trades")
    for i, bet in enumerate(htemp):
        write_temp_trade(bet, i)
        city = bet.get("city", "UNKNOWN")
        asset_trades.setdefault(city, []).append({"bot": "temp", **bet})
        for sig in signals_temp(bet):
            signal_trades.setdefault(sig, []).append(bet)

    # ── Asset hubs ───────────────────────────────────────────────────────────
    crypto_assets = {"BTC", "ETH", "SOL", "XRP"}
    for name, trades in asset_trades.items():
        bot_type = "5m-crypto" if name in crypto_assets else "temperatura"
        write_asset_hub(name, trades, bot_type)

    # ── Signal hubs ──────────────────────────────────────────────────────────
    for signal, trades in signal_trades.items():
        write_signal_hub(signal, trades)

    # ── Bot hubs + outcomes ──────────────────────────────────────────────────
    write_bot_hubs(state_5m, state_temp)

    # ── Comandos y README ────────────────────────────────────────────────────
    write_commands()
    write_readme(state_5m, state_temp)

    # ── Resumen ──────────────────────────────────────────────────────────────
    trade_files  = len(list(TRADES_DIR.glob("*.md")))
    asset_files  = len(list(ASSETS_DIR.glob("*.md")))
    signal_files = len(list(SIGNALS_DIR.glob("*.md")))
    cmd_files    = len(list(COMMANDS_DIR.glob("*.md")))

    print(f"""
Vault generado en: vault/
  trades/   : {trade_files} archivos  (un nodo por apuesta)
  assets/   : {asset_files} archivos  (hubs: BTC, ETH, ciudades...)
  signals/  : {signal_files} archivos  (hubs: edge-alto, copy-aligned...)
  commands/ : {cmd_files} comandos Claude

Proximos pasos:
  1. Instala Obsidian (obsidian.md) -- es gratis
  2. Abre vault/ como vault en Obsidian
  3. Ctrl+G -- Graph View -- veras la red de trading
  4. Abre terminal en vault/ -- escribe 'claude' -- usa /edge, /briefing, etc.
  5. Corre 'python vault_generator.py' cada vez que quieras actualizar el grafo
""")


if __name__ == "__main__":
    main()
