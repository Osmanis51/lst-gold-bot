"""
LST GOLD BOT - CLOUD VERSION 3.0
Liquidity Side Theory | Mr. Liquidity x BeikerFx
XAUUSD -> Telegram | 24/7 en Railway
"""

import time
import logging
import requests
import schedule
import pytz
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Optional
import pandas as pd
import numpy as np
import os

# ==============================================================
# CONFIGURACION
# Los valores vienen de las variables de Railway, NO van aqui
# ==============================================================
CONFIG = {
    "TG_TOKEN"       : os.getenv("TG_TOKEN",       ""),
    "TG_CHAT_ID"     : os.getenv("TG_CHAT_ID",     ""),
    "TWELVE_API_KEY" : os.getenv("TWELVE_API_KEY", ""),
    "BALANCE"        : float(os.getenv("BALANCE",  "10000")),
    "RISK_PCT"       : float(os.getenv("RISK_PCT", "0.50")),
    "SL_PIPS"        : int(os.getenv("SL_PIPS",   "150")),
    "TP1_RATIO"      : 1.5,
    "TP2_RATIO"      : 3.0,
    "FIB_LEVELS"     : [0.618, 0.700, 0.786],
    "FIB_TOLERANCE"  : 0.003,
    "MIN_ATR"        : 0.50,
    "MIN_CONFIRMATIONS" : 2,
    "LONDON_START"   : 7,
    "LONDON_END"     : 10,
    "NY_START"       : 12,
    "NY_END"         : 16,
}

# ==============================================================
# LOGGING
# ==============================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("LST_BOT")

# ==============================================================
# ESTADO
# ==============================================================
@dataclass
class BotState:
    asia_high       : float = 0.0
    asia_low        : float = float("inf")
    asia_ready      : bool  = False
    last_signal_dir : str   = ""
    last_reset_date : str   = ""
    signal_count    : int   = 0

state = BotState()

# ==============================================================
# TELEGRAM
# ==============================================================

def send_telegram(msg: str):
    token   = CONFIG["TG_TOKEN"]
    chat_id = CONFIG["TG_CHAT_ID"]

    if not token or not chat_id:
        log.error("[ERROR] TG_TOKEN o TG_CHAT_ID vacios en Railway Variables.")
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = requests.post(url, json={
            "chat_id"    : chat_id,
            "text"       : msg,
            "parse_mode" : "Markdown",
        }, timeout=10)

        if r.status_code == 200:
            log.info("[OK] Telegram: mensaje enviado.")
        elif r.status_code == 404:
            log.error("[ERROR] Telegram 404: TG_TOKEN incorrecto en Railway Variables.")
        elif r.status_code == 400:
            log.error("[ERROR] Telegram 400: TG_CHAT_ID incorrecto en Railway Variables.")
        else:
            log.error(f"[ERROR] Telegram {r.status_code}: {r.text[:150]}")
    except Exception as e:
        log.error(f"[ERROR] Telegram: {e}")

# ==============================================================
# FUENTES DE PRECIO
# ==============================================================

def fetch_twelvedata() -> Optional[pd.DataFrame]:
    key = CONFIG["TWELVE_API_KEY"]
    if not key:
        log.warning("[AVISO] TWELVE_API_KEY vacia en Railway Variables.")
        return None

    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol"     : "XAU/USD",
        "interval"   : "15min",
        "outputsize" : 100,
        "apikey"     : key,
        "format"     : "JSON",
    }
    try:
        r    = requests.get(url, params=params, timeout=15)
        data = r.json()

        if data.get("status") == "error":
            log.error(f"[ERROR] Twelve Data: {data.get('message')}")
            return None

        values = data.get("values", [])
        if not values:
            log.error("[ERROR] Twelve Data: sin datos.")
            return None

        rows = [{
            "datetime" : pd.to_datetime(v["datetime"], utc=True),
            "Open"     : float(v["open"]),
            "High"     : float(v["high"]),
            "Low"      : float(v["low"]),
            "Close"    : float(v["close"]),
            "Volume"   : float(v.get("volume", 0)),
        } for v in values]

        df = pd.DataFrame(rows).set_index("datetime").sort_index()
        log.info(f"[OK] Twelve Data: {len(df)} velas | Precio: {df['Close'].iloc[-1]:.2f}")
        return df

    except Exception as e:
        log.error(f"[ERROR] Twelve Data: {e}")
        return None


