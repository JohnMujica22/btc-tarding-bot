import time
import ccxt
import pandas as pd
import json
import os
from seguridad import obtener_credenciales, CircuitBreaker, log, reintentar

# ─────────────────────────────────────────
# CONFIGURACIÓN
# ─────────────────────────────────────────
SIMBOLO = "BTC/USDT:USDT"  # Perpetuo OKX
TF = "15m"

# Gestión de capital
POSICION_USDT = 140.0

# Riesgo / salida
STOP_PCT  = 0.02
RR        = 1.4
TAKE_PCT  = STOP_PCT * RR

# Entrada pullback
PULLBACK_PCT    = 0.002
FILL_WINDOW_MIN = 15

# RSI (confirmado por backtest)
RSI_LONG_LOW   = 58
RSI_LONG_HIGH  = 70
RSI_SHORT_LOW  = 25
RSI_SHORT_HIGH = 42

# Loop
CHECK_EVERY_SEC = 10
STATE_FILE = "bot_state.json"

# ─────────────────────────────────────────
# CONEXIÓN OKX
# ─────────────────────────────────────────
api_key, secret, passphrase = obtener_credenciales()

exchange = ccxt.okx({
    "apiKey":     api_key,
    "secret":     secret,
    "password":   passphrase,
    "options": {
        "defaultType": "swap",
    },
})
exchange.load_markets()

cb = CircuitBreaker()

# ─────────────────────────────────────────
# ESTADO
# ─────────────────────────────────────────
def guardar_estado(estado: dict):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(estado, f, ensure_ascii=False, indent=2)
    log(f"Estado guardado en {STATE_FILE}")

def cargar_estado() -> dict:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            estado = json.load(f)
        log(f"Estado cargado — keys: {list(estado.keys())}")
        return estado
    return {}

def limpiar_estado():
    if os.path.exists(STATE_FILE):
        os.remove(STATE_FILE)
    log("Estado limpiado")

# ─────────────────────────────────────────
# HELPERS EXCHANGE
# ─────────────────────────────────────────
def obtener_balance():
    b = exchange.fetch_balance({"type": "swap"})
    usdt = float(b["USDT"]["free"])
    return usdt

def amount_preciso(amount):
    return float(exchange.amount_to_precision(SIMBOLO, amount))

def price_preciso(price):
    return float(exchange.price_to_precision(SIMBOLO, price))

def cerrar_posicion_con_sl_tp(direccion, cantidad, sl_price, tp_price):
    """
    Coloca dos órdenes de salida:
    - TP: orden limit en la dirección contraria
    - SL: orden stop-market en la dirección contraria
    """
    lado_salida = "sell" if direccion == "long" else "buy"
    qty = amount_preciso(cantidad)

    # Take Profit (limit)
    tp_order = exchange.create_order(
        symbol    = SIMBOLO,
        type      = "limit",
        side      = lado_salida,
        amount    = qty,
        price     = price_preciso(tp_price),
        params    = {
            "tdMode":   "cross",
            "reduceOnly": True,
        }
    )

    # Stop Loss (stop-market)
    sl_order = exchange.create_order(
        symbol  = SIMBOLO,
        type    = "stop_market",
        side    = lado_salida,
        amount  = qty,
        params  = {
            "tdMode":    "cross",
            "stopPrice": price_preciso(sl_price),
            "reduceOnly": True,
        }
    )

    return tp_order, sl_order

def cancelar_orden(order_id):
    try:
        exchange.cancel_order(order_id, SIMBOLO)
    except Exception as e:
        log(f"Error cancelando orden {order_id}: {e}", "warning")

def orden_activa(order_id) -> bool:
    try:
        o = exchange.fetch_order(order_id, SIMBOLO)
        return o.get("status") in ["open", "partially_filled"]
    except Exception:
        return False

def posicion_cerrada() -> bool:
    try:
        posiciones = exchange.fetch_positions([SIMBOLO])
        for p in posiciones:
            if float(p.get("contracts", 0)) > 0:
                return False
        return True
    except Exception:
        return False

# ─────────────────────────────────────────
# DATOS + INDICADORES 15m
# ─────────────────────────────────────────
def obtener_datos_15m(limit=500):
    velas = exchange.fetch_ohlcv(SIMBOLO, TF, limit=limit)
    df = pd.DataFrame(velas, columns=["tiempo", "apertura", "maximo", "minimo", "cierre", "volumen"])
    df["fecha"] = pd.to_datetime(df["tiempo"], unit="ms")
    for col in ["apertura", "maximo", "minimo", "cierre", "volumen"]:
        df[col] = pd.to_numeric(df[col])
    return df

