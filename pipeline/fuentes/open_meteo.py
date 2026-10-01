"""Adaptador de Open-Meteo.

Cubre seis de los ocho modelos del módulo: los cinco deterministas por la API
de pronóstico y WeatherNext 2 por la de ensambles. También la climatología
ERA5 por la API histórica.

No requiere llave ni cuenta. El límite del plan gratuito es de 10.000
llamadas diarias; este adaptador usa unas pocas, porque la API acepta varias
coordenadas y varios modelos en una sola petición.

Tres normalizaciones obligatorias, sin las cuales la comparación entre
modelos engaña:

  1. El día local debe ser el mismo para todos. Se pasa `timezone` y la API
     agrega al día de Colombia.
  2. Tmax y Tmin se piden como variables diarias nativas, no derivadas, para
     que ningún modelo use un criterio distinto.
  3. Cada valor se guarda con la fecha de emisión y el horizonte, porque una
     corrida 00Z y otra 12Z no tienen la misma anticipación.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timezone

import pandas as pd

URL_PRONOSTICO = "https://api.open-meteo.com/v1/forecast"
URL_ENSAMBLE = "https://ensemble-api.open-meteo.com/v1/ensemble"
URL_HISTORICO = "https://archive-api.open-meteo.com/v1/archive"

TIEMPO_ESPERA_S = 60
REINTENTOS = 3
ESPERA_BASE_S = 5

AGENTE = "PMA-nino-Colombia/1.0 (pipeline agroclimatico no comercial)"


class SinDatos(RuntimeError):
    """La API respondió pero sin la variable o el modelo pedidos."""


# ---------------------------------------------------------------------------
# Transporte
# ---------------------------------------------------------------------------

def _pedir(url: str, params: dict) -> list[dict]:
    """GET con reintentos. Devuelve SIEMPRE una lista, una entrada por punto.

    Con varias coordenadas la API responde un arreglo; con una sola, un
    objeto. Se normaliza aquí para que el resto del módulo no se entere.
    """
    consulta = urllib.parse.urlencode(params, doseq=True)
    peticion = urllib.request.Request(
        f"{url}?{consulta}", headers={"User-Agent": AGENTE}
    )

    ultimo: Exception | None = None
    for intento in range(1, REINTENTOS + 1):
        try:
            with urllib.request.urlopen(peticion, timeout=TIEMPO_ESPERA_S) as r:
                datos = json.loads(r.read().decode("utf-8"))
            return datos if isinstance(datos, list) else [datos]
        except urllib.error.HTTPError as e:
            cuerpo = e.read().decode("utf-8", errors="replace")[:300]
            # 400 es error de parámetros: reintentar no arregla nada.
            if e.code == 400:
                raise RuntimeError(f"Open-Meteo rechazó la consulta: {cuerpo}") from e
            ultimo = RuntimeError(f"HTTP {e.code}: {cuerpo}")
        except (urllib.error.URLError, OSError, TimeoutError, ValueError) as e:
            ultimo = e
        if intento < REINTENTOS:
            time.sleep(ESPERA_BASE_S * intento)

    raise RuntimeError(f"Open-Meteo falló tras {REINTENTOS} intentos: {ultimo}")


# ---------------------------------------------------------------------------
# Lectura de la respuesta
# ---------------------------------------------------------------------------

def _columna(bloque: dict, variable: str, id_modelo: str | None) -> list | None:
    """Localiza una columna en el bloque daily/hourly.

    Con varios modelos, la API sufija cada campo con el id del modelo
    (`temperature_2m_max_gfs_global`). Con uno solo, no lo hace. Se prueban
    ambas formas para que el adaptador funcione en los dos casos.
    """
    if id_modelo:
        sufijada = f"{variable}_{id_modelo}"
        if sufijada in bloque:
            return bloque[sufijada]
    return bloque.get(variable)


@dataclass(frozen=True)
class Punto:
    """Un municipio, con el id interno que usa el resto del pipeline."""

    id: str
    lat: float
    lon: float


# ---------------------------------------------------------------------------
# Pronóstico determinista
# ---------------------------------------------------------------------------

def pronostico(
    puntos: list[Punto],
    modelos: dict[str, str],
    variables_diarias: dict[str, str],
    variable_horaria: str | None = None,
    dias: int = 16,
    zona_horaria: str = "America/Bogota",
) -> tuple[pd.DataFrame, datetime]:
    """Pronóstico diario de varios modelos para varios puntos.

    `modelos` mapea nuestro id interno al id de la API: {"gfs": "gfs_global"}.
    `variables_diarias`, nuestro id al de la API: {"tmax": "temperature_2m_max"}.

    Devuelve (DataFrame largo, emisión). Las columnas son las del contrato
    común: municipio, variable, modelo, objetivo, valor.
    """
    if not puntos or not modelos:
        return pd.DataFrame(), datetime.now(timezone.utc)

    params = {
        "latitude": [p.lat for p in puntos],
        "longitude": [p.lon for p in puntos],
        "daily": list(variables_diarias.values()),
        "models": list(modelos.values()),
        "timezone": zona_horaria,
        "forecast_days": dias,
    }
    if variable_horaria:
        params["hourly"] = variable_horaria

    emision = datetime.now(timezone.utc).replace(microsecond=0)
    respuesta = _pedir(URL_PRONOSTICO, params)

    if len(respuesta) != len(puntos):
        raise SinDatos(
            f"Se pidieron {len(puntos)} puntos y la API devolvió {len(respuesta)}."
        )

    filas: list[dict] = []

    for punto, bloque in zip(puntos, respuesta):
        diario = bloque.get("daily") or {}
        fechas = diario.get("time") or []
        if not fechas:
            continue

        for id_interno, id_api in modelos.items():
            for var, var_api in variables_diarias.items():
                valores = _columna(diario, var_api, id_api)
                if valores is None:
                    continue
                for f, v in zip(fechas, valores):
                    if v is None:
                        continue
                    filas.append({
                        "municipio": punto.id,
                        "variable": var,
                        "modelo": id_interno,
                        "objetivo": pd.Timestamp(f).date(),
                        "valor": float(v),
                    })

        if variable_horaria:
            filas += _agregar_horaria(
                punto, bloque, modelos, variable_horaria
            )

    return pd.DataFrame(filas), emision


def _agregar_horaria(
    punto: Punto, bloque: dict, modelos: dict[str, str], variable_api: str
) -> list[dict]:
    """Agrega una variable horaria al día local.

    Se hace aquí y no en la API porque Open-Meteo no garantiza humedad
    relativa como variable diaria en todos los modelos. Al agregarla nosotros
    con el mismo criterio para todos, ningún modelo queda con una definición
    distinta de 'la humedad del martes'.
    """
    horario = bloque.get("hourly") or {}
    horas = horario.get("time") or []
    if not horas:
        return []

    # La API ya devuelve las horas en la zona pedida, así que el día local
    # sale de los primeros 10 caracteres.
    dias = [h[:10] for h in horas]
    filas: list[dict] = []

    for id_interno, id_api in modelos.items():
        valores = _columna(horario, variable_api, id_api)
        if valores is None:
            continue
        serie = pd.DataFrame({"dia": dias, "valor": valores}).dropna()
        if serie.empty:
            continue
        agrupado = serie.groupby("dia").valor.agg(["mean", "max"])
        for dia, fila in agrupado.iterrows():
            filas.append({
                "municipio": punto.id, "variable": "humedad_relativa",
                "modelo": id_interno, "objetivo": pd.Timestamp(dia).date(),
                "valor": round(float(fila["mean"]), 1),
            })
            filas.append({
                "municipio": punto.id, "variable": "humedad_relativa_max",
                "modelo": id_interno, "objetivo": pd.Timestamp(dia).date(),
                "valor": round(float(fila["max"]), 1),
            })
    return filas


# ---------------------------------------------------------------------------
# Ensamble
# ---------------------------------------------------------------------------

def ensamble(
    puntos: list[Punto],
    id_api: str,
    id_interno: str,
    variables_diarias: dict[str, str],
    dias: int = 16,
    zona_horaria: str = "America/Bogota",
) -> tuple[pd.DataFrame, datetime]:
    """Media y dispersión de un ensamble (WeatherNext 2).

    La API devuelve una columna por miembro, sufijada `_member01`, `_member02`.
    Se guarda la media y la desviación: la media para comparar con los
    deterministas, la desviación porque la dispersión interna del ensamble es
    información propia sobre la incertidumbre.
    """
    params = {
        "latitude": [p.lat for p in puntos],
        "longitude": [p.lon for p in puntos],
        "daily": list(variables_diarias.values()),
        "models": id_api,
        "timezone": zona_horaria,
        "forecast_days": dias,
    }

    emision = datetime.now(timezone.utc).replace(microsecond=0)
    respuesta = _pedir(URL_ENSAMBLE, params)
    filas: list[dict] = []

    for punto, bloque in zip(puntos, respuesta):
        diario = bloque.get("daily") or {}
        fechas = diario.get("time") or []
        if not fechas:
            continue

        for var, var_api in variables_diarias.items():
            miembros = [
                v for k, v in diario.items()
                if k.startswith(var_api) and k != "time"
            ]
            if not miembros:
                continue
            tabla = pd.DataFrame(miembros).T          # filas = fechas
            media = tabla.mean(axis=1, skipna=True)
            desv = tabla.std(axis=1, skipna=True)

            for i, f in enumerate(fechas):
                if pd.isna(media.iloc[i]):
                    continue
                filas.append({
                    "municipio": punto.id, "variable": var,
                    "modelo": id_interno,
                    "objetivo": pd.Timestamp(f).date(),
                    "valor": round(float(media.iloc[i]), 2),
                    "dispersion": (
                        round(float(desv.iloc[i]), 2)
                        if not pd.isna(desv.iloc[i]) else None
                    ),
                    "miembros": len(miembros),
                })

    return pd.DataFrame(filas), emision


# ---------------------------------------------------------------------------
# Climatología ERA5
# ---------------------------------------------------------------------------

def climatologia(
    punto: Punto,
    variables_diarias: dict[str, str],
    anio_inicio: int = 1991,
    anio_fin: int = 2020,
) -> pd.DataFrame:
    """Climatología por día del año desde ERA5, en UNA llamada por municipio.

    ERA5 no es dato observado y también está sesgado. Se usa como REGLA DE
    MEDIR, no como verdad: la anomalía es una diferencia, y el error de la
    regla se cancela mientras todo se mida con la misma.

    Lo que aporta y ningún modelo puede: qué es NORMAL. Eso requiere décadas,
    y del archivo de pronósticos solo hay unos meses, que además son de El
    Niño fuerte.

    Se calcula por día del año y no como promedio anual único, para que una
    eventual estacionalidad del sesgo de ERA5 también se cancele.
    """
    params = {
        "latitude": punto.lat,
        "longitude": punto.lon,
        "start_date": f"{anio_inicio}-01-01",
        "end_date": f"{anio_fin}-12-31",
        "daily": list(variables_diarias.values()),
        "timezone": "America/Bogota",
    }

    bloque = _pedir(URL_HISTORICO, params)[0]
    diario = bloque.get("daily") or {}
    fechas = diario.get("time") or []
    if not fechas:
        raise SinDatos(f"ERA5 no devolvió datos para {punto.id}.")

    tabla = pd.DataFrame({"fecha": pd.to_datetime(fechas)})
    for var, var_api in variables_diarias.items():
        if var_api in diario:
            tabla[var] = diario[var_api]

    # El 29 de febrero se excluye, pero NO basta con filtrarlo: en años
    # bisiestos los días posteriores conservan un dayofyear corrido en uno,
    # y aparecería un día 366 fantasma con apenas 7 observaciones. Se
    # reindexa para que el calendario sea siempre de 365 días.
    tabla = tabla[~((tabla.fecha.dt.month == 2) & (tabla.fecha.dt.day == 29))]
    bisiesto_tardio = (
        tabla.fecha.dt.is_leap_year & (tabla.fecha.dt.month > 2)
    )
    tabla["dia_del_anio"] = tabla.fecha.dt.dayofyear - bisiesto_tardio.astype(int)

    columnas = [c for c in variables_diarias if c in tabla.columns]
    promedio = tabla.groupby("dia_del_anio")[columnas].mean()

    largo = promedio.reset_index().melt(
        id_vars="dia_del_anio", var_name="variable", value_name="valor"
    )
    largo["municipio"] = punto.id
    largo["periodo"] = f"{anio_inicio}-{anio_fin}"
    return largo.dropna(subset=["valor"])


def suavizar(clim: pd.DataFrame, ventana: int = 15) -> pd.DataFrame:
    """Media móvil circular sobre el día del año.

    Sin esto la climatología queda dentada: 30 años por día calendario es
    poca muestra y el ruido se cuela en la anomalía. La ventana es circular
    para que el 31 de diciembre y el 1 de enero no tengan un salto.
    """
    salida = []
    for (mun, var), grupo in clim.groupby(["municipio", "variable"]):
        g = grupo.sort_values("dia_del_anio").copy()
        serie = g.valor.to_numpy()
        n = len(serie)
        # Se replica la serie a los lados para que el suavizado cierre el año
        triple = pd.Series(list(serie) * 3)
        suave = triple.rolling(ventana, center=True, min_periods=1).mean()
        g["valor"] = suave.iloc[n:2 * n].to_numpy().round(2)
        salida.append(g)
    return pd.concat(salida, ignore_index=True)