def fetch_goldapi_price() -> Optional[float]:
    try:
        r = requests.get("https://api.gold-api.com/price/XAU", timeout=10)
        if r.status_code == 200:
            price = float(r.json().get("price", 0))
            if price > 100:
                log.info(f"[OK] gold-api.com: {price:.2f}")
                return price
    except Exception as e:
        log.warning(f"[AVISO] gold-api.com: {e}")
    return None


def build_df_from_price(price: float) -> pd.DataFrame:
    now   = pd.Timestamp.utcnow()
    times = pd.date_range(end=now, periods=50, freq="15min", tz="UTC")
    np.random.seed(int(now.timestamp()) % 9999)
    noise  = np.random.uniform(-0.3, 0.3, 50)
    closes = np.clip(price + np.cumsum(noise), price * 0.995, price * 1.005)
    return pd.DataFrame({
        "Open"   : closes - abs(noise) * 0.2,
        "High"   : closes + abs(noise) * 0.5,
        "Low"    : closes - abs(noise) * 0.5,
        "Close"  : closes,
        "Volume" : np.ones(50) * 1000,
    }, index=times)


def get_candles() -> Optional[pd.DataFrame]:
    df = fetch_twelvedata()
    if df is not None and len(df) > 10:
        return df

    log.warning("[AVISO] Twelve Data no disponible. Usando gold-api.com...")
    price = fetch_goldapi_price()
    if price:
        return build_df_from_price(price)

    log.error("[ERROR] Todas las fuentes fallaron.")
    return None

# ==============================================================
# LOGICA LST
# ==============================================================

def build_asia_range(df: pd.DataFrame):
    utc        = pytz.UTC
    now        = datetime.now(utc)
    asia_start = now.replace(hour=19, minute=0, second=0, microsecond=0) - timedelta(days=1)
    asia_end   = now.replace(hour=0,  minute=0, second=0, microsecond=0)

    asia = df[(df.index >= asia_start) & (df.index < asia_end)]

    if asia.empty:
        ref = df.iloc[-50:] if len(df) >= 50 else df
        state.asia_high = float(ref["High"].max())
        state.asia_low  = float(ref["Low"].min())
    else:
        state.asia_high = float(asia["High"].max())
        state.asia_low  = float(asia["Low"].min())

    state.asia_ready = True
    log.info(f"[ASIA] HIGH: {state.asia_high:.2f} | LOW: {state.asia_low:.2f}")


def calculate_atr(df: pd.DataFrame, period: int = 14) -> float:
    h  = df["High"]
    l  = df["Low"]
    cp = df["Close"].shift(1)
    tr = pd.concat([(h - l), (h - cp).abs(), (l - cp).abs()], axis=1).max(axis=1)
    v  = tr.rolling(period).mean().iloc[-1]
    return float(v) if not pd.isna(v) else 1.0


def detect_pattern_w(c3, c2, c1, support: float) -> bool:
    l3 = float(c3["Low"]); h2 = float(c2["High"]); l1 = float(c1["Low"])
    return l3 <= support * 1.002 and h2 > l3 * 1.001 and l1 <= support * 1.002 and l1 >= l3


def detect_pattern_m(c3, c2, c1, resistance: float) -> bool:
    h3 = float(c3["High"]); l2 = float(c2["Low"]); h1 = float(c1["High"])
    return h3 >= resistance * 0.998 and l2 < h3 * 0.999 and h1 >= resistance * 0.998 and h1 <= h3


def check_fibonacci(from_p: float, to_p: float, current: float, direction: str):
    rng = abs(to_p - from_p)
    for level in CONFIG["FIB_LEVELS"]:
        fp  = (from_p - rng * level) if direction == "BUY" else (from_p + rng * level)
        if abs(current - fp) <= fp * CONFIG["FIB_TOLERANCE"]:
            return True, level
    return False, 0.0


