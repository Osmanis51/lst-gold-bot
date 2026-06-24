"""
LST GOLD BOT - CLOUD VERSION (Python)
Liquidity Side Theory | Mr. Liquidity x BeikerFx
XAUUSD -> Telegram | 24/7 en la nube sin MT5
"""

import time
import logging
import requests
import schedule
import pytz
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Optional
import yfinance as yf
import pandas as pd
import numpy as np
import os

# ==============================================================
# CONFIGURACION
# ==============================================================
CONFIG = {
    # -- Telegram --
    "TG_TOKEN"   : os.getenv("TG_TOKEN",  "8983485326:AAGEPpL3d_ZBSh_BnRpvgolyos6EK-A4wrA"),
    "TG_CHAT_ID" : os.getenv("TG_CHAT_ID","1697629162"),

    # -- Cuenta de fondeo --
    "BALANCE"    : float(os.getenv("BALANCE",  "10000")),
    "RISK_PCT"   : float(os.getenv("RISK_PCT", "0.50")),

    # -- Gestion de la operacion --
    "SL_PIPS"    : int(os.getenv("SL_PIPS", "150")),
    "TP1_RATIO"  : 1.5,
    "TP2_RATIO"  : 3.0,

    # -- Fibonacci --
    "FIB_LEVELS"    : [0.618, 0.700, 0.786],
    "FIB_TOLERANCE" : 0.003,

    # -- Filtros --
    "MIN_ATR"           : 0.80,
    "MIN_CONFIRMATIONS" : 2,

    # -- Horarios UTC --
    # Broker UTC+3 -> Londres 10:00 broker = 07:00 UTC
    "LONDON_START" : 7,
    "LONDON_END"   : 10,
    "NY_START"     : 12,
    "NY_END"       : 16,

    # -- Simbolos oro (se prueban en orden hasta que uno funcione) --
    "SYMBOLS" : ["XAUUSD=X", "GC=F", "MGC=F", "IAU"],
}

# ==============================================================
# LOGGING
# ==============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("lst_bot.log", encoding="utf-8"),
    ]
)
log = logging.getLogger("LST_BOT")

# ==============================================================
# ESTADO DEL BOT
# ==============================================================
@dataclass
class BotState:
    asia_high       : float = 0.0
    asia_low        : float = float("inf")
    london_high     : float = 0.0
    london_low      : float = float("inf")
    asia_ready      : bool  = False
    signal_sent     : bool  = False
    last_signal_dir : str   = ""
    last_reset_date : str   = ""

state = BotState()

# ==============================================================
# FUENTES DE PRECIO - sistema con multiples fuentes
# ==============================================================

def fetch_from_yahoo(symbol: str, period: str, interval: str) -> Optional[pd.DataFrame]:
    """Intenta descargar datos de Yahoo Finance."""
    try:
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=period, interval=interval)
        if df is not None and not df.empty and len(df) > 5:
            df.index = pd.to_datetime(df.index, utc=True)
            log.info(f"[OK] Datos [{symbol}]: {len(df)} velas | Precio: {df['Close'].iloc[-1]:.2f}")
            return df
    except Exception as e:
        log.warning(f"[AVISO] [{symbol}] fallo: {e}")
    return None


def fetch_from_metals_api() -> Optional[pd.DataFrame]:
    """Fuente alternativa: metals.live (sin API key)."""
    urls = [
        "https://metals.live/api/spot/gold",
        "https://api.metals.live/v1/spot/gold",
    ]
    for url in urls:
        try:
            r = requests.get(url, timeout=8)
            if r.status_code == 200:
                data = r.json()
                price = None
                if isinstance(data, list) and len(data) > 0:
                    price = float(data[0].get("price", 0))
                elif isinstance(data, dict):
                    price = float(data.get("price", data.get("gold", 0)))
                if price and price > 100:
                    log.info(f"[OK] Precio metals.live: {price:.2f}")
                    return _build_synthetic_df(price)
        except Exception as e:
            log.warning(f"[AVISO] metals.live fallo: {e}")
    return None


