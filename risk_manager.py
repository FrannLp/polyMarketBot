"""
risk_manager.py
===============
Gestiona el riesgo: balance simulado, stop-loss diario, max apuestas/día.
Con $20 y apuestas de $0.50.
"""

import json
import os
from datetime import datetime, date
from config import (
    INITIAL_BALANCE, BET_SIZE, MAX_DAILY_BETS, DAILY_STOP_LOSS,
    CRYPTO_INITIAL_BALANCE, CRYPTO_BET_SIZE, CRYPTO_MAX_DAILY_BETS, CRYPTO_DAILY_STOP_LOSS,
    WEATHER_INITIAL_BALANCE, WEATHER_BET_SIZE, WEATHER_MAX_DAILY_BETS, WEATHER_DAILY_STOP_LOSS,
)

STATE_FILE        = os.path.join(os.path.dirname(__file__), "logs", "state.json")
CRYPTO_STATE_FILE = os.path.join(os.path.dirname(__file__), "logs", "crypto_state.json")


def _load_state() -> dict:
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "balance":       INITIAL_BALANCE,
        "initial":       INITIAL_BALANCE,
        "bets_today":    0,
        "loss_today":    0.0,
        "total_bets":    0,
        "total_won":     0,
        "total_pnl":     0.0,
        "last_reset":    date.today().isoformat(),
        "history":       [],
    }


def _save_state(state: dict):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


def _reset_daily_if_needed(state: dict) -> dict:
    today = date.today().isoformat()
    if state.get("last_reset") != today:
        state["bets_today"] = 0
        state["loss_today"] = 0.0
        state["last_reset"] = today
    return state


class RiskManager:
    def __init__(self):
        self.state = _load_state()
        self.state = _reset_daily_if_needed(self.state)
        _save_state(self.state)

    @property
    def balance(self) -> float:
        return round(self.state["balance"], 4)

    @property
    def bets_today(self) -> int:
        return self.state["bets_today"]

    @property
    def loss_today(self) -> float:
        return round(self.state["loss_today"], 4)

    @property
    def total_bets(self) -> int:
        return self.state["total_bets"]

    @property
    def win_rate(self) -> float:
        if self.state["total_bets"] == 0:
            return 0.0
        return round(self.state["total_won"] / self.state["total_bets"], 4)

    @property
    def total_pnl(self) -> float:
        return round(self.state["total_pnl"], 4)

    def already_bet(self, market_id: str) -> bool:
        """Retorna True si ya hay una apuesta PENDING en este mercado."""
        return any(
            b["market_id"] == market_id and b["status"] == "PENDING"
            for b in self.state["history"]
        )

    def can_bet(self) -> tuple[bool, str]:
        """Verifica si se puede hacer una apuesta."""
        self.state = _reset_daily_if_needed(self.state)

        if self.state["balance"] < BET_SIZE:
            return False, f"Balance insuficiente: ${self.balance:.2f} < ${BET_SIZE}"

        if self.state["bets_today"] >= MAX_DAILY_BETS:
            return False, f"Maximo de apuestas diarias alcanzado ({MAX_DAILY_BETS})"

        if self.state["loss_today"] >= DAILY_STOP_LOSS:
            return False, f"Stop-loss diario activado: perdida ${self.loss_today:.2f} >= ${DAILY_STOP_LOSS}"

        return True, "OK"

    def record_bet(self, signal: dict, dry_run: bool = True) -> dict:
        """
        Registra una apuesta (simulada o real).
        Retorna el registro de la apuesta.
        """
        self.state = _reset_daily_if_needed(self.state)

        bet = {
            "timestamp":    datetime.now().isoformat(),
            "dry_run":      dry_run,
            "market_id":    signal["market_id"],
            "question":     signal["question"],
            "city":         signal["city"],
            "side":         signal["best_side"],
            "bet_size":     BET_SIZE,
            "price":        signal["price_yes"] if signal["best_side"] == "YES" else signal["price_no"],
            "edge":         signal["best_edge"],
            "prob_win":     signal["prob_win"],
            "confidence":   signal["confidence"],
            "copy_aligned": signal["copy_aligned"],
            "end_date":     signal["end_date"].isoformat() if signal.get("end_date") else None,
            "days_to_resolve": signal.get("days_to_resolve"),
            "status":       "PENDING",
            "pnl":          None,
        }

        # Descontar del balance
        self.state["balance"]    -= BET_SIZE
        self.state["bets_today"] += 1
        self.state["total_bets"] += 1
        self.state["history"].append(bet)

        _save_state(self.state)
        return bet

    def record_result(self, market_id: str, won: bool):
        """
        Actualiza el resultado de una apuesta cuando el mercado resuelve.
        won=True: ganamos
        won=False: perdimos
        """
        for bet in reversed(self.state["history"]):
            if bet["market_id"] == market_id and bet["status"] == "PENDING":
                price = bet["price"]
                if won:
                    payout = BET_SIZE / price  # ganamos el payout completo
                    pnl    = payout - BET_SIZE
                    self.state["balance"]    += payout
                    self.state["total_won"]  += 1
                    bet["status"] = "WON"
                else:
                    pnl = -BET_SIZE
                    self.state["loss_today"] += BET_SIZE
                    bet["status"] = "LOST"

                bet["pnl"] = round(pnl, 4)
                self.state["total_pnl"] += pnl
                _save_state(self.state)
                return pnl

        return None

    def summary(self) -> dict:
        return {
            "balance":        self.balance,
            "initial":        self.state["initial"],
            "pnl_total":      self.total_pnl,
            "pnl_pct":        round((self.balance - self.state["initial"]) / self.state["initial"] * 100, 2),
            "bets_today":     self.bets_today,
            "max_daily":      MAX_DAILY_BETS,
            "loss_today":     self.loss_today,
            "stop_loss":      DAILY_STOP_LOSS,
            "total_bets":     self.total_bets,
            "total_won":      self.state["total_won"],
            "win_rate":       self.win_rate,
            "bet_size":       BET_SIZE,
        }

    def get_history(self, last_n: int = 20) -> list[dict]:
        return self.state["history"][-last_n:]