def detect_signal(df: pd.DataFrame, session: str) -> Optional[dict]:
    if not state.asia_ready or len(df) < 4:
        return None

    c0 = df.iloc[-1]; c1 = df.iloc[-2]
    c2 = df.iloc[-3]; c3 = df.iloc[-4]

    price  = float(c0["Close"])
    high1  = float(c1["High"]); low1   = float(c1["Low"])
    close1 = float(c1["Close"]); open1  = float(c1["Open"])
    close0 = float(c0["Close"]); open0  = float(c0["Open"])
    atr    = calculate_atr(df)
    signal = None

    # BUY
    tipo2_buy = low1  < state.asia_low   and close1 > state.asia_low
    tipo1_buy = low1 <= state.asia_low * 1.001 and close1 > state.asia_low

    if tipo2_buy or tipo1_buy:
        conf = []
        if close1 > state.asia_low:
            conf.append("Modelo 1: cierre sobre zona")
        if close0 > open0 and close0 > high1 and open0 < close1:
            conf.append("Modelo 2: vela envolvente alcista")
        if detect_pattern_w(c3, c2, c1, state.asia_low):
            conf.append("Patron W")
        fib_ok, fib_lvl = check_fibonacci(state.asia_high, state.asia_low, price, "BUY")
        if fib_ok:
            conf.append(f"Fibonacci {fib_lvl:.3f}")
        if atr >= CONFIG["MIN_ATR"]:
            conf.append(f"ATR: {atr:.2f}")
        if len(conf) >= CONFIG["MIN_CONFIRMATIONS"]:
            signal = {"dir": "BUY",  "price": price,
                      "tipo": 2 if tipo2_buy  else 1,
                      "conf": conf, "session": session}

    # SELL
    tipo2_sell = high1 > state.asia_high  and close1 < state.asia_high
    tipo1_sell = high1 >= state.asia_high * 0.999 and close1 < state.asia_high

    if (tipo2_sell or tipo1_sell) and signal is None:
        conf = []
        if close1 < state.asia_high:
            conf.append("Modelo 1: cierre bajo zona")
        if close0 < open0 and close0 < float(c1["Low"]) and open0 > close1:
            conf.append("Modelo 2: vela envolvente bajista")
        if detect_pattern_m(c3, c2, c1, state.asia_high):
            conf.append("Patron M")
        fib_ok, fib_lvl = check_fibonacci(state.asia_low, state.asia_high, price, "SELL")
        if fib_ok:
            conf.append(f"Fibonacci {fib_lvl:.3f}")
        if atr >= CONFIG["MIN_ATR"]:
            conf.append(f"ATR: {atr:.2f}")
        if len(conf) >= CONFIG["MIN_CONFIRMATIONS"]:
            signal = {"dir": "SELL", "price": price,
                      "tipo": 2 if tipo2_sell else 1,
                      "conf": conf, "session": session}

    return signal

# ==============================================================
# LOTAJE
# ==============================================================

def calculate_lot(price: float, sl: float) -> dict:
    risk_money = CONFIG["BALANCE"] * (CONFIG["RISK_PCT"] / 100)
    sl_pips    = abs(price - sl) / 0.1
    lot        = max(0.01, round(risk_money / (sl_pips * 10.0), 2))
    return {"lot": lot, "risk": round(risk_money, 2)}

# ==============================================================
# SENAL
# ==============================================================

def send_signal(signal: dict):
    d    = signal["dir"]
    price = signal["price"]
    sl_d  = CONFIG["SL_PIPS"] * 0.1

    if d == "BUY":
        sl  = round(price - sl_d, 2)
        tp1 = round(price + sl_d * CONFIG["TP1_RATIO"], 2)
        tp2 = round(price + sl_d * CONFIG["TP2_RATIO"], 2)
        dir_str = "LONG (BUY)"
    else:
        sl  = round(price + sl_d, 2)
        tp1 = round(price - sl_d * CONFIG["TP1_RATIO"], 2)
        tp2 = round(price - sl_d * CONFIG["TP2_RATIO"], 2)
        dir_str = "SHORT (SELL)"

    lot  = calculate_lot(price, sl)
    tipo = "Tipo 2 - Mechazo" if signal["tipo"] == 2 else "Tipo 1 - Continuidad"
    conf = "\n".join([f"   + {c}" for c in signal["conf"]])
    hora = datetime.now(pytz.UTC).strftime("%d/%m/%Y %H:%M UTC")

    msg = (
        f"*SENAL LST - XAUUSD (ORO)*\n"
        f"*Direccion:* {dir_str}\n"
        f"*Entrada:*   `{price:.2f}`\n"
        f"*Stop Loss:* `{sl:.2f}` ({CONFIG['SL_PIPS']} pips)\n"
        f"*TP1 50%:*  `{tp1:.2f}` (R:R {CONFIG['TP1_RATIO']})\n"
        f"*TP2 50%:*  `{tp2:.2f}` (R:R {CONFIG['TP2_RATIO']})\n"
        f"*Lotaje:*    `{lot['lot']} lotes`\n"
        f"*Riesgo:*    `${lot['risk']}`\n"
        f"*Toma:* {tipo}\n"
        f"*Sesion:* {signal['session']}\n"
        f"*Asia:* `{state.asia_low:.2f}` - `{state.asia_high:.2f}`\n"
        f"{conf}\n"
        f"1) Al 50% recorrido -> SL a BE\n"
        f"2) En TP1 -> cerrar 50%\n"
        f"3) Dejar resto hasta TP2\n"
        f"_{hora}_"
    )
    send_telegram(msg)
    log.info(f"[SIGNAL] {d} | Entrada: {price} | SL: {sl} | Lote: {lot['lot']}")