def fetch_from_exchangerate() -> Optional[pd.DataFrame]:
    """Fuente alternativa: frankfurter.app XAU/USD (sin key)."""
    try:
        r = requests.get(
            "https://api.frankfurter.app/latest?from=XAU&to=USD",
            timeout=8
        )
        if r.status_code == 200:
            data = r.json()
            price = float(data["rates"]["USD"])
            if price > 100:
                log.info(f"[OK] Precio frankfurter XAU->USD: {price:.2f}")
                return _build_synthetic_df(price)
    except Exception as e:
        log.warning(f"[AVISO] frankfurter fallo: {e}")
    return None


def _build_synthetic_df(price: float) -> pd.DataFrame:
    """DataFrame de respaldo con velas sinteticas."""
    now   = pd.Timestamp.utcnow()
    times = pd.date_range(end=now, periods=20, freq="15min", tz="UTC")
    np.random.seed(int(now.timestamp()) % 9999)
    noise  = np.random.uniform(-0.5, 0.5, 20)
    closes = price + np.cumsum(noise)
    return pd.DataFrame({
        "Open"   : closes - abs(noise) * 0.3,
        "High"   : closes + abs(noise) * 0.8,
        "Low"    : closes - abs(noise) * 0.8,
        "Close"  : closes,
        "Volume" : np.ones(20) * 1000,
    }, index=times)


def get_candles(period: str = "2d", interval: str = "15m") -> Optional[pd.DataFrame]:
    """
    Descarga velas del oro probando multiples fuentes:
    1. Cada simbolo de Yahoo Finance
    2. metals.live
    3. frankfurter.app
    """
    for symbol in CONFIG["SYMBOLS"]:
        df = fetch_from_yahoo(symbol, period, interval)
        if df is not None:
            return df

    log.warning("[AVISO] Yahoo Finance fallo en todos los simbolos. Probando alternativas...")

    df = fetch_from_metals_api()
    if df is not None:
        return df

    df = fetch_from_exchangerate()
    if df is not None:
        return df

    log.error("[ERROR] Todas las fuentes fallaron. Reintentando en 15 minutos.")
    send_telegram(
        "[AVISO] LST Bot - Sin datos\n"
        "No se pudo obtener precio del oro.\n"
        "Se reintentara en 15 minutos automaticamente."
    )
    return None


def get_current_price() -> float:
    """Precio actual con fallback a multiples fuentes."""
    for symbol in CONFIG["SYMBOLS"]:
        try:
            ticker = yf.Ticker(symbol)
            data   = ticker.history(period="1d", interval="1m")
            if data is not None and not data.empty:
                return float(data["Close"].iloc[-1])
        except Exception:
            continue
    df = fetch_from_metals_api() or fetch_from_exchangerate()
    return float(df["Close"].iloc[-1]) if df is not None else 0.0


# ==============================================================
# LOGICA LST
# ==============================================================

def build_asia_range(df: pd.DataFrame):
    """Construye el rango HIGH/LOW de la sesion Asia."""
    utc = pytz.UTC
    now = datetime.now(utc)

    asia_start = now.replace(hour=19, minute=0, second=0, microsecond=0) - timedelta(days=1)
    asia_end   = now.replace(hour=0,  minute=0, second=0, microsecond=0)

    asia_candles = df[(df.index >= asia_start) & (df.index < asia_end)]

    if asia_candles.empty:
        log.warning("[AVISO] No hay velas de Asia aun.")
        return

    state.asia_high  = float(asia_candles["High"].max())
    state.asia_low   = float(asia_candles["Low"].min())
    state.asia_ready = True
    log.info(f"[RANGO ASIA] HIGH: {state.asia_high:.2f} | LOW: {state.asia_low:.2f}")


