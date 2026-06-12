# BTC/USDT Trading Bot — Futures Long-Only

Proyecto personal de Python orientado a trading algorítmico. El bot analiza el par BTC/USDT en futuros perpetuos, genera señales de entrada y ejecuta órdenes automáticamente vía API.

---

## Contexto del proyecto

El desarrollo comenzó como un sistema de señales para ejecución manual en Binance (órdenes spot, capital inicial: $142). Al migrar a ejecución automática en un servidor DigitalOcean (Frankfurt), Binance bloqueó el acceso por restricciones regionales desde Colombia. Esto llevó a migrar la estrategia a **OKX Futures**, donde el bot opera actualmente en el par BTC/USDT con posiciones Long y Short.

El bot lleva operando en **live trading real** desde mayo 2026, con balance progresando de **$772 → $824+** durante las primeras semanas de operación. La estrategia opera únicamente en **posiciones Long**, respaldado por 1,460 días de backtesting histórico (jun 2022 → jun 2026).

---

## Estrategia

La lógica de entrada combina cuatro indicadores técnicos:

| Indicador | Rol |
|-----------|-----|
| EMA 9 / EMA 21 | Cruce para definir dirección de tendencia |
| EMA 50 | Confirmación de tendencia principal |
| RSI 14 | Filtro de sobrecompra / sobreventa |
| VWAP | Referencia de precio justo intradía |

**Temporalidad:** 15 minutos  
**Apalancamiento:** 3x Cross (configurado manualmente en OKX)  
**Par:** BTC/USDT Perpetual Futures

### Parámetros de entrada (Long-only)
| Parámetro | Valor |
|-----------|-------|
| RSI zona | 50–70 |
| Stop Loss | 2.00% |
| Take Profit | 3.00% |
| RR | 1.5 |

### Circuit breakers
| Control | Valor |
|---------|-------|
| Pérdida máxima diaria | $20 |
| Máx. trades por día | 2 |
| Drawdown total máximo | $140 |

---

## Backtesting — Long R1.5 (versión actual)

Backtest sobre **4 años de datos históricos** (jun 2022 → jun 2026), estrategia Long-only con RR 1.5.

| Métrica | Resultado |
|---------|-----------|
| Período | 2022-06-12 → 2026-06-11 |
| Total trades | 494 |
| Winrate | 45.55% |
| PnL neto | $1,048.88 |
| Fees totales | $101.92 |
| Retorno | 136.04% |
| Max Drawdown | 21.15% |
| Fill Rate | 41.41% |
| Capital inicial | $771.00 |
| Capital final | $1,819.88 |

> Fees OKX aplicados: maker 0.020% / taker 0.050%

Archivos incluidos:
- `Backtesting-Long-R1.5.py` — motor de backtesting con la estrategia final
- `Trades-Long-R1.5.csv` — registro detallado de cada operación
- `Summary_Long_R1.5.txt` — resumen de métricas

---

## Simulación Monte Carlo

Monte Carlo aplicada a los resultados del backtesting (`Trades-Long-R1.5.csv`), reordenando aleatoriamente los trades 10,000 veces para evaluar robustez estadística.

| Métrica | Resultado |
|---------|-----------|
| Capital inicial | $771.00 |
| Mediana simulada | ~$1,819 |
| Caso optimista (95%) | ~$2,300+ |
| Caso pesimista (5%) | ~$1,200+ |
| Probabilidad de ganancia | >95% |

- `monte_carlo_btc.py` — simulador Monte Carlo
- `monte_carlo_btc.png` — gráfica de 10,000 escenarios

---

## Bugs corregidos durante desarrollo live

Durante la fase de live trading se identificaron y corrigieron 6 bugs críticos:

| # | Bug | Solución |
|---|-----|----------|
| 1 | PnL calculado con balance post-trade | Se captura `balance_pre_trade` antes de la entrada y se persiste en `bot_state.json` |
| 2 | `POSICION_USDT` no usada en cálculo de qty | `qty = (POSICION_USDT × APALANCAMIENTO) / precio` |
| 3 | TP limit abría posición inversa en modo Cross | Se agregó `closePosition: True` a la orden TP |
| 4 | Rechazos por `MIN_QTY` de OKX | `POSICION_USDT` subido a $280 para cumplir mínimo |
| 5 | Posiciones 100x más pequeñas de lo esperado | `qty` debe dividirse por `CONTRACT_SIZE = 0.01` |
| 6 | `MAX_PERDIDA_DIARIA` se disparaba en SL normal | Límite subido de $16 → $20 (SL natural ~$16.80) |

---

## Estructura del repositorio

```
├── bot.py                    # Bot principal con ejecución de órdenes vía API OKX
├── seguridad.py              # Manejo seguro de credenciales y API keys
├── Backtesting-Long-R1.5.py  # Motor de backtesting — estrategia Long RR 1.5
├── Trades-Long-R1.5.csv      # Log de operaciones del backtest
├── Summary_Long_R1.5.txt     # Métricas resumen del backtest
├── Backtesting-Long-R1.5.png # Gráfica de resultados del backtesting
├── monte_carlo_btc.py        # Simulación Monte Carlo sobre los trades
└── monte_carlo_btc.png       # Gráfica de 10,000 escenarios Monte Carlo
```

---

## Infraestructura

- **Servidor:** DigitalOcean Droplet (Frankfurt)
- **Exchange:** OKX Futures — BTC/USDT Perpetual
- **Persistencia de estado:** `bot_state.json` (sobrevive reinicios)
- **Auto-restart:** crontab `@reboot`
- **Monitoreo:** `tail -f bot_trading.log`

---

## Tecnologías utilizadas

- **Python 3.x**
- `pandas` — manipulación de datos y cálculo de indicadores
- `numpy` — operaciones numéricas
- `matplotlib` — visualización de resultados
- `requests` — conexión con API de OKX

---

## Aprendizajes clave

- Implementación de indicadores técnicos desde cero sin librerías de TA
- Diseño de un motor de backtesting con gestión de riesgo
- Conexión y autenticación con APIs de exchanges (Binance → OKX)
- Diagnóstico y corrección de bugs en sistemas live con capital real
- Persistencia de estado en servidores remotos con JSON
- Simulación Monte Carlo para validación estadística de estrategias
- Despliegue en servidor remoto (DigitalOcean) con auto-restart

---

## Autor

**John Wuilfre Mujica** — Bogotá, Colombia  
Dios es Primero: Proyecto desarrollado de forma autodidacta como parte de un proceso de aprendizaje en Python aplicado al trading algorítmico.