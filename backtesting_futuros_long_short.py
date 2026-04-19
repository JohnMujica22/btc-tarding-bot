import pandas as pd
import requests
import time
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec

URL = "https://api.binance.com/api/v3/klines"

# ─────────────────────────────────────────
# PARÁMETROS
# ─────────────────────────────────────────
DIAS = 200
SYMBOL = "BTCUSDT"
CAPITAL_INICIAL = 142.0
POS_USDT = 140.0

STOP_PCT = 0.02
RR = 1.4
TAKE_PCT = STOP_PCT * RR

PULLBACK_PCT = 0.002
FILL_WINDOW = 1

# Comisiones OKX futuros (maker/taker)
FEE_MAKER = 0.0002   # 0.02%
FEE_TAKER = 0.0005   # 0.05%

# RSI ganador del análisis anterior
RSI_LONG_LOW  = 58
RSI_LONG_HIGH = 70

# RSI para shorts (espejo bajista)
RSI_SHORT_LOW  = 25
RSI_SHORT_HIGH = 42

# ─────────────────────────────────────────
# DESCARGA Y PREPARACIÓN
# ─────────────────────────────────────────
def descargar_datos(symbol, interval, dias):
    todas = []
    limite = 1000
    ahora_ms = int(time.time() * 1000)
    inicio_ms = ahora_ms - (dias * 24 * 60 * 60 * 1000)
    t_actual = inicio_ms
    while t_actual < ahora_ms:
        params = {"symbol": symbol, "interval": interval, "startTime": t_actual, "limit": limite}
        r = requests.get(URL, params=params, timeout=30)
        data = r.json()
        if not data:
            break
        todas.extend(data)
        t_actual = data[-1][0] + 1
        time.sleep(0.15)
    return todas

def resample(df5, tf):
    df = df5.copy().set_index("fecha")
    out = df[["apertura", "maximo", "minimo", "cierre", "volumen"]].resample(tf).agg({
        "apertura": "first", "maximo": "max", "minimo": "min",
        "cierre": "last", "volumen": "sum",
    }).dropna().reset_index()
    return out

def calc_rsi(close, period=14):
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_g = gain.ewm(alpha=1/period, adjust=False).mean()
    avg_l = loss.ewm(alpha=1/period, adjust=False).mean()
    rs = avg_g / avg_l
    return 100 - (100 / (1 + rs))

def fees_roundtrip(pos_usdt, pnl_bruto):
    return (pos_usdt * FEE_MAKER) + ((pos_usdt + abs(pnl_bruto)) * FEE_TAKER)

# ─────────────────────────────────────────
# DESCARGA
# ─────────────────────────────────────────
print("Descargando datos 5m (200 días)...")
raw5 = descargar_datos(SYMBOL, "5m", DIAS)

df5 = pd.DataFrame(raw5, columns=[
    "tiempo", "apertura", "maximo", "minimo", "cierre", "volumen",
    "tiempo_cierre", "volumen_quote", "num_trades", "taker_buy_base", "taker_buy_quote", "ignorar"
])
for col in ["cierre", "maximo", "minimo", "apertura", "volumen"]:
    df5[col] = pd.to_numeric(df5[col])
df5["fecha"] = pd.to_datetime(df5["tiempo"], unit="ms") - pd.Timedelta(hours=5)
df5 = df5[["fecha", "apertura", "maximo", "minimo", "cierre", "volumen"]].drop_duplicates("fecha").reset_index(drop=True)

print("Resampleando a 15m y calculando indicadores...")
df15 = resample(df5, "15min")
df15["EMA9"]  = df15["cierre"].ewm(span=9,  adjust=False).mean()
df15["EMA21"] = df15["cierre"].ewm(span=21, adjust=False).mean()
df15["EMA50"] = df15["cierre"].ewm(span=50, adjust=False).mean()
df15["RSI14"] = calc_rsi(df15["cierre"], 14)
df15["precio_tipico"] = (df15["maximo"] + df15["minimo"] + df15["cierre"]) / 3
df15["tp_vol"] = df15["precio_tipico"] * df15["volumen"]
df15["fecha_dia"] = df15["fecha"].dt.date
df15["VWAP"] = (
    df15.groupby("fecha_dia")["tp_vol"].cumsum()
    / df15.groupby("fecha_dia")["volumen"].cumsum()
)

# ─────────────────────────────────────────
# SEÑALES
# ─────────────────────────────────────────
# LONG: cruce alcista EMA9/EMA21 + RSI 58-70 + cierre > VWAP + cierre > EMA50
df15["cruce_alcista"] = (
    (df15["EMA9"] > df15["EMA21"]) &
    (df15["EMA9"].shift(1) <= df15["EMA21"].shift(1))
)
df15["senal_long"] = (
    df15["cruce_alcista"] &
    (df15["RSI14"] > RSI_LONG_LOW) & (df15["RSI14"] < RSI_LONG_HIGH) &
    (df15["cierre"] > df15["VWAP"]) &
    (df15["cierre"] > df15["EMA50"])
)