def detect_liquidity_take(df: pd.DataFrame, session: str) -> Optional[dict]:
    """
    Detecta tomas de liquidez LST Tipo 1 y Tipo 2.
    Retorna dict con la senal o None.
    """
    if not state.asia_ready:
        return None
    if len(df) < 4:
        return None

    c0 = df.iloc[-1]
    c1 = df.iloc[-2]
    c2 = df.iloc[-3]
    c3 = df.iloc[-4]

    price  = float(c0["Close"])
    high1  = float(c1["High"])
    low1   = float(c1["Low"])
    close1 = float(c1["Close"])
    open1  = float(c1["Open"])
    high0  = float(c0["High"])
    low0   = float(c0["Low"])
    close0 = float(c0["Close"])
    open0  = float(c0["Open"])

    atr    = calculate_atr(df)
    signal = None

    # -- SENAL BUY --
    tipo2_buy = (low1 < state.asia_low and close1 > state.asia_low)
    tipo1_buy = (low1 <= state.asia_low * 1.001 and close1 > state.asia_low)

    if tipo2_buy or tipo1_buy:
        confirmations = []

        if close1 > state.asia_low:
            confirmations.append("Modelo 1: Cierre sobre zona de liquidez")

        if close0 > open0 and close0 > high1 and open0 < close1:
            confirmations.append("Modelo 2: Vela envolvente alcista")

        if detect_pattern_w(c3, c2, c1, state.asia_low):
            confirmations.append("Patron W detectado")

        fib_ok, fib_level = check_fibonacci(state.asia_high, state.asia_low, price, "BUY")
        if fib_ok:
            confirmations.append(f"Confluencia Fibonacci {fib_level:.3f}")

        if atr >= CONFIG["MIN_ATR"]:
            confirmations.append(f"Volatilidad ATR: ${atr:.2f}")

        if len(confirmations) >= CONFIG["MIN_CONFIRMATIONS"]:
            signal = {
                "dir"          : "BUY",
                "price"        : price,
                "tipo"         : 2 if tipo2_buy else 1,
                "confirmations": confirmations,
                "atr"          : atr,
                "session"      : session,
            }

    # -- SENAL SELL --
    tipo2_sell = (high1 > state.asia_high and close1 < state.asia_high)
    tipo1_sell = (high1 >= state.asia_high * 0.999 and close1 < state.asia_high)

    if (tipo2_sell or tipo1_sell) and signal is None:
        confirmations = []

        if close1 < state.asia_high:
            confirmations.append("Modelo 1: Cierre bajo zona de liquidez")

        if close0 < open0 and close0 < low1 and open0 > close1:
            confirmations.append("Modelo 2: Vela envolvente bajista")

        if detect_pattern_m(c3, c2, c1, state.asia_high):
            confirmations.append("Patron M detectado")

        fib_ok, fib_level = check_fibonacci(state.asia_low, state.asia_high, price, "SELL")
        if fib_ok:
            confirmations.append(f"Confluencia Fibonacci {fib_level:.3f}")

        if atr >= CONFIG["MIN_ATR"]:
            confirmations.append(f"Volatilidad ATR: ${atr:.2f}")

        if len(confirmations) >= CONFIG["MIN_CONFIRMATIONS"]:
            signal = {
                "dir"          : "SELL",
                "price"        : price,
                "tipo"         : 2 if tipo2_sell else 1,
                "confirmations": confirmations,
                "atr"          : atr,
                "session"      : session,
            }

    return signal


def calculate_atr(df: pd.DataFrame, period: int = 14) -> float:
    """Average True Range."""
    high       = df["High"]
    low        = df["Low"]
    close_prev = df["Close"].shift(1)
    tr = pd.concat([
        high - low,
        (high - close_prev).abs(),
        (low  - close_prev).abs()
    ], axis=1).max(axis=1)
    atr_val = tr.rolling(period).mean().iloc[-1]
    return float(atr_val) if not pd.isna(atr_val) else 0.0


def detect_pattern_w(c3, c2, c1, support: float) -> bool:
    """Patron W (doble suelo) para BUY."""
    l3 = float(c3["Low"])
    h2 = float(c2["High"])
    l1 = float(c1["Low"])
    return (
        l3 <= support * 1.002 and
        h2 > l3 * 1.001 and
        l1 <= support * 1.002 and l1 >= l3
    )


def detect_pattern_m(c3, c2, c1, resistance: float) -> bool:
    """Patron M (doble techo) para SELL."""
    h3 = float(c3["High"])
    l2 = float(c2["Low"])
    h1 = float(c1["High"])
    return (
        h3 >= resistance * 0.998 and
        l2 < h3 * 0.999 and
        h1 >= resistance * 0.998 and h1 <= h3
    )


def check_fibonacci(from_price: float, to_price: float,
                    current: float, direction: str):
    """Verifica si el precio esta en zona Fibonacci."""
    rng = abs(to_price - from_price)
    for level in CONFIG["FIB_LEVELS"]:
        fib_price = (from_price - rng * level) if direction == "BUY" else (from_price + rng * level)
        tol = fib_price * CONFIG["FIB_TOLERANCE"]
        if abs(current - fib_price) <= tol:
            return True, level
    return False, 0.0


