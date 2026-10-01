"""Agregación entre modelos y señal de predictibilidad.

Convierte las emisiones archivadas en lo que ve el tablero. Nada de esto se
guarda: son derivados, y almacenarlos obligaría a regenerar el histórico
cada vez que cambie un peso.

Metodología adaptada de github.com/Flowm/meteocompare.

DOS REGLAS QUE PARECEN DETALLES Y NO LO SON

1. La precipitación NO se promedia. Promediar modelos produce una llovizna
   constante que ningún modelo predijo y que nunca ocurre: si tres dicen
   20 mm y cuatro dicen 0, el promedio da 8,6 mm, un día de lluvia moderada
   que nadie pronosticó. Para lluvia se usa la mediana y, sobre todo, el
   acuerdo: qué fracción de los modelos supera el umbral de día con lluvia.

2. La confianza del día es el MÍNIMO entre sus variables, no el promedio.
   Promediar hace que todo quede en medio: una variable certera nunca
   levanta el día y una incierta nunca lo hunde. El día vale lo que vale su
   variable menos confiable.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from pipeline import configuracion as config


# ---------------------------------------------------------------------------
# Pesos
# ---------------------------------------------------------------------------

def peso_modelo(modelo: str, horizonte: int) -> float:
    """Peso de un modelo a un horizonte dado. 0 si ya no alcanza.

    Más allá de su horizonte máximo el modelo no aporta: dejar su último
    valor repetido o extrapolarlo inventaría una señal que el modelo nunca
    produjo.
    """
    modelos = config.pronostico()["modelos"]
    cfg = modelos.get(modelo)
    if cfg is None:
        return 0.0
    if horizonte > cfg["horizonte_max"]:
        return 0.0
    return float(cfg.get("peso", 1.0))


def _aplica_a(modelo: str, variable: str) -> bool:
    """¿Este modelo aporta esta variable?

    CHIRPS-GEFS solo pronostica lluvia. Incluirlo en temperatura con valor
    nulo reduciría el conteo de modelos y castigaría la confianza sin razón.
    """
    cfg = config.pronostico()["modelos"].get(modelo, {})
    permitidas = cfg.get("solo_variables")
    return permitidas is None or variable in permitidas


# ---------------------------------------------------------------------------
# Predictibilidad
# ---------------------------------------------------------------------------

def dispersion_tipica(variable: str, horizonte: int) -> float:
    """Dispersión que se considera normal entre modelos a ese horizonte.

    Crece con los días porque la incertidumbre crece. Se interpola entre los
    puntos declarados en la configuración en lugar de usar escalones, para
    que la confianza no dé saltos artificiales entre el día 3 y el 4.
    """
    tabla = config.pronostico()["confianza"]["dispersion_tipica"].get(variable)
    if not tabla:
        return 1.0
    dias = sorted(int(k) for k in tabla)
    valores = [float(tabla[d]) for d in dias]
    return float(np.interp(horizonte, dias, valores))


def predictibilidad(valores: list[float], pesos: list[float],
                    variable: str, horizonte: int) -> float:
    """Confianza entre 0 y 1 a partir del acuerdo entre modelos.

        spreadScore = clamp(1 − desviación / dispersión_típica, 0, 1)
        modelFactor = min(1, n / 3)

    El factor por número de modelos evita que uno solo alcance confianza
    alta "coincidiendo consigo mismo": con un modelo el techo es un tercio,
    con dos, dos tercios.
    """
    n = len(valores)
    if n == 0:
        return 0.0

    minimo = config.pronostico()["confianza"]["modelos_para_factor_pleno"]
    factor_modelos = min(1.0, n / minimo)

    if n == 1:
        return round(factor_modelos, 3)

    arr = np.asarray(valores, dtype=float)
    w = np.asarray(pesos, dtype=float)
    media = np.average(arr, weights=w)
    desv = float(np.sqrt(np.average((arr - media) ** 2, weights=w)))

    acuerdo = max(0.0, min(1.0, 1 - desv / dispersion_tipica(variable, horizonte)))
    return round(acuerdo * factor_modelos, 3)


def categoria_confianza(valor: float) -> str:
    cortes = config.pronostico()["confianza"]["cortes"]
    if valor >= cortes["alta"]:
        return "alta"
    if valor >= cortes["media"]:
        return "media"
    return "baja"


# ---------------------------------------------------------------------------
# Agregación
# ---------------------------------------------------------------------------

def agregar(pronosticos: pd.DataFrame) -> pd.DataFrame:
    """Un registro por municipio, variable y día objetivo.

    Devuelve el valor agregado, la dispersión, el número de modelos que
    aportaron y la predictibilidad. Para precipitación añade la probabilidad
    de superar el umbral de día con lluvia.
    """
    if pronosticos.empty:
        return pd.DataFrame()

    umbral_lluvia = config.umbrales()["precipitacion"]["dia_con_lluvia_mm"]
    filas = []

    claves = ["municipio", "variable", "objetivo", "horizonte"]
    for (mun, var, obj, hor), grupo in pronosticos.groupby(claves, sort=False):
        datos = [
            (r.valor, peso_modelo(r.modelo, hor), r.modelo,
             getattr(r, "anomalia", None))
            for r in grupo.itertuples()
            if _aplica_a(r.modelo, var) and peso_modelo(r.modelo, hor) > 0
            and pd.notna(r.valor)
        ]
        if not datos:
            continue

        valores = [d[0] for d in datos]
        pesos = [d[1] for d in datos]
        anomalias = [d[3] for d in datos if d[3] is not None and pd.notna(d[3])]

        registro = {
            "municipio": mun, "variable": var, "objetivo": obj,
            "horizonte": hor,
            "modelos": len(datos),
            "modelos_lista": ",".join(sorted(d[2] for d in datos)),
            "dispersion": round(float(np.sqrt(np.average(
                (np.asarray(valores) - np.average(valores, weights=pesos)) ** 2,
                weights=pesos))), 2) if len(valores) > 1 else 0.0,
            "predictibilidad": predictibilidad(valores, pesos, var, hor),
            "minimo": round(float(min(valores)), 2),
            "maximo": round(float(max(valores)), 2),
        }

        if var == "precipitacion":
            # Mediana, no media: la lluvia es intermitente y el promedio
            # inventa un día de llovizna que nadie pronosticó.
            registro["valor"] = round(float(np.median(valores)), 2)
            peso_total = sum(pesos)
            peso_lluvia = sum(
                p for v, p in zip(valores, pesos) if v >= umbral_lluvia
            )
            registro["probabilidad_lluvia"] = round(peso_lluvia / peso_total, 3)
        else:
            registro["valor"] = round(float(np.average(valores, weights=pesos)), 2)
            if anomalias:
                registro["anomalia"] = round(float(np.mean(anomalias)), 2)

        registro["confianza"] = categoria_confianza(registro["predictibilidad"])
        filas.append(registro)

    return pd.DataFrame(filas)


def resumen_dia(agregado: pd.DataFrame) -> pd.DataFrame:
    """Una fila por municipio y día, con la confianza global.

    La confianza del día es el MÍNIMO de las variables declaradas en la
    configuración, no el promedio: el día vale lo que vale su variable menos
    confiable. Promediar regresaba todo a "medio" y no discriminaba.
    """
    if agregado.empty:
        return pd.DataFrame()

    cfg = config.pronostico()["confianza"]
    usar = cfg["variables_resumen"]
    filas = []

    for (mun, obj), grupo in agregado.groupby(["municipio", "objetivo"]):
        fila = {"municipio": mun, "objetivo": obj,
                "horizonte": int(grupo.horizonte.iloc[0])}

        for r in grupo.itertuples():
            fila[r.variable] = r.valor
            if getattr(r, "anomalia", None) is not None and pd.notna(r.anomalia):
                fila[f"{r.variable}_anomalia"] = r.anomalia
            if r.variable == "precipitacion":
                fila["probabilidad_lluvia"] = r.probabilidad_lluvia
            fila[f"{r.variable}_confianza"] = r.predictibilidad

        relevantes = grupo[grupo.variable.isin(usar)].predictibilidad
        if len(relevantes):
            fila["predictibilidad"] = round(float(relevantes.min()), 3)
            fila["confianza"] = categoria_confianza(fila["predictibilidad"])
            # Qué variable hundió el día: útil para explicar la insignia.
            peor = grupo[grupo.variable.isin(usar)].nsmallest(1, "predictibilidad")
            fila["variable_limitante"] = peor.variable.iloc[0]

        fila["modelos"] = int(grupo.modelos.max())
        filas.append(fila)

    return pd.DataFrame(filas).sort_values(["municipio", "objetivo"])
