"""
╔══════════════════════════════════════════════════════════════╗
║         LST GOLD BOT — CLOUD VERSION (Python)               ║
║   Liquidity Side Theory | Mr. Liquidity x BeikerFx          ║
║   XAUUSD → Telegram | 24/7 en la nube sin MT5               ║
╚══════════════════════════════════════════════════════════════╝

Corre 100% en la nube (VPS/Railway/Render).
Usa la API de Yahoo Finance (gratis) para obtener precios del oro.
Envía señales a Telegram aunque no estés conectado.
"""

import time
import json
import logging
import requests
import schedule
import pytz
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional
import yfinance as yf
import pandas as pd
import numpy as np
import os

# ══════════════════════════════════════════════════════════════
# CONFIGURACIÓN — edita solo esta sección
# ══════════════════════════════════════════════════════════════
CONFIG = {
    # ── Telegram ──────────────────────────────────────────────
    "TG_TOKEN"      : os.getenv("TG_TOKEN", "TU_BOT_TOKEN_AQUI"),
    "TG_CHAT_ID"    : os.getenv("TG_CHAT_ID", "TU_CHAT_ID_AQUI"),

    # ── Cuenta de fondeo ──────────────────────────────────────
    "BALANCE"       : float(os.getenv("BALANCE", "10000")),
    "RISK_PCT"      : float(os.getenv("RISK_PCT", "0.50")),   # 0.5%

    # ── Gestión de la operación ───────────────────────────────
    "SL_PIPS"       : int(os.getenv("SL_PIPS", "150")),       # pips de SL
    "TP1_RATIO"     : 1.5,   # R:R TP1 (cierra 50%)
    "TP2_RATIO"     : 3.0,   # R:R TP2 (deja correr el resto)

    # ── Fibonacci ─────────────────────────────────────────────
    "FIB_LEVELS"    : [0.618, 0.700, 0.786],
    "FIB_TOLERANCE" : 0.003,  # ±0.3%

    # ── Filtros ───────────────────────────────────────────────
    "MIN_ATR"       : 0.80,   # ATR mínimo en $ para operar
    "MIN_CONFIRMATIONS": 2,   # mínimo de confirmaciones requeridas

    # ── Horarios UTC (broker UTC+3 → restar 3h para UTC) ──────
    # Tu broker: London 10:00 → UTC 07:00
    "ASIA_START"    : 19,  # hora UTC del día anterior
    "ASIA_END"      : 0,   # medianoche UTC
    "LONDON_START"  : 7,   # 07:00 UTC = 10:00 broker
    "LONDON_END"    : 10,  # 10:00 UTC = 13:00 broker
    "NY_START"      : 12,  # 12:00 UTC = 15:00 broker
    "NY_END"        : 16,  # 16:00 UTC = 19:00 broker

    # ── Símbolo en Yahoo Finance ──────────────────────────────
    "SYMBOL"        : "GC=F",   # Gold Futures (precio más preciso)
    # Alternativa spot: "XAUUSD=X"
}

# ══════════════════════════════════════════════════════════════
# LOGGING
# ══════════════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("lst_bot.log", encoding="utf-8")
    ]
)
log = logging.getLogger("LST_BOT")

# ══════════════════════════════════════════════════════════════
# ESTADO DEL BOT (en memoria, se resetea cada día)
# ══════════════════════════════════════════════════════════════
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

# ══════════════════════════════════════════════════════════════
# OBTENER DATOS DE PRECIO (Yahoo Finance — gratis, sin API key)
# ══════════════════════════════════════════════════════════════
def get_candles(period: str = "2d", interval: str = "15m") -> Optional[pd.DataFrame]:
    """Descarga velas OHLCV del oro desde Yahoo Finance."""
    try:
        ticker = yf.Ticker(CONFIG["SYMBOL"])
        df = ticker.history(period=period, interval=interval)
        if df.empty:
            log.warning("⚠️  Yahoo Finance devolvió datos vacíos.")
            return None
        df.index = pd.to_datetime(df.index, utc=True)
        log.info(f"✅ Datos obtenidos: {len(df)} velas | Último precio: {df['Close'].iloc[-1]:.2f}")
        return df
    except Exception as e:
        log.error(f"❌ Error obteniendo datos: {e}")
        return None