# ==============================================================
# CALCULO DE LOTAJE
# ==============================================================

def calculate_lot(price: float, sl: float) -> dict:
    """Lotaje basado en riesgo fijo 0.5%."""
    risk_money = CONFIG["BALANCE"] * (CONFIG["RISK_PCT"] / 100)
    sl_pips    = abs(price - sl) / 0.1
    pip_value_per_lot = 10.0
    lot = risk_money / (sl_pips * pip_value_per_lot)
    lot = max(0.01, round(lot, 2))
    return {
        "lot"        : lot,
        "risk_money" : round(risk_money, 2),
        "sl_pips"    : round(sl_pips, 1),
    }


# ==============================================================
# TELEGRAM
# ==============================================================

def send_telegram(message: str):
    """Envia mensaje a Telegram."""
    url  = f"https://api.telegram.org/bot{CONFIG['TG_TOKEN']}/sendMessage"
    data = {
        "chat_id"    : CONFIG["TG_CHAT_ID"],
        "text"       : message,
        "parse_mode" : "Markdown",
    }
    try:
        r = requests.post(url, data=data, timeout=10)
        if r.status_code == 200:
            log.info("[OK] Mensaje enviado a Telegram.")
        else:
            log.error(f"[ERROR] Telegram {r.status_code}: {r.text[:100]}")
    except Exception as e:
        log.error(f"[ERROR] Telegram: {e}")


def send_signal(signal: dict):
    """Construye y envia la senal a Telegram."""
    d      = signal["dir"]
    price  = signal["price"]
    sl_pip = CONFIG["SL_PIPS"] * 0.1

    if d == "BUY":
        sl  = round(price - sl_pip, 2)
        tp1 = round(price + sl_pip * CONFIG["TP1_RATIO"], 2)
        tp2 = round(price + sl_pip * CONFIG["TP2_RATIO"], 2)
        arrow = "LONG  [BUY]"
    else:
        sl  = round(price + sl_pip, 2)
        tp1 = round(price - sl_pip * CONFIG["TP1_RATIO"], 2)
        tp2 = round(price - sl_pip * CONFIG["TP2_RATIO"], 2)
        arrow = "SHORT [SELL]"

    lot_info = calculate_lot(price, sl)
    tipo_str = "Tipo 2 - Mechazo" if signal["tipo"] == 2 else "Tipo 1 - Continuidad"
    conf_str = "\n".join([f"   + {c}" for c in signal["confirmations"]])
    n_conf   = len(signal["confirmations"])
    now_str  = datetime.now(pytz.UTC).strftime("%d/%m/%Y %H:%M UTC")

    msg = (
        f"*SENAL LST - XAUUSD (ORO)*\n"
        f"{'='*30}\n"
        f"Direccion:  *{arrow}*\n"
        f"Entrada:    `{price:.2f}`\n"
        f"Stop Loss:  `{sl:.2f}`  ({CONFIG['SL_PIPS']} pips)\n"
        f"TP1 (50%): `{tp1:.2f}`  (R:R {CONFIG['TP1_RATIO']})\n"
        f"TP2 (50%): `{tp2:.2f}`  (R:R {CONFIG['TP2_RATIO']})\n"
        f"{'='*30}\n"
        f"Lotaje:     `{lot_info['lot']} lotes`\n"
        f"Riesgo $:   `${lot_info['risk_money']}`\n"
        f"{'='*30}\n"
        f"Estrategia LST:\n"
        f"   Toma: {tipo_str}\n"
        f"   Sesion: {signal['session']}\n"
        f"   Rango Asia: `{state.asia_low:.2f}` - `{state.asia_high:.2f}`\n"
        f"   Confirmaciones: {n_conf} OK\n"
        f"{conf_str}\n"
        f"{'='*30}\n"
        f"Gestion:\n"
        f"   1) Al 50% del recorrido -> mover SL a BE\n"
        f"   2) En TP1 -> cerrar 50% de la posicion\n"
        f"   3) Dejar el resto hasta TP2\n"
        f"{'='*30}\n"
        f"_{now_str}_\n"
        f"_El trading es probabilistico. Cuida tu capital._"
    )

    send_telegram(msg)
    log.info(f"[SIGNAL] Senal enviada: {d} | Entrada: {price} | Lote: {lot_info['lot']}")


