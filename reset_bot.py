"""
reset_bot.py — Utilidad para resetear el estado del bot 5M

Uso:
  python reset_bot.py            → Reset completo: $500, historial limpio
  python reset_bot.py --resume   → Solo despausa (resetea stop-loss diario y bets_today)
  python reset_bot.py --balance 300  → Reset completo con balance personalizado
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

STATE_FILE = Path("logs/cryp5m_state.json")


def load() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save(state: dict) -> None:
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")


def full_reset(balance: float) -> None:
    state = {
        "balance":    balance,
        "initial":    balance,
        "bets_today": 0,
        "loss_today": 0.0,
        "total_bets": 0,
        "total_won":  0,
        "total_pnl":  0.0,
        "last_reset": datetime.now(timezone.utc).date().isoformat(),
        "history":    [],
    }
    save(state)
    print(f"[OK] Reset completo -- Balance: ${balance:.2f} | Historial limpio")


def resume() -> None:
    """Despausa el bot reseteando solo los contadores diarios."""
    state = load()
    if not state:
        print("[WARN] No hay estado guardado. Usa reset sin --resume primero.")
        return

    old_loss = state.get("loss_today", 0)
    old_bets = state.get("bets_today", 0)
    state["loss_today"] = 0.0
    state["bets_today"] = 0
    state["last_reset"] = datetime.now(timezone.utc).date().isoformat()
    save(state)

    balance = state.get("balance", 0)
    total_bets = state.get("total_bets", 0)
    won = state.get("total_won", 0)
    pnl = state.get("total_pnl", 0)
    print(
        f"[RESUME] Bot reanudado\n"
        f"   Loss diaria reseteada: ${old_loss:.2f} -> $0.00\n"
        f"   Bets hoy reseteadas:   {old_bets} -> 0\n"
        f"   Balance: ${balance:.2f} | PnL total: ${pnl:+.2f} | Win: {won}/{total_bets}"
    )


def main():
    args = sys.argv[1:]

    if "--resume" in args:
        resume()
        return

    balance = 500.0
    if "--balance" in args:
        idx = args.index("--balance")
        try:
            balance = float(args[idx + 1])
        except (IndexError, ValueError):
            print("❌ Valor de balance inválido. Ej: python reset_bot.py --balance 300")
            sys.exit(1)

    full_reset(balance)


if __name__ == "__main__":
    main()
