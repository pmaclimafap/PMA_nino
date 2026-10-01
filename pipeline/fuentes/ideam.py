"""Adaptador del WRF-IDEAM.

El IDEAM publica la corrida 00Z como un ZIP de GeoTIFF en un índice Apache,
sin API. La convención del archivo diario es:

    https://bart.ideam.gov.co/wrfideam/new_modelo/WRF00COLOMBIA/tif/
        geoTIFF<DDMMAAAA>00Z.zip                ~9,6 MB  · diario, 7 variables
        geoTIFF<var>horario<DDMMAAAA>00Z.zip    22-37 MB · horario, 1 variable

Se usa el DIARIO, no los horarios. Trae TMAX_C, TMIN_C, TMED_C,
ACUM24HPREC_MM, VIENTOMAX_MPS, VIENTOMED_MPS y ET0_mm ya agregadas, con 7
días por variable, en un archivo cuatro veces más liviano que uno solo de
los horarios. La contrapartida: NO incluye humedad relativa, que solo está
en el ZIP horario de 32 MB.

Dentro del ZIP, un GeoTIFF por variable y día objetivo:

    <VARIABLE>_<DDMMAAAA corrida>_fcst_<DDMMAAAA objetivo>.tif

Malla de 249 × 249 celdas a 0,09° (unos 10 km), EPSG:4326, cubriendo de
85,4°O a 62,9°O y de 6,5°S a 15,8°N.

Publicación hacia las 09:30 hora local (unas 14:30 UTC) y retención de unas
seis semanas, así que una corrida perdida se puede recuperar al día
siguiente.
"""

from __future__ import annotations

import re
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

BASE = "https://bart.ideam.gov.co/wrfideam/new_modelo/WRF00COLOMBIA/tif"
AGENTE = "PMA-nino-Colombia/1.0 (pipeline agroclimatico no comercial)"

TIEMPO_ESPERA_S = 180        # el ZIP pesa ~10 MB y el servidor no es rápido
REINTENTOS = 3
ESPERA_BASE_S = 10
TAMANO_MINIMO_BYTES = 1_000_000

# Nombre dentro del ZIP diario -> id interno del pipeline.
# El viento y ET0 están disponibles pero no se guardan: el viento solo se
# muestra vía el mapa de Windy, y ET0 quedó fuera para mantener las mismas
# cuatro variables del monitoreo.
VARIABLES_DIARIAS = {
    "TMAX_C": "tmax",
    "TMIN_C": "tmin",
    "ACUM24HPREC_MM": "precipitacion",
}

# La humedad relativa NO está en el ZIP diario. Solo en el horario, que pesa
# unos 32 MB y trae 168 rásteres (7 días x 24 h). Se baja aparte y se agrega
# al día local con el mismo criterio que usamos en Open-Meteo, para que
# ningún modelo termine con una definición distinta de "la humedad del
# martes".
VARIABLE_HORARIA = "humedad"

# Convención real del ZIP horario, verificada sobre el archivo del 1-oct-2026:
#
#     RH1H_<DDMMAAAA corrida>_fcst_DIA<d><hh>HLC.tif
#
# donde d es el día del pronóstico (1 a 7, siendo 1 el de la corrida) y hh
# la hora (00 a 23). El sufijo HLC significa HORA LOCAL COLOMBIA: los datos
# YA vienen en hora local, así que NO hay que desplazar de UTC. Hacerlo
# correría el día completo.

PATRON = re.compile(
    r"^(?P<var>.+?)_(?P<corrida>\d{8})_fcst_(?P<objetivo>\d{8})\.tif$"
)

# El ZIP horario no se ha inspeccionado todavía, así que el nombre puede
# traer la hora de varias formas. Se prueban las plausibles y, si ninguna
# encaja, inspeccionar_horario() imprime los nombres reales para ajustar.
PATRON_HORARIO = re.compile(
    r"^(?P<var>.+?)_(?P<corrida>\d{8})_fcst_DIA(?P<dia>\d)(?P<hora>\d{2})HLC\.tif$",
    re.IGNORECASE,
)


class SinPublicar(RuntimeError):
    """La corrida de esa fecha todavía no está en el servidor."""


@dataclass(frozen=True)
class Punto:
    id: str
    lat: float
    lon: float


def _ddmmaaaa(f: date) -> str:
    return f"{f.day:02d}{f.month:02d}{f.year}"


def _fecha(texto: str) -> date:
    return datetime.strptime(texto, "%d%m%Y").date()


def url_diario(corrida: date) -> str:
    return f"{BASE}/geoTIFF{_ddmmaaaa(corrida)}00Z.zip"