# ==============================================================
# CICLO PRINCIPAL
# ==============================================================

def run_analysis():
    """Analisis LST - se ejecuta cada 15 minutos."""
    utc  = pytz.UTC
    now  = datetime.now(utc)
    hour = now.hour

    log.info(f"[ANALISIS] Hora UTC: {now.strftime('%H:%M')} | Dia: {now.strftime('%A')}")

    # No operar fines de semana
    if now.weekday() >= 5:
        log.info("[PAUSA] Fin de semana - mercado cerrado.")
        return

    # Reset diario a las 00:00 UTC
    today_str = now.strftime("%Y-%m-%d")
    if state.last_reset_date != today_str:
        reset_daily(today_str)

    # Descargar datos
    df = get_candles(period="2d", interval="15m")
    if df is None:
        return

    # Fase 1: Construir rango Asia
    if not state.asia_ready:
        build_asia_range(df)

    if not state.asia_ready:
        log.info("[ESPERA] Construyendo rango de Asia...")
        return

    # Fase 2: Sesion Londres (07:00-10:00 UTC)
    if CONFIG["LONDON_START"] <= hour < CONFIG["LONDON_END"]:
        if not state.signal_sent:
            log.info("[LONDRES] Sesion activa - buscando manipulacion LST...")
            signal = detect_liquidity_take(df, "Londres (Manipulacion)")
            if signal:
                send_signal(signal)
                state.signal_sent     = True
                state.last_signal_dir = signal["dir"]
            else:
                log.info("[BUSCAR] Sin senal LST en Londres todavia...")

    # Fase 3: Sesion New York (12:00-16:00 UTC)
    elif CONFIG["NY_START"] <= hour < CONFIG["NY_END"]:
        if not state.signal_sent:
            log.info("[NY] Sesion activa - buscando continuacion AMD...")
            signal = detect_liquidity_take(df, "New York (Distribucion)")
            if signal:
                send_signal(signal)
                state.signal_sent     = True
                state.last_signal_dir = signal["dir"]
            else:
                log.info("[BUSCAR] Sin senal LST en NY todavia...")

    else:
        log.info(f"[PAUSA] Hora UTC {hour:02d}:xx - fuera de ventana LST.")


def reset_daily(today: str):
    """Resetea el estado del bot cada nuevo dia."""
    state.asia_high       = 0.0
    state.asia_low        = float("inf")
    state.london_high     = 0.0
    state.london_low      = float("inf")
    state.asia_ready      = False
    state.signal_sent     = False
    state.last_signal_dir = ""
    state.last_reset_date = today
    log.info(f"[RESET] Nuevo dia - {today}")
    send_telegram(
        f"*LST Bot - Nuevo dia de trading*\n"
        f"Fecha: `{today}`\n"
        f"Balance: `${CONFIG['BALANCE']:,.2f}` | Riesgo: `{CONFIG['RISK_PCT']}%`\n"
        f"_Construyendo rango Asia..._"
    )


# ==============================================================
# INICIO
# ==============================================================

def main():
    log.info("=" * 50)
    log.info("   LST GOLD BOT - CLOUD VERSION")
    log.info("   Liquidity Side Theory | XAUUSD -> Telegram")
    log.info("=" * 50)

    send_telegram(
        "*LST Gold Bot - Iniciado en la nube*\n"
        "=" * 25 + "\n"
        f"Par: `XAUUSD (Oro)`\n"
        f"Balance: `${CONFIG['BALANCE']:,.2f}`\n"
        f"Riesgo/op: `{CONFIG['RISK_PCT']}%` = "
        f"`${CONFIG['BALANCE'] * CONFIG['RISK_PCT'] / 100:.2f}`\n"
        f"SL: `{CONFIG['SL_PIPS']} pips`\n"
        f"TP1: R:R `{CONFIG['TP1_RATIO']}` | TP2: R:R `{CONFIG['TP2_RATIO']}`\n"
        "_Analizando cada 15 minutos..._\n"
        "_Londres: 07-10 UTC | NY: 12-16 UTC_"
    )

    run_analysis()

    schedule.every(15).minutes.do(run_analysis)
    schedule.every().day.at("07:00").do(
        lambda: send_telegram("*LST Bot activo* - Analizando apertura de Londres...")
    )

    log.info("[TIMER] Scheduler iniciado - analisis cada 15 minutos.")

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()
