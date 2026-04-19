import os
import logging
from dotenv import load_dotenv
from datetime import datetime

# ─────────────────────────────────────────
# CONFIGURACIÓN DE LOGS
# ─────────────────────────────────────────
logging.Formatter.converter = lambda *args: __import__('datetime').datetime.now(
    __import__('pytz').timezone('America/Bogota')).timetuple()
logging.basicConfig(
    filename="bot_trading.log",
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)

def log(mensaje, nivel="info"):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {mensaje}")
    if nivel == "info":
        logging.info(mensaje)
    elif nivel == "error":
        logging.error(mensaje)
    elif nivel == "warning":
        logging.warning(mensaje)

# ─────────────────────────────────────────
# CARGA DE VARIABLES DE ENTORNO
# ─────────────────────────────────────────
load_dotenv()

def obtener_credenciales():
    api_key    = os.getenv("OKX_API_KEY")
    secret     = os.getenv("OKX_SECRET")
    passphrase = os.getenv("OKX_PASSPHRASE")

    if not api_key or not secret or not passphrase:
        log("ERROR: Credenciales OKX no encontradas en .env", "error")
        raise ValueError("Configura OKX_API_KEY, OKX_SECRET y OKX_PASSPHRASE en .env")

    log("Credenciales OKX cargadas correctamente")
    return api_key, secret, passphrase

# ─────────────────────────────────────────
# CIRCUIT BREAKER
# ─────────────────────────────────────────
class CircuitBreaker:
    def __init__(self):
        self.perdida_diaria = 0
        self.max_perdida    = float(os.getenv("MAX_PERDIDA_DIARIA", 10))
        self.trades_hoy     = 0
        self.max_trades_dia = int(os.getenv("MAX_TRADES_DIA", 10))
        self.activo         = True
        self.fecha_hoy      = datetime.now().date()

    def resetear_si_nuevo_dia(self):
        hoy = datetime.now().date()
        if hoy != self.fecha_hoy:
            self.perdida_diaria = 0
            self.trades_hoy     = 0
            self.activo         = True
            self.fecha_hoy      = hoy
            log("Circuit breaker reseteado — nuevo día")

    def registrar_trade(self, pnl_neto):
        self.trades_hoy += 1
        if pnl_neto < 0:
            self.perdida_diaria += abs(pnl_neto)
        if self.perdida_diaria >= self.max_perdida:
            self.activo = False
            log(f"🔴 CIRCUIT BREAKER — Pérdida diaria ${self.perdida_diaria:.2f} >= ${self.max_perdida}", "error")
        if self.trades_hoy >= self.max_trades_dia:
            self.activo = False
            log(f"🔴 CIRCUIT BREAKER — Máximo {self.max_trades_dia} trades diarios alcanzado", "error")

    def puede_operar(self):
        self.resetear_si_nuevo_dia()
        if not self.activo:
            log("⚠️ Circuit breaker activo — bot pausado hoy", "warning")
        return self.activo

# ─────────────────────────────────────────
# MANEJADOR DE ERRORES
# ─────────────────────────────────────────
import time

def reintentar(funcion, intentos=3, espera=5):
    for intento in range(intentos):
        try:
            return funcion()
        except Exception as e:
            log(f"Error intento {intento + 1}/{intentos}: {e}", "error")
            if intento < intentos - 1:
                log(f"Reintentando en {espera} segundos...")
                time.sleep(espera)
            else:
                log("Todos los intentos fallaron", "error")