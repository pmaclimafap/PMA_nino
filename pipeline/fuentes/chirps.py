"""Adaptador de CHIRPS-GEFS, pronóstico de precipitación.

Combina el ensamble GEFS de la NOAA con la climatología de CHIRPS, a 0,05°
(unos 5,6 km) y 16 días de horizonte. Es el **único producto del conjunto ya
corregido por sesgo**, mediante emparejamiento de cuantiles contra la
climatología de CHIRPS. Para lluvia, la variable donde los modelos peor se
portan, tenerla calibrada de origen vale mucho.

Ruta (versión 3, verificada el 1-oct-2026):

    .../CHIRPS-GEFS/v3/daily/global/<AAAA>/<MM>/<DD>/c3g_<AAAA>.<MM>.<DD>.tif
                                    └── corrida ──┘        └── objetivo ──┘

La carpeta lleva la fecha de la CORRIDA y el archivo la del DÍA OBJETIVO.
Dentro de una carpeta hay 16 archivos, uno por día de horizonte.

LECTURA PARCIAL: cada GeoTIFF es global (7200 × 2400, de 60°S a 60°N) y pesa
69 MB. Descargar los 16 serían 1,1 GB diarios para leer siete puntos. En vez
de eso se abren por `/vsicurl/` y GDAL pide solo las filas necesarias con
rangos HTTP: unos pocos cientos de KB por archivo. El ráster no está
tileado, sino organizado por filas de 7200 celdas (28,8 KB cada una), lo que
para siete puntos colombianos significa leer un puñado de filas.

Publicación observada: hacia las 08:25 UTC.
"""

from __future__ import annotations

import concurrent.futures
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import pandas as pd

BASE = "https://data.chc.ucsb.edu/products/CHIRPS-GEFS/v3/daily/global"
AGENTE = "PMA-nino-Colombia/1.0 (pipeline agroclimatico no comercial)"

HORIZONTE_DIAS = 16
REINTENTOS = 3
ESPERA_BASE_S = 5
# Cada archivo tarda unos 4-5 s por los viajes HTTP, no por volumen. Leerlos
# en paralelo baja la corrida de ~75 s a ~15 s, y vuelve viable rellenar
# histórico (16 archivos x 180 días serían horas en secuencia).
HILOS = 6

# Sin esto GDAL lista el directorio remoto entero en cada apertura y pide
# rangos innecesarios. Con ambas, solo descarga los bytes que hacen falta.
ENTORNO_GDAL = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
    "GDAL_HTTP_MAX_RETRY": "3",
    "GDAL_HTTP_RETRY_DELAY": "5",
}


class SinPublicar(RuntimeError):
    """La corrida de esa fecha todavía no está en el servidor."""


@dataclass(frozen=True)
class Punto:
    id: str
    lat: float
    lon: float


def _configurar_gdal() -> None:
    for clave, valor in ENTORNO_GDAL.items():
        os.environ.setdefault(clave, valor)


def url(corrida: date, objetivo: date) -> str:
    return (
        f"{BASE}/{corrida.year}/{corrida.month:02d}/{corrida.day:02d}/"
        f"c3g_{objetivo.year}.{objetivo.month:02d}.{objetivo.day:02d}.tif"
    )


def existe(corrida: date) -> bool:
    """¿Está publicada esa corrida? Consulta la cabecera del primer archivo."""
    peticion = urllib.request.Request(
        url(corrida, corrida), headers={"User-Agent": AGENTE}, method="HEAD"
    )
    try:
        with urllib.request.urlopen(peticion, timeout=30) as r:
            return r.status == 200
    except Exception:
        return False


def ultima_disponible(desde: date | None = None, max_dias: int = 3) -> date:
    """Corrida más reciente publicada, retrocediendo si hace falta.

    Una corrida de ayer sigue cubriendo quince de los dieciséis días hacia
    adelante: mucho mejor que quedarse sin la única fuente de lluvia ya
    corregida por sesgo.
    """
    desde = desde or date.today()
    for i in range(max_dias):
        candidata = desde - timedelta(days=i)
        if existe(candidata):
            return candidata
    raise SinPublicar(
        f"No hay corrida de CHIRPS-GEFS entre "
        f"{desde - timedelta(days=max_dias - 1)} y {desde}."
    )


def _leer_puntos(ruta: str, puntos: list[Punto]) -> list[float | None]:
    """Abre un GeoTIFF remoto y muestrea los puntos, con reintentos."""
    import rasterio

    coords = [(p.lon, p.lat) for p in puntos]
    ultimo: Exception | None = None

    for intento in range(1, REINTENTOS + 1):
        try:
            with rasterio.open(f"/vsicurl/{ruta}") as r:
                nodata = r.nodata
                crudos = [float(v[0]) for v in r.sample(coords)]
            salida: list[float | None] = []
            for v in crudos:
                if v != v or (nodata is not None and v == nodata):
                    salida.append(None)
                # CHIRPS marca el mar y los huecos con negativos grandes
                elif v < -100:
                    salida.append(None)
                else:
                    salida.append(max(0.0, round(v, 2)))
            return salida
        except Exception as e:
            ultimo = e
            if intento < REINTENTOS:
                time.sleep(ESPERA_BASE_S * intento)

    raise RuntimeError(f"No se pudo leer {ruta} tras {REINTENTOS} intentos: {ultimo}")


def pronostico(puntos: list[Punto], corrida: date | None = None,
               dias: int = HORIZONTE_DIAS
               ) -> tuple[pd.DataFrame, datetime, list[str]]:
    """Pronóstico de lluvia para los puntos, día a día.

    Devuelve (DataFrame largo, emisión, avisos).

    Un día que falte no detiene la corrida: se registra como aviso y los
    demás se devuelven. El horizonte lejano es el más propenso a faltar y es
    el menos crítico.
    """
    _configurar_gdal()
    corrida = corrida or ultima_disponible()
    emision = datetime(corrida.year, corrida.month, corrida.day,
                       tzinfo=timezone.utc)

    objetivos = [corrida + timedelta(days=i) for i in range(dias)]
    filas: list[dict] = []
    avisos: list[str] = []

    def leer(objetivo: date):
        return objetivo, _leer_puntos(url(corrida, objetivo), puntos)

    with concurrent.futures.ThreadPoolExecutor(max_workers=HILOS) as pool:
        tareas = {pool.submit(leer, o): o for o in objetivos}
        for tarea in concurrent.futures.as_completed(tareas):
            objetivo = tareas[tarea]
            try:
                _, valores = tarea.result()
            except Exception as e:
                avisos.append(f"{objetivo}: {str(e)[:120]}")
                continue
            for punto, valor in zip(puntos, valores):
                if valor is None:
                    continue
                filas.append({
                    "municipio": punto.id,
                    "variable": "precipitacion",
                    "modelo": "chirps_gefs",
                    "objetivo": objetivo,
                    "valor": valor,
                })

    df = pd.DataFrame(filas)
    if not df.empty:
        df = df.sort_values(["municipio", "objetivo"]).reset_index(drop=True)
    return df, emision, avisos