def calcular_indicadores_15m(df: pd.DataFrame) -> pd.DataFrame:
    df["EMA9"]  = df["cierre"].ewm(span=9,  adjust=False).mean()
    df["EMA21"] = df["cierre"].ewm(span=21, adjust=False).mean()
    df["EMA50"] = df["cierre"].ewm(span=50, adjust=False).mean()

    delta = df["cierre"].diff()
    gan = delta.where(delta > 0, 0.0)
    per = -delta.where(delta < 0, 0.0)
    pg  = gan.ewm(alpha=1/14, adjust=False).mean()
    pp  = per.ewm(alpha=1/14, adjust=False).mean()
    rs  = pg / pp
    df["RSI14"] = 100 - (100 / (1 + rs))

    df["precio_tipico"] = (df["maximo"] + df["minimo"] + df["cierre"]) / 3
    df["tp_vol"]        = df["precio_tipico"] * df["volumen"]
    df["fecha_dia"]     = df["fecha"].dt.date
    df["VWAP"] = (
        df.groupby("fecha_dia")["tp_vol"].cumsum()
        / df.groupby("fecha_dia")["volumen"].cumsum()
    )
    return df

def detectar_senal_15m(df: pd.DataFrame):
    """
    LONG : cruce alcista EMA9/EMA21 + RSI 58-70 + cierre > VWAP + cierre > EMA50
    SHORT: cruce bajista EMA9/EMA21 + RSI 25-42 + cierre < VWAP + cierre < EMA50
    Retorna dict con direccion, precios de entrada/SL/TP o None.
    """
    if len(df) < 60:
        return None

    actual   = df.iloc[-1]
    anterior = df.iloc[-2]

    cruce_alcista = (actual["EMA9"] > actual["EMA21"]) and (anterior["EMA9"] <= anterior["EMA21"])
    cruce_bajista = (actual["EMA9"] < actual["EMA21"]) and (anterior["EMA9"] >= anterior["EMA21"])

    rsi = float(actual["RSI14"])

    # LONG
    if (cruce_alcista
            and RSI_LONG_LOW < rsi < RSI_LONG_HIGH
            and actual["cierre"] > actual["VWAP"]
            and actual["cierre"] > actual["EMA50"]):

        close        = float(actual["cierre"])
        limit_entry  = round(close * (1 - PULLBACK_PCT), 2)
        sl           = round(limit_entry * (1 - STOP_PCT), 2)
        tp           = round(limit_entry * (1 + TAKE_PCT), 2)
        return {
            "direccion":   "long",
            "close_signal": close,
            "limit_entry": limit_entry,
            "sl": sl, "tp": tp,
            "fecha": str(actual["fecha"]),
            "rsi": rsi,
        }

    # SHORT
    if (cruce_bajista
            and RSI_SHORT_LOW < rsi < RSI_SHORT_HIGH
            and actual["cierre"] < actual["VWAP"]
            and actual["cierre"] < actual["EMA50"]):

        close        = float(actual["cierre"])
        limit_entry  = round(close * (1 + PULLBACK_PCT), 2)
        sl           = round(limit_entry * (1 + STOP_PCT), 2)
        tp           = round(limit_entry * (1 - TAKE_PCT), 2)
        return {
            "direccion":   "short",
            "close_signal": close,
            "limit_entry": limit_entry,
            "sl": sl, "tp": tp,
            "fecha": str(actual["fecha"]),
            "rsi": rsi,
        }

    return None

# ─────────────────────────────────────────
# ENTRADA LIMIT CON EXPIRACIÓN
# ─────────────────────────────────────────
def colocar_limit_entry(direccion, limit_price):
    lado   = "buy" if direccion == "long" else "sell"
    precio = price_preciso(limit_price)
    qty = max(amount_preciso(POSICION_USDT / limit_price), 0.01)

    orden = exchange.create_order(
        symbol = SIMBOLO,
        type   = "limit",
        side   = lado,
        amount = qty,
        price  = precio,
        params = {"tdMode": "cross"},
    )
    return orden, qty

def esperar_fill_o_cancelar(order_id, timeout_sec):
    t0 = time.time()
    while True:
        o      = exchange.fetch_order(order_id, SIMBOLO)
        status = o.get("status")
        if status == "closed":
            return True, o
        if status == "canceled":
            return False, o
        if time.time() - t0 >= timeout_sec:
            cancelar_orden(order_id)
            o2 = exchange.fetch_order(order_id, SIMBOLO)
            return False, o2
        time.sleep(CHECK_EVERY_SEC)

