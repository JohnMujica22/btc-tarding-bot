import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

# =============================================================
# SIMULACION MONTE CARLO — Bot BTC/USDT Futuros Long/Short
# Wil Mujica | github.com/JohnMujica22
# =============================================================
# Aarchivo de trades del backtesting y simula 10,000
# escenarios reordenando aleatoriamente los resultados para
# evaluar la robustez real de la estrategia.
# =============================================================

# --- Configuracion ---
ARCHIVO_TRADES  = 'trades_long_short.csv'
CAPITAL_INICIAL = 142.0
N_SIMULACIONES  = 10000
SEMILLA         = 42

# --- Cargar datos ---
df  = pd.read_csv(ARCHIVO_TRADES)
pnl = df['pnl_neto'].values
n_trades = len(pnl)

np.random.seed(SEMILLA)

# --- Simulaciones ---
curvas             = []
resultados_finales = []

for _ in range(N_SIMULACIONES):
    muestra = np.random.choice(pnl, size=n_trades, replace=True)
    curva   = CAPITAL_INICIAL + np.cumsum(muestra)
    curva   = np.insert(curva, 0, CAPITAL_INICIAL)
    curvas.append(curva)
    resultados_finales.append(curva[-1])

curvas             = np.array(curvas)
resultados_finales = np.array(resultados_finales)

# --- Percentiles ---
p5  = np.percentile(curvas,  5, axis=0)
p50 = np.percentile(curvas, 50, axis=0)
p95 = np.percentile(curvas, 95, axis=0)

# --- Curva real ---
capital_real = CAPITAL_INICIAL + np.cumsum(pnl)
capital_real = np.insert(capital_real, 0, CAPITAL_INICIAL)

# --- Metricas ---
prob_ganancia = np.mean(resultados_finales > CAPITAL_INICIAL) * 100
prob_perdida  = np.mean(resultados_finales < CAPITAL_INICIAL) * 100
mediana_final = np.median(resultados_finales)
peor_caso     = np.percentile(resultados_finales,  5)
mejor_caso    = np.percentile(resultados_finales, 95)

def max_drawdown(curva):
    peak = np.maximum.accumulate(curva)
    dd   = (curva - peak) / peak * 100
    return dd.min()

drawdowns   = [max_drawdown(c) for c in curvas]
dd_promedio = np.mean(drawdowns)
dd_peor     = np.percentile(drawdowns, 5)

print("=" * 50)
print("  RESULTADOS MONTE CARLO")
print("=" * 50)
print(f"  Trades analizados     : {n_trades}")
print(f"  Capital inicial       : ${CAPITAL_INICIAL:.2f}")
print(f"  Capital real final    : ${capital_real[-1]:.2f}")
print(f"  Mediana simulada      : ${mediana_final:.2f}")
print(f"  Caso optimista (p95)  : ${mejor_caso:.2f}")
print(f"  Caso pesimista (p5)   : ${peor_caso:.2f}")
print(f"  Prob. de ganancia     : {prob_ganancia:.1f}%")
print(f"  Prob. de perdida      : {prob_perdida:.1f}%")
print(f"  Drawdown promedio     : {dd_promedio:.1f}%")
print(f"  Drawdown peor caso    : {dd_peor:.1f}%")
print("=" * 50)

# --- Grafica ---
fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 9))
fig.patch.set_facecolor('#0d1117')
for ax in [ax1, ax2]:
    ax.set_facecolor('#161b22')
    ax.tick_params(colors='#8b949e')
    ax.spines['bottom'].set_color('#30363d')
    ax.spines['left'].set_color('#30363d')
    ax.spines['top'].set_visible(False)
    ax.spines['right'].set_visible(False)

x = np.arange(n_trades + 1)

for i in range(300):
    ax1.plot(x, curvas[i], color='#58a6ff', alpha=0.03, linewidth=0.5)

ax1.fill_between(x, p5, p95, color='#58a6ff', alpha=0.15, label='Rango 5%-95%')
ax1.plot(x, p50, color='#58a6ff', linewidth=1.5, linestyle='--', label='Mediana simulada')
ax1.plot(x, capital_real, color='#3fb950', linewidth=2.2, label='Resultado real')
ax1.axhline(CAPITAL_INICIAL, color='#8b949e', linewidth=0.8, linestyle=':')
ax1.set_title('Simulacion Monte Carlo — 10,000 escenarios', color='#e6edf3', fontsize=13, pad=12)
ax1.set_ylabel('Capital (USD)', color='#8b949e', fontsize=10)
ax1.set_xlabel('Numero de trades', color='#8b949e', fontsize=10)
ax1.legend(facecolor='#21262d', edgecolor='#30363d', labelcolor='#e6edf3', fontsize=9)
ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f'${v:.0f}'))

ax2.hist(resultados_finales, bins=80, color='#58a6ff', alpha=0.7, edgecolor='none')
ax2.axvline(CAPITAL_INICIAL,    color='#8b949e', linewidth=1.0, linestyle=':', label=f'Capital inicial ${CAPITAL_INICIAL:.0f}')
ax2.axvline(mediana_final,      color='#58a6ff', linewidth=1.5, linestyle='--', label=f'Mediana ${mediana_final:.0f}')
ax2.axvline(capital_real[-1],   color='#3fb950', linewidth=2.0, label=f'Resultado real ${capital_real[-1]:.0f}')
ax2.axvline(peor_caso,          color='#f85149', linewidth=1.2, linestyle='--', label=f'Peor 5% ${peor_caso:.0f}')
ax2.axvline(mejor_caso,         color='#ffa657', linewidth=1.2, linestyle='--', label=f'Mejor 95% ${mejor_caso:.0f}')
ax2.set_title('Distribucion de resultados finales', color='#e6edf3', fontsize=13, pad=12)
ax2.set_xlabel('Capital final (USD)', color='#8b949e', fontsize=10)
ax2.set_ylabel('Frecuencia', color='#8b949e', fontsize=10)
ax2.legend(facecolor='#21262d', edgecolor='#30363d', labelcolor='#e6edf3', fontsize=8)
ax2.xaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f'${v:.0f}'))

stats = (f"  Prob. ganancia: {prob_ganancia:.1f}%   |   "
         f"Drawdown prom.: {dd_promedio:.1f}%   |   "
         f"Drawdown peor caso: {dd_peor:.1f}%  ")
fig.text(0.5, 0.01, stats, ha='center', color='#8b949e', fontsize=9,
         bbox=dict(facecolor='#21262d', edgecolor='#30363d', boxstyle='round,pad=0.4'))

plt.tight_layout(rect=[0, 0.04, 1, 1])
plt.savefig('monte_carlo_btc.png', dpi=150, bbox_inches='tight', facecolor='#0d1117')
print("  Grafica guardada: monte_carlo_btc.png")