# SHORT: cruce bajista EMA9/EMA21 + RSI 30-42 + cierre < VWAP + cierre < EMA50
df15["cruce_bajista"] = (
    (df15["EMA9"] < df15["EMA21"]) &
    (df15["EMA9"].shift(1) >= df15["EMA21"].shift(1))
)
df15["senal_short"] = (
    df15["cruce_bajista"] &
    (df15["RSI14"] > RSI_SHORT_LOW) & (df15["RSI14"] < RSI_SHORT_HIGH) &
    (df15["cierre"] < df15["VWAP"]) &
    (df15["cierre"] < df15["EMA50"])
)

# ─────────────────────────────────────────
# BACKTEST LONG + SHORT
# ─────────────────────────────────────────
capital = CAPITAL_INICIAL
en_trade = False
pending = False
direccion = None  # "long" o "short"
limit_price = None
expire_i = None
entry = stop = take = None
trades = []
equity_curve = [{"idx": 0, "capital": capital, "fecha": df15["fecha"].iloc[0]}]
fills = missed = 0

for i in range(1, len(df15)):
    row = df15.iloc[i]
    low   = float(row["minimo"])
    high  = float(row["maximo"])
    close = float(row["cierre"])

    # Verificar fill de orden pendiente
    if pending and not en_trade:
        if direccion == "long" and low <= limit_price:
            fills += 1
            entry = limit_price
            stop  = round(entry * (1 - STOP_PCT), 2)
            take  = round(entry * (1 + TAKE_PCT), 2)
            en_trade = True
            pending = False
        elif direccion == "short" and high >= limit_price:
            fills += 1
            entry = limit_price
            stop  = round(entry * (1 + STOP_PCT), 2)
            take  = round(entry * (1 - TAKE_PCT), 2)
            en_trade = True
            pending = False
        elif i >= expire_i:
            missed += 1
            pending = False

    # Gestión del trade abierto
    if en_trade:
        resultado = None
        pnl_bruto = 0.0

        if direccion == "long":
            if low <= stop:
                resultado = "STOP"
                pnl_bruto = -POS_USDT * STOP_PCT
            elif high >= take:
                resultado = "TAKE"
                pnl_bruto = POS_USDT * STOP_PCT * RR

        elif direccion == "short":
            if high >= stop:
                resultado = "STOP"
                pnl_bruto = -POS_USDT * STOP_PCT
            elif low <= take:
                resultado = "TAKE"
                pnl_bruto = POS_USDT * STOP_PCT * RR

        if resultado:
            fees = fees_roundtrip(POS_USDT, pnl_bruto)
            pnl_neto = pnl_bruto - fees
            capital += pnl_neto
            trades.append({
                "fecha": row["fecha"],
                "direccion": direccion.upper(),
                "resultado": resultado,
                "entry": entry,
                "pnl_bruto": round(pnl_bruto, 4),
                "fees": round(fees, 4),
                "pnl_neto": round(pnl_neto, 4),
                "capital": round(capital, 4),
            })
            equity_curve.append({"idx": len(trades), "capital": capital, "fecha": row["fecha"]})
            en_trade = False
            direccion = None
        continue

    # Buscar nueva señal (no entra si ya hay trade/pending)
    if not pending:
        if bool(row["senal_long"]):
            limit_price = round(close * (1 - PULLBACK_PCT), 2)
            expire_i = i + FILL_WINDOW
            pending = True
            direccion = "long"
        elif bool(row["senal_short"]):
            limit_price = round(close * (1 + PULLBACK_PCT), 2)
            expire_i = i + FILL_WINDOW
            pending = True
            direccion = "short"

# ─────────────────────────────────────────
# RESUMEN
# ─────────────────────────────────────────
df_tr = pd.DataFrame(trades)
df_eq = pd.DataFrame(equity_curve)

total      = len(df_tr)
longs      = df_tr[df_tr["direccion"] == "LONG"]
shorts     = df_tr[df_tr["direccion"] == "SHORT"]
wins       = int((df_tr["resultado"] == "TAKE").sum())
winrate    = round(wins / total * 100, 2) if total else 0
pnl_total  = round(float(df_tr["pnl_neto"].sum()), 2) if total else 0
fees_total = round(float(df_tr["fees"].sum()), 2) if total else 0
retorno    = round(pnl_total / CAPITAL_INICIAL * 100, 2)

wins_long  = int((longs["resultado"] == "TAKE").sum()) if len(longs) else 0
wins_short = int((shorts["resultado"] == "TAKE").sum()) if len(shorts) else 0
wr_long    = round(wins_long / len(longs) * 100, 2) if len(longs) else 0
wr_short   = round(wins_short / len(shorts) * 100, 2) if len(shorts) else 0
pnl_long   = round(float(longs["pnl_neto"].sum()), 2) if len(longs) else 0
pnl_short  = round(float(shorts["pnl_neto"].sum()), 2) if len(shorts) else 0

