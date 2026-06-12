import time
import ccxt
import pandas as pd
import json
import os
from seguridad import obtener_credenciales, CircuitBreaker, log, reintentar
import logging

logging.basicConfig(
    filename="bot_trading.log",
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    datefmt="[%H:%M:%S]"
)

# ─────────────────────────────────────────
# CONFIGURACIÓN 11-6-2026 AA
# ─────────────────────────────────────────
SIMBOLO = "BTC/USDT:USDT"  # Perpetuo OKX
TF = "15m"

# Gestión de capital
# FIX #2: POSICION_USDT ahora se usa realmente para calcular qty
# Apalancamiento 3x configurado en la app OKX manualmente
POSICION_USDT  = 280.0
APALANCAMIENTO = 3

# Riesgo / salida
STOP_PCT  = 0.02
RR        = 1.5  # antes 1.4
TAKE_PCT  = STOP_PCT * RR   # 0.03 → TP 3%

# Entrada pullback
PULLBACK_PCT    = 0.002
FILL_WINDOW_MIN = 30  # antes 15

# RSI (confirmado por backtest)
RSI_LONG_LOW   = 50  # antes 58
RSI_LONG_HIGH  = 70
RSI_SHORT_LOW  = 25
RSI_SHORT_HIGH = 42

# Control de direcciones
# True  = opera Long + Short (contexto bajista o neutral)
# False = solo Long          (contexto alcista confirmado)
ENABLE_SHORTS = True

# Loop
CHECK_EVERY_SEC = 10
STATE_FILE = "bot_state.json"

# ─────────────────────────────────────────
# CONEXIÓN OKX
# ─────────────────────────────────────────
api_key, secret, passphrase = obtener_credenciales()