# ─── Risk Manager para TEMPERATURA (balance $100, apuesta Kelly-sized) ────────

WEATHER_STATE_FILE = os.path.join(os.path.dirname(__file__), "logs", "weather_state.json")


def _load_weather_state() -> dict:
    if os.path.exists(WEATHER_STATE_FILE):
        try:
            with open(WEATHER_STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "balance":    WEATHER_INITIAL_BALANCE,
        "initial":    WEATHER_INITIAL_BALANCE,
        "bets_today": 0,
        "loss_today": 0.0,
        "total_bets": 0,
        "total_won":  0,
        "total_pnl":  0.0,
        "last_reset": date.today().isoformat(),
        "history":    [],
    }


def _save_weather_state(state: dict):
    os.makedirs(os.path.dirname(WEATHER_STATE_FILE), exist_ok=True)
    with open(WEATHER_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


class WeatherRiskManager:
    """Risk manager for the temperature bot. Uses WEATHER_ config and weather_state.json."""

    def __init__(self):
        self.state = _load_weather_state()
        self.state = _reset_daily_if_needed(self.state)
        _save_weather_state(self.state)

    @property
    def balance(self) -> float:
        return round(self.state["balance"], 4)

    @property
    def bets_today(self) -> int:
        return self.state["bets_today"]

    @property
    def loss_today(self) -> float:
        return round(self.state["loss_today"], 4)

    @property
    def total_bets(self) -> int:
        return self.state["total_bets"]

    @property
    def win_rate(self) -> float:
        if self.state["total_bets"] == 0:
            return 0.0
        return round(self.state["total_won"] / self.state["total_bets"], 4)

    @property
    def total_pnl(self) -> float:
        return round(self.state["total_pnl"], 4)

    def already_bet(self, market_id: str) -> bool:
        return any(
            b["market_id"] == market_id and b["status"] == "PENDING"
            for b in self.state["history"]
        )

    def can_bet(self) -> tuple[bool, str]:
        self.state = _reset_daily_if_needed(self.state)
        if self.state["balance"] < WEATHER_BET_SIZE:
            return False, f"Balance insuficiente: ${self.balance:.2f}"
        if self.state["bets_today"] >= WEATHER_MAX_DAILY_BETS:
            return False, f"Máximo apuestas diarias alcanzado ({WEATHER_MAX_DAILY_BETS})"
        if self.state["loss_today"] >= WEATHER_DAILY_STOP_LOSS:
            return False, f"Stop-loss diario activado: pérdida ${self.loss_today:.2f} >= ${WEATHER_DAILY_STOP_LOSS}"
        return True, "OK"

    def record_bet(self, signal: dict, dry_run: bool = True, bet_size: float = None) -> dict:
        self.state = _reset_daily_if_needed(self.state)
        if bet_size is None:
            bet_size = WEATHER_BET_SIZE
        price = signal["price_yes"] if signal["best_side"] == "YES" else signal["price_no"]
        bet = {
            "timestamp":       datetime.now().isoformat(),
            "dry_run":         dry_run,
            "market_id":       signal["market_id"],
            "question":        signal["question"],
            "city":            signal["city"],
            "unit":            signal.get("unit", "C"),
            "temp_threshold":  signal.get("temp_threshold"),
            "condition":       signal.get("condition"),
            "side":            signal["best_side"],
            "bet_size":        round(bet_size, 4),
            "price":           price,
            "edge":            signal["best_edge"],
            "ev":              signal.get("ev", 0),
            "kelly_frac":      signal.get("kelly_frac", 0),
            "prob_win":        signal.get("prob_win", 0),
            "confidence":      signal["confidence"],
            "primary_temp":    signal.get("primary_temp"),
            "primary_source":  signal.get("primary_source"),
            "copy_aligned":    signal["copy_aligned"],
            "token_id_yes":    signal.get("token_id_yes"),
            "token_id_no":     signal.get("token_id_no"),
            "end_date":        signal["end_date"].isoformat() if signal.get("end_date") else None,
            "days_to_resolve": signal.get("days_to_resolve"),
            "status":          "PENDING",
            "pnl":             None,
            "close_reason":    None,
        }
        self.state["balance"]    -= bet_size
        self.state["bets_today"] += 1
        self.state["total_bets"] += 1
        self.state["history"].append(bet)
        _save_weather_state(self.state)
        return bet

    def record_result(self, market_id: str, won: bool, close_reason: str = "resolved") -> float | None:
        for bet in reversed(self.state["history"]):
            if bet["market_id"] == market_id and bet["status"] == "PENDING":
                bet_size = bet["bet_size"]
                price    = bet["price"]
                if won:
                    payout = bet_size / price
                    pnl    = payout - bet_size
                    self.state["balance"]   += payout
                    self.state["total_won"] += 1
                    bet["status"] = "WON"
                else:
                    pnl = -bet_size
                    self.state["loss_today"] += bet_size
                    bet["status"] = "LOST"
                bet["pnl"]          = round(pnl, 4)
                bet["close_reason"] = close_reason
                self.state["total_pnl"] += pnl
                _save_weather_state(self.state)
                return pnl
        return None

    def summary(self) -> dict:
        return {
            "balance":    self.balance,
            "initial":    self.state["initial"],
            "pnl_total":  self.total_pnl,
            "pnl_pct":    round((self.balance - self.state["initial"]) / self.state["initial"] * 100, 2),
            "bets_today": self.bets_today,
            "max_daily":  WEATHER_MAX_DAILY_BETS,
            "loss_today": self.loss_today,
            "stop_loss":  WEATHER_DAILY_STOP_LOSS,
            "total_bets": self.total_bets,
            "total_won":  self.state["total_won"],
            "win_rate":   self.win_rate,
            "bet_size":   WEATHER_BET_SIZE,
        }

    def get_history(self, last_n: int = 20) -> list[dict]:
        return self.state["history"][-last_n:]


# ─── Risk Manager para CRYPTO (balance $200, apuesta $5) ─────────────────────

def _load_crypto_state() -> dict:
    if os.path.exists(CRYPTO_STATE_FILE):
        try:
            with open(CRYPTO_STATE_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "balance":    CRYPTO_INITIAL_BALANCE,
        "initial":    CRYPTO_INITIAL_BALANCE,
        "bets_today": 0,
        "loss_today": 0.0,
        "total_bets": 0,
        "total_won":  0,
        "total_pnl":  0.0,
        "last_reset": date.today().isoformat(),
        "history":    [],
    }


def _save_crypto_state(state: dict):
    os.makedirs(os.path.dirname(CRYPTO_STATE_FILE), exist_ok=True)
    with open(CRYPTO_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


class CryptoRiskManager:
    """Mismo comportamiento que RiskManager pero con config de crypto ($200 / $5)."""

    def __init__(self):
        self.state = _load_crypto_state()
        self.state = _reset_daily_if_needed(self.state)
        _save_crypto_state(self.state)

    @property
    def balance(self) -> float:
        return round(self.state["balance"], 4)

    @property
    def bets_today(self) -> int:
        return self.state["bets_today"]

    @property
    def loss_today(self) -> float:
        return round(self.state["loss_today"], 4)

    @property
    def total_bets(self) -> int:
        return self.state["total_bets"]

    @property
    def win_rate(self) -> float:
        if self.state["total_bets"] == 0:
            return 0.0
        return round(self.state["total_won"] / self.state["total_bets"], 4)

    @property
    def total_pnl(self) -> float:
        return round(self.state["total_pnl"], 4)

    def already_bet(self, market_id: str) -> bool:
        return any(
            b["market_id"] == market_id and b["status"] == "PENDING"
            for b in self.state["history"]
        )

    def can_bet(self) -> tuple[bool, str]:
        self.state = _reset_daily_if_needed(self.state)
        if self.state["balance"] < CRYPTO_BET_SIZE:
            return False, f"Balance insuficiente: ${self.balance:.2f} < ${CRYPTO_BET_SIZE}"
        if self.state["bets_today"] >= CRYPTO_MAX_DAILY_BETS:
            return False, f"Máximo de apuestas diarias alcanzado ({CRYPTO_MAX_DAILY_BETS})"
        if self.state["loss_today"] >= CRYPTO_DAILY_STOP_LOSS:
            return False, f"Stop-loss diario activado: pérdida ${self.loss_today:.2f} >= ${CRYPTO_DAILY_STOP_LOSS}"
        return True, "OK"

    def record_bet(self, signal: dict, dry_run: bool = True) -> dict:
        self.state = _reset_daily_if_needed(self.state)
        price = signal["price_yes"] if signal["best_side"] == "YES" else signal["price_no"]
        bet = {
            "timestamp":      datetime.now().isoformat(),
            "dry_run":        dry_run,
            "market_id":      signal["market_id"],
            "question":       signal["question"],
            "asset":          signal.get("asset", ""),
            "city":           signal.get("asset", ""),  # compatibilidad historial
            "side":           signal["best_side"],
            "bet_size":       CRYPTO_BET_SIZE,
            "price":          price,
            "edge":           signal["best_edge"],
            "prob_win":       signal["prob_win"],
            "confidence":     signal["confidence"],
            "copy_aligned":   signal["copy_aligned"],
            "end_date":       signal["end_date"].isoformat() if signal.get("end_date") else None,
            "days_to_resolve": signal.get("days_to_resolve"),
            "status":         "PENDING",
            "pnl":            None,
        }
        self.state["balance"]    -= CRYPTO_BET_SIZE
        self.state["bets_today"] += 1
        self.state["total_bets"] += 1
        self.state["history"].append(bet)
        _save_crypto_state(self.state)
        return bet

    def record_result(self, market_id: str, won: bool):
        for bet in reversed(self.state["history"]):
            if bet["market_id"] == market_id and bet["status"] == "PENDING":
                if won:
                    payout = CRYPTO_BET_SIZE / bet["price"]
                    pnl    = payout - CRYPTO_BET_SIZE
                    self.state["balance"]   += payout
                    self.state["total_won"] += 1
                    bet["status"] = "WON"
                else:
                    pnl = -CRYPTO_BET_SIZE
                    self.state["loss_today"] += CRYPTO_BET_SIZE
                    bet["status"] = "LOST"
                bet["pnl"] = round(pnl, 4)
                self.state["total_pnl"] += pnl
                _save_crypto_state(self.state)
                return pnl
        return None

    def summary(self) -> dict:
        return {
            "balance":    self.balance,
            "initial":    self.state["initial"],
            "pnl_total":  self.total_pnl,
            "pnl_pct":    round((self.balance - self.state["initial"]) / self.state["initial"] * 100, 2),
            "bets_today": self.bets_today,
            "max_daily":  CRYPTO_MAX_DAILY_BETS,
            "loss_today": self.loss_today,
            "stop_loss":  CRYPTO_DAILY_STOP_LOSS,
            "total_bets": self.total_bets,
            "total_won":  self.state["total_won"],
            "win_rate":   self.win_rate,
            "bet_size":   CRYPTO_BET_SIZE,
        }

    def get_history(self, last_n: int = 20) -> list[dict]:
        return self.state["history"][-last_n:]