peak = CAPITAL_INICIAL
max_dd = 0.0
cap = CAPITAL_INICIAL
for t in trades:
    cap += t["pnl_neto"]
    if cap > peak:
        peak = cap
    dd = (peak - cap) / peak * 100
    if dd > max_dd:
        max_dd = dd

fill_rate = round(fills / (fills + missed) * 100, 2) if (fills + missed) else 0

summary = f"""
════════════════════════════════════════════════
BACKTEST LONG + SHORT — OKX FUTUROS (15m)
Periodo: {df15['fecha'].iloc[0]} → {df15['fecha'].iloc[-1]}
STOP: {STOP_PCT*100:.2f}% | TAKE: {TAKE_PCT*100:.2f}% | RR: {RR}
RSI Long: {RSI_LONG_LOW}-{RSI_LONG_HIGH} | RSI Short: {RSI_SHORT_LOW}-{RSI_SHORT_HIGH}
Fees OKX: maker {FEE_MAKER*100:.3f}% | taker {FEE_TAKER*100:.3f}%
════════════════════════════════════════════════
TOTAL TRADES : {total}
WINRATE TOTAL: {winrate}%
PnL NETO     : ${pnl_total}
FEES TOTAL   : ${fees_total}
RETORNO      : {retorno}%
MAX DRAWDOWN : {round(max_dd, 2)}%
FILL RATE    : {fill_rate}%

── LONGS ({len(longs)} trades) ──────────────────
  Winrate : {wr_long}%
  PnL neto: ${pnl_long}

── SHORTS ({len(shorts)} trades) ─────────────────
  Winrate : {wr_short}%
  PnL neto: ${pnl_short}

Capital inicial: ${CAPITAL_INICIAL}
Capital final  : ${round(capital, 2)}
════════════════════════════════════════════════
"""

print(summary)

with open("summary_long_short.txt", "w", encoding="utf-8") as f:
    f.write(summary)

df_tr.to_csv("trades_long_short.csv", index=False)
print("Guardado: summary_long_short.txt | trades_long_short.csv")

# ─────────────────────────────────────────
# GRÁFICA
# ─────────────────────────────────────────
if len(df_eq) > 1:
    fig = plt.figure(figsize=(16, 10))
    gs = gridspec.GridSpec(2, 2, figure=fig)

    # 1. Curva de equity
    ax1 = fig.add_subplot(gs[0, :])
    ax1.plot(df_eq["idx"], df_eq["capital"], linewidth=2, color="#2ecc71", label="Capital")
    ax1.axhline(y=CAPITAL_INICIAL, linestyle="--", color="gray", alpha=0.6, label="Capital inicial")

    # Marcar longs y shorts en la curva
    for t in trades:
        color = "#3498db" if t["direccion"] == "LONG" else "#e74c3c"
        idx = trades.index(t) + 1
        ax1.axvline(x=idx, color=color, alpha=0.15, linewidth=1)

    ax1.set_title(f"Curva de capital — Long+Short — 200 días | PnL: ${pnl_total} ({retorno}%)", fontsize=13)
    ax1.set_xlabel("Trade #")
    ax1.set_ylabel("Capital (USDT)")
    ax1.legend()
    ax1.grid(True, alpha=0.3)

    # 2. PnL por trade
    ax2 = fig.add_subplot(gs[1, 0])
    colores = []
    pnls = []
    for t in trades:
        pnls.append(t["pnl_neto"])
        if t["resultado"] == "TAKE":
            colores.append("#2ecc71")
        else:
            colores.append("#e74c3c")
    ax2.bar(range(1, total+1), pnls, color=colores, alpha=0.8)
    ax2.axhline(0, color="gray", linewidth=0.8)
    ax2.set_title("PnL por trade (verde=TAKE, rojo=STOP)")
    ax2.set_xlabel("Trade #")
    ax2.set_ylabel("PnL neto (USDT)")
    ax2.grid(axis="y", alpha=0.3)

    # 3. Comparación Long vs Short
    ax3 = fig.add_subplot(gs[1, 1])
    categorias = ["Longs", "Shorts"]
    pnls_comp = [pnl_long, pnl_short]
    trades_comp = [len(longs), len(shorts)]
    colors_comp = ["#3498db", "#e74c3c"]
    bars = ax3.bar(categorias, pnls_comp, color=colors_comp, alpha=0.8, width=0.4)
    for bar, n, wr in zip(bars, trades_comp, [wr_long, wr_short]):
        ax3.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.3,
                f"{n} trades\nWR {wr}%", ha="center", va="bottom", fontsize=10)
    ax3.axhline(0, color="gray", linewidth=0.8)
    ax3.set_title("PnL neto: Longs vs Shorts")
    ax3.set_ylabel("PnL neto (USDT)")
    ax3.grid(axis="y", alpha=0.3)

    plt.tight_layout()
    plt.savefig("backtest_long_short.png", dpi=150, bbox_inches="tight")
    plt.show()
    print("Guardado: backtest_long_short.png")