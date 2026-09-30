"""Índice oceánico de El Niño (ONI) de la NOAA.

Fuente: https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt
Formato: texto tabulado, una fila por trimestre solapado desde 1950.

    SEAS  YR   TOTAL   ANOM
    DJF 1950  25.01  -1.32
    ...
    JJA 2026  29.09   1.80

CADENCIA: la NOAA actualiza la tabla hacia el día 5 de cada mes.

MADUREZ: los valores más recientes pueden cambiar hasta dos meses después de
su publicación inicial, por el filtro de alta frecuencia aplicado al ERSST.
Es el mismo problema que el NRT de MSWEP y se trata igual: el valor se marca
como provisional, no se presenta como definitivo.

NOTA SOBRE EL ÍNDICE OFICIAL: según la declaración pública 26-05 del NWS, el
índice usado para el monitoreo y la predicción oficial del ENSO es el RONI
(Relative Oceanic Niño Index), que resta la anomalía media del cinturón
tropical para aislar el calor del Pacífico del calentamiento general del
océano. El ONI se conserva aquí porque tiene un endpoint estable en texto
plano y una serie continua desde 1950; el RONI solo se publica como tabla
HTML. Queda pendiente añadirlo.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from dataclasses import dataclass, asdict

URL_ONI = "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt"
URL_DISCUSION = (
    "https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/"
    "enso_advisory/ensodisc.shtml"
)
TIEMPO_ESPERA_S = 30

# Trimestres solapados: el código son las iniciales de los tres meses.
# El mes central es el que representa el valor.
CODIGOS = ["DJF", "JFM", "FMA", "MAM", "AMJ", "MJJ",
           "JJA", "JAS", "ASO", "SON", "OND", "NDJ"]
MES_CENTRAL = {c: i + 1 for i, c in enumerate(CODIGOS)}

NOMBRES = {
    "DJF": "diciembre-enero-febrero", "JFM": "enero-febrero-marzo",
    "FMA": "febrero-marzo-abril", "MAM": "marzo-abril-mayo",
    "AMJ": "abril-mayo-junio", "MJJ": "mayo-junio-julio",
    "JJA": "junio-julio-agosto", "JAS": "julio-agosto-septiembre",
    "ASO": "agosto-septiembre-octubre", "SON": "septiembre-octubre-noviembre",
    "OND": "octubre-noviembre-diciembre", "NDJ": "noviembre-diciembre-enero",
}

# El umbral de fase es ±0.5 °C. Los rangos de intensidad son la convención
# de uso común, no una definición oficial de la NOAA.
UMBRAL_FASE = 0.5

# Los dos trimestres más recientes están sujetos a revisión.
TRIMESTRES_PROVISIONALES = 2


@dataclass
class Trimestre:
    codigo: str
    anio: int
    sst: float
    anomalia: float

    @property
    def etiqueta(self) -> str:
        return f"{self.codigo} {self.anio}"

    @property
    def nombre_largo(self) -> str:
        return f"{NOMBRES.get(self.codigo, self.codigo)} de {self.anio}"


def descargar(url: str = URL_ONI) -> str:
    peticion = urllib.request.Request(
        url, headers={"User-Agent": "PMA-nino-Colombia/1.0 (pipeline agroclimatico)"}
    )
    with urllib.request.urlopen(peticion, timeout=TIEMPO_ESPERA_S) as r:
        return r.read().decode("utf-8", errors="replace")


def parsear(texto: str) -> list[Trimestre]:
    """Lee la tabla. Ignora la cabecera y cualquier línea mal formada."""
    filas: list[Trimestre] = []
    for linea in texto.splitlines():
        partes = linea.split()
        if len(partes) != 4 or partes[0] not in MES_CENTRAL:
            continue  # cabecera, línea vacía o basura
        try:
            filas.append(Trimestre(partes[0], int(partes[1]),
                                   float(partes[2]), float(partes[3])))
        except ValueError:
            continue
    return filas


def fase(anomalia: float) -> str:
    if anomalia >= UMBRAL_FASE:
        return "El Niño"
    if anomalia <= -UMBRAL_FASE:
        return "La Niña"
    return "Neutral"


def intensidad(anomalia: float) -> str:
    a = abs(anomalia)
    if a < UMBRAL_FASE:
        return "neutral"
    if a < 1.0:
        return "débil"
    if a < 1.5:
        return "moderado"
    if a < 2.0:
        return "fuerte"
    return "muy fuerte"


def categoria(anomalia: float) -> str:
    f = fase(anomalia)
    if f == "Neutral":
        return "Condiciones neutrales"
    return f"{f} {intensidad(anomalia)}"


def posicion_historica(serie: list[Trimestre], actual: Trimestre) -> dict:
    """Qué tan excepcional es el valor actual para esa época del año.

    El reporte del proyecto afirma que el evento es uno de los más intensos
    desde 1950. Con la serie completa eso se puede cuantificar en lugar de
    dejarlo como afirmación.
    """
    mismo_trimestre = [t for t in serie if t.codigo == actual.codigo]
    mas_calidos = [t for t in mismo_trimestre if t.anomalia > actual.anomalia]
    return {
        "puesto": len(mas_calidos) + 1,
        "de": len(mismo_trimestre),
        "desde": min(t.anio for t in mismo_trimestre) if mismo_trimestre else None,
        "texto": (
            f"{len(mas_calidos) + 1}.º trimestre más cálido de "
            f"{len(mismo_trimestre)} para {NOMBRES.get(actual.codigo, actual.codigo)} "
            f"desde {min(t.anio for t in mismo_trimestre)}"
        ) if mismo_trimestre else "",
    }


def obtener(url: str = URL_ONI) -> dict:
    """Estado actual del ENSO, en el esquema que consume el tablero.

    Nunca lanza excepción: si la fuente falla, devuelve valor nulo y el
    tablero muestra "sin dato" en lugar de un número inventado. Un índice
    equivocado es peor que ninguno cuando lo que se está comunicando es si
    viene o no una sequía.
    """
    base = {
        "valor": None,
        "categoria": "Sin dato",
        "actualizado": "",
        "fuente_url": URL_DISCUSION,
    }

    try:
        serie = parsear(descargar(url))
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        base["categoria"] = "Sin dato (fuente no disponible)"
        base["error"] = str(e)[:200]
        return base

    if not serie:
        base["categoria"] = "Sin dato (tabla vacía o formato cambiado)"
        return base

    actual = serie[-1]
    provisional = True  # el último trimestre siempre lo es
    previos = serie[-TRIMESTRES_PROVISIONALES:]

    return {
        **base,
        "valor": round(actual.anomalia, 2),
        "categoria": categoria(actual.anomalia),
        "actualizado": f"{actual.etiqueta} (NOAA CPC)",
        "fase": fase(actual.anomalia),
        "intensidad": intensidad(actual.anomalia),
        "trimestre": actual.etiqueta,
        "trimestre_largo": actual.nombre_largo,
        "sst_absoluta": round(actual.sst, 2),
        # Los valores recientes se revisan hasta dos meses después.
        "provisional": provisional,
        "nota_madurez": (
            "Valor sujeto a revisión: la NOAA puede ajustarlo hasta dos meses "
            "después de su publicación inicial."
        ),
        "ultimos": [
            {"trimestre": t.etiqueta, "anomalia": round(t.anomalia, 2),
             "categoria": categoria(t.anomalia)}
            for t in previos
        ],
        "historico": posicion_historica(serie, actual),
        "indice": "ONI (ERSSTv5)",
        "nota_indice": (
            "Desde la declaración 26-05 del NWS, el índice oficial de "
            "monitoreo del ENSO es el RONI. El ONI se muestra aquí por tener "
            "serie continua desde 1950."
        ),
        "serie_desde": serie[0].anio,
        "trimestres": len(serie),
    }