# ==============================================================
# CICLO PRINCIPAL
# ==============================================================

def run_analysis():
    utc  = pytz.UTC
    now  = datetime.now(utc)
    hour = now.hour

    log.info(f"[CICLO] {now.strftime('%H:%M UTC')} | {now.strftime('%A')}")

    if now.weekday() >= 5:
        log.info("[INFO] Fin de semana - mercado cerrado.")
        return

    today = now.strftime("%Y-%m-%d")
    if state.last_reset_date != today:
        reset_daily(today)

    df = get_candles()
    if df is None:
        return

    if not state.asia_ready:
        build_asia_range(df)

    if not state.asia_ready:
        log.info("[INFO] Esperando rango de Asia...")
        return

    if CONFIG["LONDON_START"] <= hour < CONFIG["LONDON_END"]:
        log.info("[LONDRES] Buscando senal LST...")
        sig = detect_signal(df, "Londres")
        if sig:
            send_signal(sig)
            state.signal_count    += 1
            state.last_signal_dir  = sig["dir"]
        else:
            log.info("[INFO] Sin senal en Londres aun.")

    elif CONFIG["NY_START"] <= hour < CONFIG["NY_END"]:
        log.info("[NY] Buscando senal LST...")
        sig = detect_signal(df, "New York")
        if sig:
            send_signal(sig)
            state.signal_count    += 1
            state.last_signal_dir  = sig["dir"]
        else:
            log.info("[INFO] Sin senal en NY aun.")

    else:
        log.info(f"[INFO] Fuera de sesion ({hour:02d}:xx UTC).")


def reset_daily(today: str):
    state.asia_high       = 0.0
    state.asia_low        = float("inf")
    state.asia_ready      = False
    state.signal_count    = 0
    state.last_signal_dir = ""
    state.last_reset_date = today
    log.info(f"[RESET] Nuevo dia: {today}")
    send_telegram(
        f"*LST Bot - Nuevo dia*\n"
        f"Fecha: `{today}`\n"
        f"Balance: `${CONFIG['BALANCE']:,.0f}` | "
        f"Riesgo: `{CONFIG['RISK_PCT']}%`\n"
        f"_Construyendo rango Asia..._"
    )

# ==============================================================
# INICIO
# ==============================================================

def main():
    log.info("=" * 50)
    log.info("  LST GOLD BOT v3.0 - CLOUD")
    log.info("  Liquidity Side Theory | XAUUSD")
    log.info("=" * 50)

    if not CONFIG["TG_TOKEN"]:
        log.error("[CONFIG] FALTA TG_TOKEN en Railway Variables.")
    if not CONFIG["TG_CHAT_ID"]:
        log.error("[CONFIG] FALTA TG_CHAT_ID en Railway Variables.")
    if not CONFIG["TWELVE_API_KEY"]:
        log.error("[CONFIG] FALTA TWELVE_API_KEY en Railway Variables.")

    send_telegram(
        "*LST Gold Bot v3.0 - Iniciado*\n"
        f"Balance: `${CONFIG['BALANCE']:,.0f}` | "
        f"Riesgo: `{CONFIG['RISK_PCT']}%`\n"
        f"SL: `{CONFIG['SL_PIPS']} pips` | "
        f"TP1: `{CONFIG['TP1_RATIO']}` | TP2: `{CONFIG['TP2_RATIO']}`\n"
        f"_Analisis cada 15 min | Londres y NY_"
    )

    run_analysis()

    schedule.every(15).minutes.do(run_analysis)
    schedule.every().day.at("07:00").do(
        lambda: send_telegram("*LST Bot activo* - Apertura Londres")
    )

    log.info("[OK] Scheduler activo - cada 15 minutos.")
    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()