# ─────────────────────────────────────────
# LOOP PRINCIPAL
# ─────────────────────────────────────────
log("🤖 Bot iniciado — BTC/USDT:USDT — OKX SWAP — 15m — Long+Short")
usdt = obtener_balance()
log(f"Balance inicial: ${usdt:.2f} USDT")
log(f"SL={STOP_PCT*100:.2f}% | TP={TAKE_PCT*100:.2f}% (RR={RR})")
log(f"RSI Long: {RSI_LONG_LOW}-{RSI_LONG_HIGH} | RSI Short: {RSI_SHORT_LOW}-{RSI_SHORT_HIGH}")

estado = cargar_estado()

en_trade    = bool(estado.get("en_trade", False))
direccion   = estado.get("direccion")
tp_order_id = estado.get("tp_order_id")
sl_order_id = estado.get("sl_order_id")
entry_price = estado.get("entry_price")
sl_price    = estado.get("sl_price")
tp_price    = estado.get("tp_price")
cantidad    = estado.get("cantidad")

if en_trade:
    log(f"🔁 Reanudando: {direccion.upper()} activo | entry={entry_price} | qty={cantidad}")

while True:
    try:
        if not cb.puede_operar():
            log("🔴 Bot pausado por circuit breaker — revisando en 1 hora")
            time.sleep(3600)
            continue

        # Si estamos en trade: revisar si la posición cerró
        if en_trade:
            if posicion_cerrada():
                log("✅ Posición cerrada. Limpiando estado.")
                # Cancelar orden que no se ejecutó (TP o SL)
                if tp_order_id and orden_activa(tp_order_id):
                    cancelar_orden(tp_order_id)
                if sl_order_id and orden_activa(sl_order_id):
                    cancelar_orden(sl_order_id)
                en_trade = False
                limpiar_estado()
            else:
                time.sleep(30)
            continue

        # Sin trade: buscar señal
        df = reintentar(obtener_datos_15m)
        df = calcular_indicadores_15m(df)
        actual = df.iloc[-1]

        log(
            f"15m close=${float(actual['cierre']):,.2f} | "
            f"EMA9=${float(actual['EMA9']):,.2f} | EMA21=${float(actual['EMA21']):,.2f} | "
            f"EMA50=${float(actual['EMA50']):,.2f} | RSI={float(actual['RSI14']):.1f} | "
            f"VWAP=${float(actual['VWAP']):,.2f}"
        )

        senal = detectar_senal_15m(df)
        if not senal:
            log("Sin señal — esperando próxima vela 15m")
            time.sleep(60)
            continue

        direccion   = senal["direccion"]
        limit_entry = senal["limit_entry"]
        sl          = senal["sl"]
        tp          = senal["tp"]

        log(
            f"{'🟢' if direccion == 'long' else '🔴'} Señal {direccion.upper()} 15m | "
            f"close={senal['close_signal']:.2f} | limit_entry={limit_entry:.2f} | "
            f"SL={sl:.2f} | TP={tp:.2f} | RSI={senal['rsi']:.1f}"
        )

        # Colocar limit y esperar fill
        orden, cantidad = colocar_limit_entry(direccion, limit_entry)
        log(f"🧾 Limit {direccion.upper()} colocada id={orden['id']} | qty={cantidad}")

        filled, ord_final = esperar_fill_o_cancelar(orden["id"], timeout_sec=FILL_WINDOW_MIN * 60)
        if not filled:
            log(f"⏳ Limit NO llenó. Cancelada. Volviendo a esperar.")
            time.sleep(30)
            continue

        avg         = ord_final.get("average") or ord_final.get("price") or limit_entry
        entry_price = float(avg)
        log(f"✅ Limit llenó. Entry≈{entry_price:.2f} | Colocando SL y TP...")

        # Recalcular SL/TP desde entry real
        if direccion == "long":
            sl_price = round(entry_price * (1 - STOP_PCT), 2)
            tp_price = round(entry_price * (1 + TAKE_PCT), 2)
        else:
            sl_price = round(entry_price * (1 + STOP_PCT), 2)
            tp_price = round(entry_price * (1 - TAKE_PCT), 2)

        tp_order, sl_order = cerrar_posicion_con_sl_tp(direccion, cantidad, sl_price, tp_price)
        tp_order_id = tp_order.get("id")
        sl_order_id = sl_order.get("id")

        log(f"🧾 TP id={tp_order_id} @ {tp_price:.2f} | SL id={sl_order_id} @ {sl_price:.2f}")
        en_trade = True

        guardar_estado({
            "en_trade":    True,
            "direccion":   direccion,
            "tp_order_id": tp_order_id,
            "sl_order_id": sl_order_id,
            "cantidad":    cantidad,
            "entry_price": entry_price,
            "sl_price":    sl_price,
            "tp_price":    tp_price,
        })

        time.sleep(30)

    except KeyboardInterrupt:
        log("🛑 Bot detenido manualmente")
        break
    except Exception as e:
        log(f"Error inesperado: {e}", "error")
        time.sleep(30)
        continue