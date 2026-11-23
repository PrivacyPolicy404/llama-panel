"""Energía de la GPU acumulada por día, a partir de los watts que muestrea hardware.py. Se guarda en
CFG_DIR/energia.json para sobrevivir a los reinicios del panel.

Solo la GPU: es lo único que informa su consumo sin root (amdgpu en hwmon, nvidia-smi). El de la CPU (RAPL) lo lee
solo root, y lo del enchufe no lo sabe nadie. El costo sale de LLAMA_KWH_PRICE (paths.env, pestaña Configuración).
"""
import json
import os
import threading
import time
from datetime import datetime

from . import config as C

MAX_GAP = 30      # s: un hueco más largo entre muestras (panel apagado, suspensión) no se cuenta
SAVE_EVERY = 60   # s
KEEP_DAYS = 62


def price():
    """Precio del kWh o None si no está configurado (o no es un número)."""
    raw = (os.environ.get("LLAMA_KWH_PRICE") or C._LOCAL.get("LLAMA_KWH_PRICE", "")).strip().replace(",", ".")
    try:
        v = float(raw)
    except ValueError:
        return None
    return v if v >= 0 else None


class Meter:
    def __init__(self, path=None):
        self.path = path or (C.CFG_DIR / "energia.json")
        self.lock = threading.Lock()
        self.last_t = None
        self.saved_t = 0.0
        try:
            self.days = {k: float(v) for k, v in json.loads(self.path.read_text()).get("days", {}).items()}
        except (OSError, ValueError, AttributeError):
            self.days = {}

    def add(self, watts, now=None):
        """Suma la energía desde la muestra anterior (en Wh), al día local de `now`."""
        now = now or time.time()
        with self.lock:
            prev, self.last_t = self.last_t, now
            if watts is None or prev is None or not 0 < now - prev <= MAX_GAP:
                return
            day = datetime.fromtimestamp(now).strftime("%Y-%m-%d")
            self.days[day] = self.days.get(day, 0.0) + watts * (now - prev) / 3600
            if len(self.days) > KEEP_DAYS:
                for k in sorted(self.days)[:-KEEP_DAYS]:
                    del self.days[k]
            if now - self.saved_t >= SAVE_EVERY:
                self.saved_t = now
                self._save()

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"days": {k: round(v, 3) for k, v in self.days.items()}}))
            tmp.replace(self.path)
        except OSError:
            pass

    def summary(self, now=None):
        d = datetime.fromtimestamp(now or time.time())
        today, month = d.strftime("%Y-%m-%d"), d.strftime("%Y-%m")
        with self.lock:
            day_kwh = self.days.get(today, 0.0) / 1000
            month_kwh = sum(v for k, v in self.days.items() if k.startswith(month)) / 1000
            month_days = [round(self.days.get(f"{month}-{i:02d}", 0.0) / 1000, 4) for i in range(1, d.day + 1)]
        p = price()
        return {"day_kwh": round(day_kwh, 4), "month_kwh": round(month_kwh, 4), "month_days": month_days,
                "price": p, "day_cost": None if p is None else round(day_kwh * p, 2),
                "month_cost": None if p is None else round(month_kwh * p, 2)}


METER = None


def meter():
    global METER
    if METER is None:
        METER = Meter()
    return METER
