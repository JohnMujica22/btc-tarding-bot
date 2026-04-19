# BTC/USDT Trading Bot — Futures Long/Short

Primer proyecto personal de Python orientado a trading algorítmico. El bot analiza el par BTC/USDT en futuros perpetuos, genera señales de entrada y ejecuta órdenes automáticamente vía API.

---

## Contexto del proyecto

El desarrollo comenzó como un sistema de señales para ejecución manual en Binance (órdenes spot, capital inicial: $142). Al migrar a ejecución automática en un servidor DigitalOcean (Frankfurt), Binance bloqueó el acceso por restricciones regionales desde Colombia. Esto llevó a migrar la estrategia a **OKX Futures**, donde el bot opera actualmente en el par BTC/USDT con posiciones Long y Short.

---

## Estrategia

La lógica de entrada combina cuatro indicadores técnicos:

| Indicador | Rol |
|-----------|-----|
| EMA 9 / EMA 21 | Cruce para definir dirección de tendencia |
| EMA 50 | Confirmación de tendencia principal |
| RSI 14 | Filtro de sobrecompra / sobreventa |
| VWAP | Referencia de precio justo intradía |

**Temporalidad elegida:** 15 minutos  
En backtesting comparativo (5 min vs 15 min, 200 días c/u), la temporalidad de 15 min generó menor cantidad de trades y mayor rentabilidad neta WINRATE TOTAL: 54.69%, ya que en 5 min las comisiones absorbían una parte significativa de las ganancias y el ruido de señales era mayor.

---

## Backtesting

Se realizaron más de 8 backtests sobre 200 días de datos históricos.  
El archivo `backtesting_futuros_long_short.py` contiene la versión final del backtest con los resultados incluidos en este repositorio.

Resultados incluidos:
- `graf_backtest_long_short.png` — curva de equity del backtest
- `trades_long_short.csv` — registro detallado de cada operación
- `summary_long_short.txt` — resumen de métricas (win rate, profit factor, drawdown)

---

## Estructura del repositorio

```
├── bot.py                            # Bot principal con ejecución de órdenes vía API OKX
├── backtesting_futuros_long_short.py # Motor de backtesting con la estrategia final
├── seguridad.py                      # Manejo seguro de credenciales y API keys
├── graf_backtest_long_short.png      # Gráfica de resultados del backtesting
├── trades_long_short.csv             # Log de operaciones del backtest
└── summary_long_short.txt            # Métricas resumen del backtest
```

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
- Análisis comparativo de estrategias por temporalidad
- Despliegue en servidor remoto (DigitalOcean)

---

## Autor

**John Wuilfre Mujica** — Bogotá, Colombia  
Dios es Primero: Proyecto desarrollado de forma autodidacta como parte de un proceso de aprendizaje en Python aplicado al trading algorítmico.