exchange = ccxt.okx({
    "apiKey":   api_key,
    "secret":   secret,
    "password": passphrase,
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
    usdt_free  = float(b["USDT"]["free"])
    usdt_total = float(b["USDT"]["total"])
    usdt_used  = float(b["USDT"]["used"])
    logging.info(f"[BALANCE] free={usdt_free:.2f} | total={usdt_total:.2f} | used={usdt_used:.2f}")
    return usdt_free

def amount_preciso(amount):
    try:
        resultado = float(exchange.amount_to_precision(SIMBOLO, amount))
    except Exception as e:
        log(f"⚠️ amount_to_precision falló: {e} — usando redondeo manual", "warning")
        resultado = round(amount, 4)
    log(f"🔍 amount_preciso input={amount:.6f} output={resultado}")
    return resultado

def price_preciso(price):
    return float(exchange.price_to_precision(SIMBOLO, price))

def cerrar_posicion_con_sl_tp(direccion, cantidad, sl_price, tp_price):
    """
    FIX #3: Se agrega closePosition=True en el TP para que OKX cierre
    exactamente la posición abierta y no abra una nueva en dirección contraria.
    SL sigue siendo orden trigger (ordType=trigger) sin reduceOnly (incompatible con Cross).
    """
    lado_salida = "sell" if direccion == "long" else "buy"
    qty = amount_preciso(cantidad)

    # ── Take Profit (limit + closePosition) ──
    tp_order = exchange.create_order(
        symbol = SIMBOLO,
        type   = "limit",
        side   = lado_salida,
        amount = qty,
        price  = price_preciso(tp_price),
        params = {
            "tdMode":        "cross",
            "closePosition": True,   # FIX #3: cierra exactamente la posición abierta
        }
    )

    # ── Stop Loss (trigger market) ──
    sl_order = exchange.create_order(
        symbol = SIMBOLO,
        type   = "limit",
        side   = lado_salida,
        amount = qty,
        price  = -1,
        params = {
            "tdMode":        "cross",
            "ordType":       "trigger",
            "triggerPrice":  price_preciso(sl_price),
            "triggerPxType": "last",
            "orderPrice":    -1,
        }
    )

    return tp_order, sl_order

def cancelar_orden(order_id):
    """Cancela una orden limit normal (TP). No sirve para órdenes trigger."""
    try:
        exchange.cancel_order(order_id, SIMBOLO)
        log(f"✅ Orden limit {order_id} cancelada")
    except Exception as e:
        log(f"⚠️ Error cancelando orden limit {order_id}: {e}", "warning")

# ─────────────────────────────────────────
# FIX #9 — CANCELAR ORDEN TRIGGER (SL HUÉRFANA)
# ─────────────────────────────────────────
def cancelar_algo_order(algo_id):
    """
    Cancela una orden trigger/algo en OKX usando el endpoint nativo.
    OKX usa POST /api/v5/trade/cancel-algos — endpoint DIFERENTE al cancel-order estándar.
    ccxt lo expone como private_post_trade_cancel_algos y recibe una LISTA de objetos.
    Se llama específicamente para cancelar el SL cuando el TP ya ejecutó.
    """
    try:
        # Convertir símbolo ccxt → formato OKX nativo: BTC/USDT:USDT → BTC-USDT-SWAP
        inst_id = SIMBOLO.replace("/", "-").replace(":USDT", "-SWAP")  # BTC-USDT-SWAP
        resultado = exchange.private_post_trade_cancel_algos([
            {
                "algoId": str(algo_id),
                "instId": inst_id,
            }
        ])
        log(f"✅ Algo order (SL trigger) {algo_id} cancelada | resp={resultado}")
        return True
    except Exception as e:
        log(f"⚠️ Error cancelando algo order {algo_id}: {e}", "warning")
        return False

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
    gan   = delta.where(delta > 0, 0.0)
    per   = -delta.where(delta < 0, 0.0)
    pg    = gan.ewm(alpha=1/14, adjust=False).mean()
    pp    = per.ewm(alpha=1/14, adjust=False).mean()
    rs    = pg / pp
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
    LONG : cruce alcista EMA9/EMA21 + RSI 50-70 + cierre > VWAP + cierre > EMA50
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

        close       = float(actual["cierre"])
        limit_entry = round(close * (1 - PULLBACK_PCT), 2)
        sl          = round(limit_entry * (1 - STOP_PCT), 2)
        tp          = round(limit_entry * (1 + TAKE_PCT), 2)
        return {
            "direccion":    "long",
            "close_signal": close,
            "limit_entry":  limit_entry,
            "sl": sl, "tp": tp,
            "fecha": str(actual["fecha"]),
            "rsi": rsi,
        }

    # SHORT — solo si ENABLE_SHORTS = True
    if ENABLE_SHORTS:
        if (cruce_bajista
                and RSI_SHORT_LOW < rsi < RSI_SHORT_HIGH
                and actual["cierre"] < actual["VWAP"]
                and actual["cierre"] < actual["EMA50"]):

            close       = float(actual["cierre"])
            limit_entry = round(close * (1 + PULLBACK_PCT), 2)
            sl          = round(limit_entry * (1 + STOP_PCT), 2)
            tp          = round(limit_entry * (1 - TAKE_PCT), 2)
            return {
                "direccion":    "short",
                "close_signal": close,
                "limit_entry":  limit_entry,
                "sl": sl, "tp": tp,
                "fecha": str(actual["fecha"]),
                "rsi": rsi,
            }

    return None

# ─────────────────────────────────────────
# ENTRADA LIMIT CON EXPIRACIÓN
# ─────────────────────────────────────────
def colocar_limit_entry(direccion, limit_price):
    """
    FIX #2: qty calculada desde POSICION_USDT * APALANCAMIENTO,
    no desde el balance total. Así arriesgas exactamente lo configurado.
    Con POSICION_USDT=280 y APALANCAMIENTO=3:
        nocional = $840 | TP ~$25.20 | SL ~$16.80
    """
    lado   = "buy" if direccion == "long" else "sell"
    precio = price_preciso(limit_price)

    balance_actual = obtener_balance()
    log(f"💰 Balance actual: ${balance_actual:.2f} USDT")

    # Verificar que tengamos margen suficiente para la posición
    margen_requerido = POSICION_USDT
    if balance_actual < margen_requerido:
        log(f"⚠️ Balance insuficiente (${balance_actual:.2f}) para posición de ${margen_requerido:.2f}. Orden cancelada.", "warning")
        return None, None

    CONTRACT_SIZE = 0.01  # 1 contrato OKX = 0.01 BTC
    qty_btc       = (POSICION_USDT * APALANCAMIENTO) / limit_price
    qty_raw       = qty_btc / CONTRACT_SIZE  # convertir BTC → contratos
    qty           = amount_preciso(qty_raw)

    log(f"📐 POSICION=${POSICION_USDT} | APAL={APALANCAMIENTO}x | qty_raw={qty_raw:.6f} | qty={qty}")
    log(f"📊 Ganancia estimada TP: ${qty * CONTRACT_SIZE * limit_price * TAKE_PCT:.2f} | Pérdida estimada SL: ${qty * CONTRACT_SIZE * limit_price * STOP_PCT:.2f}")

    MIN_QTY = 0.01
    if qty < MIN_QTY:
        log(f"⚠️ qty={qty} menor al mínimo {MIN_QTY} BTC. Orden cancelada.", "warning")
        return None, None

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
log("🤖 Bot iniciado — BTC/USDT:USDT — OKX SWAP — 15m")
log(f"📡 Modo: {'Long + Short' if ENABLE_SHORTS else '✋ Solo Long (shorts desactivados)'}")
usdt = obtener_balance()
log(f"Balance inicial: ${usdt:.2f} USDT")
log(f"SL={STOP_PCT*100:.2f}% | TP={TAKE_PCT*100:.2f}% (RR={RR})")
log(f"RSI Long: {RSI_LONG_LOW}-{RSI_LONG_HIGH} | RSI Short: {RSI_SHORT_LOW}-{RSI_SHORT_HIGH}")
log(f"Posición: ${POSICION_USDT} × {APALANCAMIENTO}x = ${POSICION_USDT * APALANCAMIENTO} nocional")
log(f"Ganancia estimada por TP: ~${POSICION_USDT * APALANCAMIENTO * TAKE_PCT:.2f} | Pérdida por SL: ~${POSICION_USDT * APALANCAMIENTO * STOP_PCT:.2f}")

estado = cargar_estado()

en_trade    = bool(estado.get("en_trade", False))
direccion   = estado.get("direccion")
tp_order_id = estado.get("tp_order_id")
sl_order_id = estado.get("sl_order_id")
entry_price = estado.get("entry_price")
sl_price    = estado.get("sl_price")
tp_price    = estado.get("tp_price")
cantidad    = estado.get("cantidad")
# FIX #1: cargar balance previo al trade para calcular PnL real
balance_pre_trade = estado.get("balance_pre_trade")

if en_trade:
    log(f"🔁 Reanudando: {direccion.upper()} activo | entry={entry_price} | qty={cantidad}")

while True:
    try:
        if not cb.puede_operar():
            log("🔴 Bot pausado por circuit breaker — revisando en 1 hora")
            time.sleep(3600)
            continue

        # ── Si estamos en trade: revisar si la posición cerró ──
        if en_trade:
            if posicion_cerrada():
                log("✅ Posición cerrada. Limpiando estado.")

                # Cancelar TP (orden limit normal)
                cancelar_orden(tp_order_id)

                # FIX #9: Cancelar SL usando endpoint nativo de OKX para órdenes trigger
                # cancel-order estándar NO funciona para trigger orders en OKX
                if sl_order_id:
                    cancelar_algo_order(sl_order_id)

                # FIX #1: PnL real usando diferencia de balance
                try:
                    balance_post_trade = obtener_balance()
                    if balance_pre_trade:
                        pnl_real = balance_post_trade - float(balance_pre_trade)
                        cb.registrar_trade(pnl_real)
                        resultado = "✅ TP" if pnl_real > 0 else "❌ SL"
                        log(f"💰 {resultado} | PnL real: ${pnl_real:+.2f} | Balance: ${balance_post_trade:.2f}")
                    else:
                        log("⚠️ No se encontró balance_pre_trade — PnL no registrado en CircuitBreaker", "warning")
                except Exception as e:
                    log(f"⚠️ Error calculando PnL real: {e}", "warning")

                en_trade          = False
                balance_pre_trade = None
                limpiar_estado()
            else:
                time.sleep(30)
            continue

        # ── Sin trade: buscar señal ──
        df     = reintentar(obtener_datos_15m)
        df     = calcular_indicadores_15m(df)
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

        # FIX #1: capturar balance antes de entrar al trade
        balance_pre_trade = obtener_balance()
        log(f"💼 Balance pre-trade capturado: ${balance_pre_trade:.2f}")

        orden, cantidad = colocar_limit_entry(direccion, limit_entry)
        if orden is None:
            log("⚠️ No se pudo colocar la orden. Volviendo a esperar.")
            balance_pre_trade = None
            time.sleep(30)
            continue

        log(f"🧾 Limit {direccion.upper()} colocada id={orden['id']} | qty={cantidad}")

        filled, ord_final = esperar_fill_o_cancelar(orden["id"], timeout_sec=FILL_WINDOW_MIN * 60)
        if not filled:
            log("⏳ Limit NO llenó. Cancelada. Volviendo a esperar.")
            balance_pre_trade = None
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

        # FIX #1: guardar balance_pre_trade en el estado para sobrevivir reinicios
        guardar_estado({
            "en_trade":          True,
            "direccion":         direccion,
            "tp_order_id":       tp_order_id,
            "sl_order_id":       sl_order_id,
            "cantidad":          cantidad,
            "entry_price":       entry_price,
            "sl_price":          sl_price,
            "tp_price":          tp_price,
            "balance_pre_trade": balance_pre_trade,
        })

        time.sleep(30)

    except KeyboardInterrupt:
        log("🛑 Bot detenido manualmente")
        break
    except Exception as e:
        log(f"Error inesperado: {e}", "error")
        time.sleep(30)
        continue