def url_horaria(corrida: date, variable: str = VARIABLE_HORARIA) -> str:
    return f"{BASE}/geoTIFF{variable}horario{_ddmmaaaa(corrida)}00Z.zip"


def descargar(corrida: date, destino: Path, url: str | None = None,
              minimo: int = TAMANO_MINIMO_BYTES) -> Path:
    """Descarga un ZIP de una corrida, con reintentos.

    Un 404 significa que todavía no la publicaron, y eso no es un error.
    """
    url = url or url_diario(corrida)
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    peticion = urllib.request.Request(url, headers={"User-Agent": AGENTE})

    ultimo: Exception | None = None
    for intento in range(1, REINTENTOS + 1):
        parcial = destino.with_suffix(".parcial")
        try:
            with urllib.request.urlopen(peticion, timeout=TIEMPO_ESPERA_S) as r:
                parcial.write_bytes(r.read())

            tam = parcial.stat().st_size
            if tam < minimo:
                raise IOError(
                    f"El ZIP de {corrida} pesa solo {tam} bytes; "
                    "probablemente llegó truncado o es una página de error."
                )
            # Escritura atómica: si el proceso muere a mitad, no queda un
            # archivo truncado que mañana se dé por bueno.
            parcial.replace(destino)
            return destino

        except urllib.error.HTTPError as e:
            parcial.unlink(missing_ok=True)
            if e.code == 404:
                raise SinPublicar(
                    f"La corrida del {corrida} aún no está publicada "
                    "(el IDEAM publica hacia las 14:30 UTC)."
                ) from e
            ultimo = RuntimeError(f"HTTP {e.code} al pedir {url}")
        except Exception as e:
            parcial.unlink(missing_ok=True)
            ultimo = e

        if intento < REINTENTOS:
            time.sleep(ESPERA_BASE_S * intento)

    raise RuntimeError(f"No se pudo descargar {url} tras {REINTENTOS} intentos: {ultimo}")


def listar_contenido(ruta_zip: Path) -> list[dict]:
    """Inventario del ZIP: qué variable, qué corrida y para qué día."""
    salida = []
    with zipfile.ZipFile(ruta_zip) as z:
        for nombre in z.namelist():
            base = Path(nombre).name
            m = PATRON.match(base)
            if not m:
                continue
            salida.append({
                "archivo": nombre,
                "variable_ideam": m.group("var"),
                "corrida": _fecha(m.group("corrida")),
                "objetivo": _fecha(m.group("objetivo")),
            })
    return salida


def verificar_corrida(ruta_zip: Path, esperada: date) -> None:
    """Comprueba que el ZIP contenga de verdad la corrida pedida.

    Es la verificación fuerte: no depende de cabeceras HTTP ni de la hora
    del servidor, sino del nombre de los archivos que vienen dentro. Si el
    IDEAM republicara una corrida vieja con fecha de hoy, o si un proxy
    sirviera una copia en caché, esto lo detecta.
    """
    inventario = listar_contenido(ruta_zip)
    if not inventario:
        raise RuntimeError(
            f"{ruta_zip.name} no contiene archivos con la convención esperada."
        )
    corridas = {x["corrida"] for x in inventario}
    if corridas != {esperada}:
        raise RuntimeError(
            f"Se pidió la corrida del {esperada} pero el ZIP contiene "
            f"{sorted(corridas)}. No se procesa: el contenido no corresponde."
        )


def publicado_hoy(corrida: date) -> tuple[bool, str]:
    """¿La cabecera Last-Modified corresponde a la fecha de la corrida?

    Verificación débil y complementaria: depende de la hora del servidor,
    que puede estar en UTC o en hora de Colombia. Sirve para avisar, no
    para decidir. La decisión la toma verificar_corrida() sobre el
    contenido.
    """
    peticion = urllib.request.Request(
        url_diario(corrida), headers={"User-Agent": AGENTE}, method="HEAD"
    )
    try:
        with urllib.request.urlopen(peticion, timeout=30) as r:
            cabecera = r.headers.get("Last-Modified", "")
    except Exception as e:
        return False, f"no se pudo consultar: {e}"

    if not cabecera:
        return False, "el servidor no reporta Last-Modified"

    from email.utils import parsedate_to_datetime
    try:
        cuando = parsedate_to_datetime(cabecera)
    except (TypeError, ValueError):
        return False, f"Last-Modified ilegible: {cabecera}"

    # Se acepta el día de la corrida o el siguiente, porque la diferencia
    # entre UTC y la hora de Colombia puede cruzar la medianoche.
    from datetime import timedelta
    ok = cuando.date() in (corrida, corrida + timedelta(days=1))
    return ok, cuando.isoformat()