def get_current_price() -> float:
    """Precio actual del oro."""
    try:
        ticker = yf.Ticker(CONFIG["SYMBOL"])
        data = ticker.history(period="1d", interval="1m")
        return float(data["Close"].iloc[-1]) if not data.empty else 0.0
    except:
        return 0.0

def calculate_atr(df: pd.DataFrame, period: int = 14) -> float:
    """Average True Range para filtrar volatilidad."""
    high = df["High"]
    low  = df["Low"]
    close_prev = df["Close"].shift(1)
    tr = pd.concat([
        high - low,
        (high - close_prev).abs(),
        (low  - close_prev).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean().iloc[-1]

# ══════════════════════════════════════════════════════════════
# LÓGICA LST
# ══════════════════════════════════════════════════════════════

def build_asia_range(df: pd.DataFrame):
    """Construye el rango HIGH/LOW de la sesión Asia."""
    utc = pytz.UTC
    now = datetime.now(utc)

    # Asia = 19:00 UTC día anterior a 00:00 UTC hoy
    asia_start = now.replace(hour=19, minute=0, second=0, microsecond=0) - timedelta(days=1)
    asia_end   = now.replace(hour=0,  minute=0, second=0, microsecond=0)

    asia_candles = df[(df.index >= asia_start) & (df.index < asia_end)]

    if asia_candles.empty:
        log.warning("⚠️  No hay velas de Asia aún.")
        return

    state.asia_high  = float(asia_candles["High"].max())
    state.asia_low   = float(asia_candles["Low"].min())
    state.asia_ready = True
    log.info(f"📊 Rango Asia → HIGH: {state.asia_high:.2f} | LOW: {state.asia_low:.2f}")

def detect_liquidity_take(df: pd.DataFrame, session: str) -> Optional[dict]:
    """
    Detecta tomas de liquidez LST:
    - Tipo 1: Continuación (vela cierra con cuerpo fuera de la zona)
    - Tipo 2: Mechazo/Manipulación (mecha penetra y cierra de vuelta)
    Retorna dict con señal o None.
    """
    if not state.asia_ready:
        return None

    # Últimas 4 velas
    if len(df) < 4:
        return None

    c0 = df.iloc[-1]   # vela actual
    c1 = df.iloc[-2]   # vela anterior (la que hace la toma)
    c2 = df.iloc[-3]
    c3 = df.iloc[-4]

    price   = float(c0["Close"])
    high1   = float(c1["High"])
    low1    = float(c1["Low"])
    close1  = float(c1["Close"])
    open1   = float(c1["Open"])
    high0   = float(c0["High"])
    low0    = float(c0["Low"])
    close0  = float(c0["Close"])
    open0   = float(c0["Open"])

    atr     = calculate_atr(df)
    signal  = None

    # ── SEÑAL BUY ────────────────────────────────────────────
    # Tipo 2: mecha baja del AsiaLow y vela CIERRA de vuelta arriba (mechazo)
    tipo2_buy = (low1 < state.asia_low and close1 > state.asia_low)
    # Tipo 1: precio en tendencia alcista y retrocede al AsiaLow
    tipo1_buy = (low1 <= state.asia_low * 1.001 and close1 > state.asia_low)

    if tipo2_buy or tipo1_buy:
        confirmations = []

        # Modelo 1: vela de toma cierra con cuerpo sobre la zona
        if close1 > state.asia_low:
            confirmations.append("✅ Modelo 1: Cierre sobre zona de liquidez")

        # Modelo 2: vela envolvente alcista
        if close0 > open0 and close0 > high1 and open0 < close1:
            confirmations.append("✅ Modelo 2: Vela envolvente alcista")

        # Patrón W
        if detect_pattern_w(c3, c2, c1, state.asia_low):
            confirmations.append("✅ Patrón W detectado")

        # Fibonacci
        fib_ok, fib_level = check_fibonacci(state.asia_high, state.asia_low, price, "BUY")
        if fib_ok:
            confirmations.append(f"✅ Confluencia Fib {fib_level:.3f}")

        # ATR
        if atr >= CONFIG["MIN_ATR"]:
            confirmations.append(f"✅ Volatilidad ATR: ${atr:.2f}")

        if len(confirmations) >= CONFIG["MIN_CONFIRMATIONS"]:
            signal = {
                "dir"          : "BUY",
                "price"        : price,
                "tipo"         : 2 if tipo2_buy else 1,
                "confirmations": confirmations,
                "atr"          : atr,
                "session"      : session,
            }

    # ── SEÑAL SELL ───────────────────────────────────────────
    tipo2_sell = (high1 > state.asia_high and close1 < state.asia_high)
    tipo1_sell = (high1 >= state.asia_high * 0.999 and close1 < state.asia_high)

    if (tipo2_sell or tipo1_sell) and signal is None:
        confirmations = []

        if close1 < state.asia_high:
            confirmations.append("✅ Modelo 1: Cierre bajo zona de liquidez")

        if close0 < open0 and close0 < low1 and open0 > close1:
            confirmations.append("✅ Modelo 2: Vela envolvente bajista")

        if detect_pattern_m(c3, c2, c1, state.asia_high):
            confirmations.append("✅ Patrón M detectado")

        fib_ok, fib_level = check_fibonacci(state.asia_low, state.asia_high, price, "SELL")
        if fib_ok:
            confirmations.append(f"✅ Confluencia Fib {fib_level:.3f}")

        if atr >= CONFIG["MIN_ATR"]:
            confirmations.append(f"✅ Volatilidad ATR: ${atr:.2f}")

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

def detect_pattern_w(c3, c2, c1, support: float) -> bool:
    """Detecta patrón W (doble suelo) para confirmación BUY."""
    l3 = float(c3["Low"])
    h2 = float(c2["High"])
    l1 = float(c1["Low"])
    first_valley  = l3 <= support * 1.002
    peak_between  = h2 > l3 * 1.001
    second_valley = l1 <= support * 1.002 and l1 >= l3
    return first_valley and peak_between and second_valley

def detect_pattern_m(c3, c2, c1, resistance: float) -> bool:
    """Detecta patrón M (doble techo) para confirmación SELL."""
    h3 = float(c3["High"])
    l2 = float(c2["Low"])
    h1 = float(c1["High"])
    first_peak  = h3 >= resistance * 0.998
    valley      = l2 < h3 * 0.999
    second_peak = h1 >= resistance * 0.998 and h1 <= h3
    return first_peak and valley and second_peak

def check_fibonacci(from_price: float, to_price: float,
                    current: float, direction: str):
    """Verifica si el precio está en una zona Fibonacci clave."""
    rng = abs(to_price - from_price)
    for level in CONFIG["FIB_LEVELS"]:
        if direction == "BUY":
            fib_price = from_price - rng * level
        else:
            fib_price = from_price + rng * level
        tol = fib_price * CONFIG["FIB_TOLERANCE"]
        if abs(current - fib_price) <= tol:
            return True, level
    return False, 0.0

# ══════════════════════════════════════════════════════════════
# CÁLCULO DE LOTAJE
# ══════════════════════════════════════════════════════════════
def calculate_lot(price: float, sl: float) -> dict:
    """
    Calcula el lotaje basado en riesgo fijo del 0.5%.
    XAUUSD: 1 lote estándar = 100 oz → $10 por pip (aprox)
    """
    risk_money = CONFIG["BALANCE"] * (CONFIG["RISK_PCT"] / 100)
    sl_pips    = abs(price - sl) / 0.1   # 1 pip XAUUSD = $0.10

    # Valor por pip por lote estándar en XAUUSD ≈ $10
    pip_value_per_lot = 10.0
    lot = risk_money / (sl_pips * pip_value_per_lot)

    # Redondear a 2 decimales, mínimo 0.01
    lot = max(0.01, round(lot, 2))

    return {
        "lot"        : lot,
        "risk_money" : round(risk_money, 2),
        "sl_pips"    : round(sl_pips, 1),
    }

# ══════════════════════════════════════════════════════════════
# TELEGRAM
# ══════════════════════════════════════════════════════════════
def send_telegram(message: str):
    """Envía mensaje a Telegram con formato Markdown."""
    url  = f"https://api.telegram.org/bot{CONFIG['TG_TOKEN']}/sendMessage"
    data = {
        "chat_id"    : CONFIG["TG_CHAT_ID"],
        "text"       : message,
        "parse_mode" : "Markdown",
    }
    try:
        r = requests.post(url, data=data, timeout=10)
        if r.status_code == 200:
            log.info("✅ Mensaje enviado a Telegram.")
        else:
            log.error(f"❌ Telegram error {r.status_code}: {r.text}")
    except Exception as e:
        log.error(f"❌ Error enviando a Telegram: {e}")

def send_signal(signal: dict):
    """Construye y envía el mensaje de señal a Telegram."""
    d      = signal["dir"]
    price  = signal["price"]
    sl_pip = CONFIG["SL_PIPS"] * 0.1   # 1 pip XAUUSD = $0.10

    if d == "BUY":
        sl  = round(price - sl_pip, 2)
        tp1 = round(price + sl_pip * CONFIG["TP1_RATIO"], 2)
        tp2 = round(price + sl_pip * CONFIG["TP2_RATIO"], 2)
        emoji = "🟢"
    else:
        sl  = round(price + sl_pip, 2)
        tp1 = round(price - sl_pip * CONFIG["TP1_RATIO"], 2)
        tp2 = round(price - sl_pip * CONFIG["TP2_RATIO"], 2)
        emoji = "🔴"

    lot_info = calculate_lot(price, sl)
    tipo_str = "Tipo 2 ─ Mechazo 💥" if signal["tipo"] == 2 else "Tipo 1 ─ Continuidad"
    conf_str = "\n".join([f"   {c}" for c in signal["confirmations"]])
    n_conf   = len(signal["confirmations"])

    now_utc  = datetime.now(pytz.UTC)
    now_local_str = now_utc.strftime("%d/%m/%Y %H:%M UTC")

    msg = f"""
{emoji} *SEÑAL LST — XAUUSD (ORO)*
━━━━━━━━━━━━━━━━━━━━━
📌 *Dirección:*  `{d}`
💲 *Entrada:*    `{price:.2f}`
🛑 *Stop Loss:*  `{sl:.2f}`  ({CONFIG['SL_PIPS']} pips)
🎯 *TP1 (50%):* `{tp1:.2f}`  (R:R {CONFIG['TP1_RATIO']})
🏆 *TP2 (50%):* `{tp2:.2f}`  (R:R {CONFIG['TP2_RATIO']})
━━━━━━━━━━━━━━━━━━━━━
📦 *Lotaje:*     `{lot_info['lot']} lotes`
💰 *Riesgo $:*   `${lot_info['risk_money']}`
━━━━━━━━━━━━━━━━━━━━━
📊 *Estrategia LST:*
   • Toma: {tipo_str}
   • Sesión: {signal['session']}
   • Rango Asia: `{state.asia_low:.2f}` — `{state.asia_high:.2f}`
   • Confirmaciones: {n_conf}/{len(signal['confirmations'])} ✅
{conf_str}
━━━━━━━━━━━━━━━━━━━━━
⚙️ *Gestión de la operación:*
   1️⃣ Al 50% del recorrido → mover SL a BE
   2️⃣ En TP1 → cerrar 50% de la posición
   3️⃣ Dejar el resto correr hasta TP2
━━━━━━━━━━━━━━━━━━━━━
🕐 _{now_local_str}_
⚠️ _El trading es probabilístico. Cuida tu capital._
""".strip()

    send_telegram(msg)
    log.info(f"📡 Señal enviada: {d} | Entrada: {price} | Lote: {lot_info['lot']}")

# ══════════════════════════════════════════════════════════════
# CICLO PRINCIPAL — se ejecuta cada 15 minutos
# ══════════════════════════════════════════════════════════════
def run_analysis():
    """Análisis principal LST — se ejecuta en cada ciclo."""
    utc  = pytz.UTC
    now  = datetime.now(utc)
    hour = now.hour

    log.info(f"🔄 Análisis LST | Hora UTC: {now.strftime('%H:%M')} | Día: {now.strftime('%A')}")

    # No operar fines de semana
    if now.weekday() >= 5:
        log.info("⏸  Fin de semana — mercado cerrado.")
        return

    # Reset diario (a las 00:00 UTC)
    today_str = now.strftime("%Y-%m-%d")
    if state.last_reset_date != today_str:
        reset_daily(today_str)

    # Descargar datos
    df = get_candles(period="2d", interval="15m")
    if df is None:
        return

    # ── FASE 1: Construir rango Asia ──────────────────────────
    if not state.asia_ready:
        build_asia_range(df)

    if not state.asia_ready:
        log.info("⏳ Esperando completar el rango de Asia...")
        return

    # ── FASE 2: Sesión Londres (07:00–10:00 UTC) ──────────────
    if CONFIG["LONDON_START"] <= hour < CONFIG["LONDON_END"]:
        if not state.signal_sent:
            log.info("🏙  Sesión LONDRES activa — buscando manipulación LST...")
            signal = detect_liquidity_take(df, "🏙 Londres (Manipulación)")
            if signal:
                send_signal(signal)
                state.signal_sent     = True
                state.last_signal_dir = signal["dir"]
            else:
                log.info("🔍 Sin señal LST en Londres todavía...")

    # ── FASE 3: Sesión New York (12:00–16:00 UTC) ─────────────
    elif CONFIG["NY_START"] <= hour < CONFIG["NY_END"]:
        if not state.signal_sent:
            log.info("🗽 Sesión NEW YORK activa — buscando continuación AMD...")
            signal = detect_liquidity_take(df, "🗽 New York (Distribución)")
            if signal:
                send_signal(signal)
                state.signal_sent     = True
                state.last_signal_dir = signal["dir"]
            else:
                log.info("🔍 Sin señal LST en New York todavía...")

    else:
        log.info(f"⏸  Hora UTC {hour:02d}:xx — fuera de ventana de trading LST.")

def reset_daily(today: str):
    """Resetea el estado del bot cada nuevo día."""
    state.asia_high       = 0.0
    state.asia_low        = float("inf")
    state.london_high     = 0.0
    state.london_low      = float("inf")
    state.asia_ready      = False
    state.signal_sent     = False
    state.last_signal_dir = ""
    state.last_reset_date = today
    log.info(f"🔄 Reset diario ejecutado — {today}")
    send_telegram(
        f"🌅 *LST Bot — Nuevo día de trading*\n"
        f"📅 Fecha: `{today}`\n"
        f"⏳ Construyendo rango Asia...\n"
        f"💰 Balance: `${CONFIG['BALANCE']:,.2f}` | Riesgo: `{CONFIG['RISK_PCT']}%`"
    )

# ══════════════════════════════════════════════════════════════
# INICIO
# ══════════════════════════════════════════════════════════════
def main():
    log.info("=" * 60)
    log.info("   LST GOLD BOT — CLOUD VERSION  🚀")
    log.info("   Liquidity Side Theory | XAUUSD → Telegram")
    log.info("=" * 60)

    send_telegram(
        "🤖 *LST Gold Bot — Iniciado en la nube* ☁️\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Par: `XAUUSD (Oro)`\n"
        f"💰 Balance: `${CONFIG['BALANCE']:,.2f}`\n"
        f"⚠️ Riesgo/op: `{CONFIG['RISK_PCT']}%` = `${CONFIG['BALANCE'] * CONFIG['RISK_PCT'] / 100:.2f}`\n"
        f"🛑 SL: `{CONFIG['SL_PIPS']} pips`\n"
        f"🎯 TP1: `R:R {CONFIG['TP1_RATIO']}` | TP2: `R:R {CONFIG['TP2_RATIO']}`\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "_Analizando mercado cada 15 minutos..._\n"
        "_Sesiones: Londres 07–10 UTC | NY 12–16 UTC_"
    )

    # Ejecutar inmediatamente al iniciar
    run_analysis()

    # Programar cada 15 minutos
    schedule.every(15).minutes.do(run_analysis)

    # Heartbeat diario (confirma que el bot sigue vivo)
    schedule.every().day.at("07:00").do(
        lambda: send_telegram("💚 *LST Bot activo* — Analizando apertura de Londres...")
    )

    log.info("⏰ Scheduler iniciado — análisis cada 15 minutos.")

    while True:
        schedule.run_pending()
        time.sleep(30)

if __name__ == "__main__":
    main()