def inspeccionar_horario(ruta_zip: Path, muestra: int = 10) -> list[str]:
    """Nombres reales dentro del ZIP horario.

    El ZIP horario no se ha inspeccionado, así que PATRONES_HORARIOS es una
    conjetura. Esta función imprime los nombres para ajustarla si ninguno
    encaja.
    """
    with zipfile.ZipFile(ruta_zip) as z:
        nombres = [Path(n).name for n in z.namelist() if n.endswith(".tif")]
    return sorted(nombres)[:muestra]


def _parsear_horario(nombre: str, corrida: date | None = None) -> dict | None:
    """Lee un nombre del ZIP horario.

    El día objetivo no viene como fecha sino como número relativo a la
    corrida: DIA1 es el día de la corrida, DIA7 el séptimo.
    """
    from datetime import timedelta

    m = PATRON_HORARIO.match(nombre)
    if not m:
        return None
    inicio = _fecha(m.group("corrida"))
    return {
        "variable_ideam": m.group("var"),
        "corrida": inicio,
        "objetivo": inicio + timedelta(days=int(m.group("dia")) - 1),
        "hora": int(m.group("hora")),
    }


def extraer_humedad(ruta_zip: Path, puntos: list[Punto]) -> pd.DataFrame:
    """Humedad relativa agregada al día local desde el ZIP horario.

    Son 168 rásteres (7 días x 24 h). Se agrega a media y máxima diarias con
    el mismo criterio que Open-Meteo.

    Los GeoTIFF vienen marcados HLC (hora local Colombia), así que NO se
    desplaza nada: el día del archivo ya es el día del proyecto. Desplazar
    desde UTC correría el pronóstico un día completo.
    """
    import rasterio

    with zipfile.ZipFile(ruta_zip) as z:
        archivos = [n for n in z.namelist() if n.endswith(".tif")]

    inventario = []
    for nombre in archivos:
        info = _parsear_horario(Path(nombre).name)
        if info:
            info["archivo"] = nombre
            inventario.append(info)

    if not inventario:
        raise RuntimeError(
            "Ningún archivo del ZIP horario encaja con los patrones conocidos. "
            "Correr inspeccionar_horario() para ver los nombres reales. "
            f"Ejemplos: {sorted(Path(n).name for n in archivos)[:3]}"
        )

    coords = [(p.lon, p.lat) for p in puntos]
    filas: list[dict] = []

    with zipfile.ZipFile(ruta_zip) as z, tempfile.TemporaryDirectory() as tmp:
        for item in inventario:
            destino = Path(tmp) / Path(item["archivo"]).name
            destino.write_bytes(z.read(item["archivo"]))
            try:
                with rasterio.open(destino) as r:
                    muestras = list(r.sample(coords))
                    nodata = r.nodata
            finally:
                destino.unlink(missing_ok=True)

            dia_local = item["objetivo"]

            for punto, valor in zip(puntos, muestras):
                v = float(valor[0])
                if (nodata is not None and v == nodata) or v != v:
                    continue
                filas.append({"municipio": punto.id, "dia": dia_local, "valor": v})

    if not filas:
        return pd.DataFrame()

    tabla = pd.DataFrame(filas)
    # Un día con pocas horas daría una media sesgada hacia las que sí tiene.
    # Con HLC los siete días vienen completos, pero la guarda se queda por
    # si una corrida llega recortada.
    cuenta = tabla.groupby(["municipio", "dia"]).valor.count()
    completos = cuenta[cuenta >= 20].index

    agregado = (
        tabla.set_index(["municipio", "dia"]).loc[completos]
        .groupby(["municipio", "dia"]).valor.agg(media="mean", maxima="max")
        .reset_index()
    )

    salida = []
    for fila in agregado.to_dict("records"):
        for columna, variable in (("media", "humedad_relativa"),
                                  ("maxima", "humedad_relativa_max")):
            salida.append({
                "municipio": fila["municipio"], "variable": variable,
                "modelo": "wrf_ideam", "objetivo": fila["dia"],
                "valor": round(float(fila[columna]), 1),
            })
    return pd.DataFrame(salida)


def extraer(ruta_zip: Path, puntos: list[Punto],
            variables: dict[str, str] | None = None) -> pd.DataFrame:
    """Extrae los puntos de todos los GeoTIFF relevantes del ZIP.

    Se leen directamente desde el ZIP sin descomprimirlo entero: solo se
    extraen los archivos de las variables que interesan, que son 21 de 49.
    """
    import rasterio

    variables = variables or VARIABLES_DIARIAS
    inventario = [
        x for x in listar_contenido(ruta_zip)
        if x["variable_ideam"] in variables
    ]
    if not inventario:
        raise RuntimeError(
            f"El ZIP no contiene ninguna de las variables esperadas "
            f"({sorted(variables)}). ¿Cambió la convención de nombres?"
        )

    coords = [(p.lon, p.lat) for p in puntos]
    filas: list[dict] = []

    with zipfile.ZipFile(ruta_zip) as z, tempfile.TemporaryDirectory() as tmp:
        for item in inventario:
            destino = Path(tmp) / Path(item["archivo"]).name
            destino.write_bytes(z.read(item["archivo"]))
            try:
                with rasterio.open(destino) as r:
                    muestras = list(r.sample(coords))
                    nodata = r.nodata
                for punto, valor in zip(puntos, muestras):
                    v = float(valor[0])
                    if nodata is not None and v == nodata:
                        continue
                    if v != v:          # NaN
                        continue
                    filas.append({
                        "municipio": punto.id,
                        "variable": variables[item["variable_ideam"]],
                        "modelo": "wrf_ideam",
                        "objetivo": item["objetivo"],
                        "valor": round(v, 2),
                    })
            finally:
                destino.unlink(missing_ok=True)

    return pd.DataFrame(filas)


def existe(corrida: date) -> bool:
    """¿Está publicada esa corrida? Consulta la cabecera, no descarga."""
    peticion = urllib.request.Request(
        url_diario(corrida), headers={"User-Agent": AGENTE}, method="HEAD"
    )
    try:
        with urllib.request.urlopen(peticion, timeout=30) as r:
            return r.status == 200
    except Exception:
        return False


def ultima_disponible(desde: date | None = None, max_dias: int = 3) -> date:
    """Corrida más reciente publicada, retrocediendo si hace falta.

    El IDEAM publica hacia las 14:30 UTC y el pipeline corre a las 16:00:
    hora y media de margen es poco. Si la corrida de hoy se retrasa, se usa
    la de ayer, que sigue cubriendo seis de los siete días hacia adelante.
    Un pronóstico un día más viejo es mucho mejor que ninguno.
    """
    from datetime import timedelta

    desde = desde or date.today()
    for i in range(max_dias):
        candidata = desde - timedelta(days=i)
        if existe(candidata):
            return candidata
    raise SinPublicar(
        f"No hay corrida publicada entre {desde - timedelta(days=max_dias - 1)} "
        f"y {desde}. ¿Cambió la URL o el servidor está caído?"
    )


def pronostico(puntos: list[Punto], corrida: date | None = None,
               con_humedad: bool = True
               ) -> tuple[pd.DataFrame, datetime, list[str]]:
    """Descarga y extrae una corrida completa. Por defecto, la más reciente.

    Devuelve (DataFrame largo, emisión, avisos).

    La emisión es la corrida 00Z de la fecha, no el momento de la descarga:
    es la hora de referencia de las observaciones que alimentaron el modelo,
    y es lo que hace comparable el horizonte entre fuentes.

    `con_humedad` controla si se baja además el ZIP horario de 32 MB. Si esa
    descarga falla, las otras tres variables se devuelven igual: la humedad
    es la menos crítica y no debe tumbar la corrida entera.
    """
    corrida = corrida or ultima_disponible()
    emision = datetime(corrida.year, corrida.month, corrida.day,
                       tzinfo=timezone.utc)
    avisos: list[str] = []

    coincide, cuando = publicado_hoy(corrida)
    if not coincide:
        avisos.append(
            f"Last-Modified del ZIP diario no corresponde a la corrida "
            f"({cuando}). Se verifica por contenido."
        )

    with tempfile.TemporaryDirectory() as tmp:
        ruta = descargar(corrida, Path(tmp) / f"ideam_{corrida}.zip")
        # Verificación fuerte, por contenido: los nombres de adentro llevan
        # la fecha de corrida. Si no coincide, no se procesa.
        verificar_corrida(ruta, corrida)
        partes = [extraer(ruta, puntos)]

        if con_humedad:
            try:
                ruta_hr = descargar(
                    corrida, Path(tmp) / f"ideam_hr_{corrida}.zip",
                    url=url_horaria(corrida), minimo=5_000_000,
                )
                partes.append(extraer_humedad(ruta_hr, puntos))
            except Exception as e:
                avisos.append(f"Humedad del IDEAM no disponible: {e}")

    df = pd.concat([p for p in partes if not p.empty], ignore_index=True)
    return df, emision, avisos
