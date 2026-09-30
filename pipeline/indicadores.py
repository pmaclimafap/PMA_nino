"""Indicadores agroclimáticos.

Se calculan AQUÍ y no en el navegador. El frontend solo dibuja: así el
indicador es el mismo para quien mira el tablero, quien consulta el archivo y
quien audita el cálculo.

Entrada: DataFrame largo con columnas [fecha, municipio, variable, valor].
Salida: un diccionario de indicadores por municipio y por ventana temporal.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from pipeline import configuracion as config


# ---------------------------------------------------------------------------
# Control de calidad
# ---------------------------------------------------------------------------

@dataclass
class Banderas:
    """Problemas detectados en los datos de un municipio.

    Se distinguen DOS niveles, porque mezclarlos vuelve la bandera inútil:

    GRAVE — el dato es físicamente imposible o está fuera de rango. Es un
      error del producto en ese día concreto y hay que mirarlo.

    SESGO — la amplitud térmica es más baja de lo esperable. NO es un error
      puntual: es una característica documentada de MSWX, que subestima Tmax
      y sobrestima Tmin. Se reporta como proporción de la ventana, no como
      incidencia por día, porque sobre 60 días ocurre casi siempre y marcar
      al municipio no informaría nada.
    """

    dias_evaluados: int = 0
    incoherencia_termica: list[str] = field(default_factory=list)
    fuera_de_rango: list[str] = field(default_factory=list)
    amplitud_baja: list[str] = field(default_factory=list)

    MAX_EJEMPLOS = 10

    @property
    def graves(self) -> int:
        return len(self.incoherencia_termica) + len(self.fuera_de_rango)

    @property
    def hay_problemas(self) -> bool:
        """Solo lo grave enciende la bandera del tablero."""
        return self.graves > 0

    @property
    def fraccion_amplitud_baja(self) -> float:
        if not self.dias_evaluados:
            return 0.0
        return len(self.amplitud_baja) / self.dias_evaluados

    def a_dict(self) -> dict:
        recortar = lambda xs: xs[: self.MAX_EJEMPLOS]  # noqa: E731
        return {
            "dias_evaluados": self.dias_evaluados,
            "graves": self.graves,
            "incoherencia_termica": {
                "dias": len(self.incoherencia_termica),
                "ejemplos": recortar(self.incoherencia_termica),
            },
            "fuera_de_rango": {
                "dias": len(self.fuera_de_rango),
                "ejemplos": recortar(self.fuera_de_rango),
            },
            # Informativo, no cuenta como problema.
            "amplitud_baja": {
                "dias": len(self.amplitud_baja),
                "fraccion": round(self.fraccion_amplitud_baja, 2),
                "nota": (
                    "Sesgo conocido de MSWX (subestima Tmax, sobrestima Tmin), "
                    "no un error puntual."
                ),
            },
        }


def revisar_calidad(pivote: pd.DataFrame) -> Banderas:
    """Revisa un DataFrame indexado por fecha con una columna por variable."""
    cfg = config.umbrales()
    qc = cfg["control_calidad"]
    b = Banderas(dias_evaluados=len(pivote))

    for var, (minimo, maximo) in qc["rango_valido"].items():
        if var not in pivote.columns:
            continue
        malos = pivote[(pivote[var] < minimo) | (pivote[var] > maximo)]
        b.fuera_de_rango += [f"{f.date()}:{var}" for f in malos.index]

    tiene_las_tres = all(
        v in pivote.columns for v in ("tmin", "temperatura", "tmax")
    )
    if qc.get("exigir_tmin_menor_igual_temp_menor_igual_tmax") and tiene_las_tres:
        incoherentes = pivote[
            ~(
                (pivote.tmin <= pivote.temperatura)
                & (pivote.temperatura <= pivote.tmax)
            )
            & pivote[["tmin", "temperatura", "tmax"]].notna().all(axis=1)
        ]
        b.incoherencia_termica += [str(f.date()) for f in incoherentes.index]

    if qc.get("marcar_amplitud_baja") and {"tmax", "tmin"} <= set(pivote.columns):
        minimo = cfg["temperatura"]["amplitud_minima_esperada"]
        estrechos = pivote[(pivote.tmax - pivote.tmin) < minimo]
        b.amplitud_baja += [str(f.date()) for f in estrechos.index]

    return b


# ---------------------------------------------------------------------------
# Rachas
# ---------------------------------------------------------------------------

def _rachas(condicion: pd.Series) -> list[int]:
    """Longitudes de los tramos consecutivos donde la condición es verdadera."""
    valores = condicion.fillna(False).astype(bool).to_numpy()
    if not valores.any():
        return []
    # Corta la serie donde cambia de valor y mide los tramos verdaderos
    cambios = np.diff(valores.astype(int))
    inicios = np.flatnonzero(cambios == 1) + 1
    finales = np.flatnonzero(cambios == -1) + 1
    if valores[0]:
        inicios = np.r_[0, inicios]
    if valores[-1]:
        finales = np.r_[finales, len(valores)]
    return (finales - inicios).tolist()


def racha_actual(condicion: pd.Series) -> int:
    """Días consecutivos al final de la serie que cumplen la condición.

    Es el número que le importa a un productor hoy: no cuál fue la racha más
    larga del mes, sino cuántos días lleva sin llover.
    """
    valores = condicion.fillna(False).astype(bool).to_numpy()
    cuenta = 0
    for v in reversed(valores):
        if not v:
            break
        cuenta += 1
    return cuenta


# ---------------------------------------------------------------------------
# Indicadores por variable
# ---------------------------------------------------------------------------

def _categoria_acumulado(acumulado: float, dias: int) -> str:
    """Clasifica la lluvia acumulada según la duración de la ventana."""
    tabla = config.umbrales()["precipitacion"]["acumulado_mm"]
    escala = tabla.get(dias) or tabla[min(tabla, key=lambda d: abs(d - dias))]
    if acumulado <= escala["seco"]:
        return "seco"
    if acumulado < escala["moderada"]:
        return "baja"
    if acumulado < escala["fuerte"]:
        return "moderada"
    return "fuerte"


def indicadores_precipitacion(serie: pd.Series, dias: int) -> dict:
    cfg = config.umbrales()["precipitacion"]
    umbral = cfg["dia_con_lluvia_mm"]
    validos = serie.dropna()
    if validos.empty:
        return {"sin_datos": True}

    llovio = serie >= umbral
    seco = serie < umbral
    acumulado = float(validos.sum())
    rachas_secas = _rachas(seco)

    return {
        "acumulado_mm": round(acumulado, 1),
        "categoria": _categoria_acumulado(acumulado, dias),
        "dias_con_lluvia": int(llovio.sum()),
        "dias_secos": int(seco.sum()),
        "racha_seca_max": max(rachas_secas) if rachas_secas else 0,
        "racha_seca_actual": racha_actual(seco),
        "maximo_diario_mm": round(float(validos.max()), 1),
        "dias_con_dato": int(len(validos)),
    }


def indicadores_temperatura(pivote: pd.DataFrame, sistema: str) -> dict:
    cfg = config.umbrales()
    sistemas = cfg["sistemas_productivos"]
    if sistema not in sistemas:
        raise KeyError(f"Sistema productivo '{sistema}' no está en umbrales.yml")

    umbral = sistemas[sistema]["tmax"]
    minimo_ola = cfg["temperatura"]["dias_consecutivos_ola_calor"]

    if "tmax" not in pivote.columns or pivote.tmax.dropna().empty:
        return {"sin_datos": True}

    tmax = pivote.tmax
    calurosos = tmax > umbral
    rachas = _rachas(calurosos)
    olas = [r for r in rachas if r >= minimo_ola]

    salida = {
        "umbral_tmax": umbral,
        "sistema_productivo": sistema,
        "dias_sobre_umbral": int(calurosos.sum()),
        "tmax_maxima": round(float(tmax.max()), 1),
        "tmax_promedio": round(float(tmax.mean()), 1),
        # Olas de calor: tramos de 3+ días consecutivos sobre el umbral del
        # sistema productivo. Definición distinta a la del Reporte 1, que usa
        # el P80 climatológico. Ver la nota en umbrales.yml.
        "olas_de_calor": len(olas),
        "ola_mas_larga": max(olas) if olas else 0,
        "dias_sobre_umbral_seguidos_ahora": racha_actual(calurosos),
    }

    if "tmin" in pivote.columns and not pivote.tmin.dropna().empty:
        salida["tmin_minima"] = round(float(pivote.tmin.min()), 1)
        salida["amplitud_promedio"] = round(
            float((pivote.tmax - pivote.tmin).mean()), 1
        )
    return salida


def indicadores_humedad(pivote: pd.DataFrame, sistema: str) -> dict:
    sistemas = config.umbrales()["sistemas_productivos"]
    umbral = sistemas[sistema]["humedad_relativa"]

    if "humedad_relativa" not in pivote.columns:
        return {"sin_datos": True}
    hr = pivote.humedad_relativa.dropna()
    if hr.empty:
        return {"sin_datos": True}

    altos = pivote.humedad_relativa > umbral
    rachas = _rachas(altos)
    return {
        "umbral_hr": umbral,
        "dias_sobre_umbral": int(altos.sum()),
        "racha_max": max(rachas) if rachas else 0,
        "hr_promedio": round(float(hr.mean()), 1),
        "hr_maxima": round(float(hr.max()), 1),
    }


# ---------------------------------------------------------------------------
# Índice de temperatura y humedad
# ---------------------------------------------------------------------------

def calcular_ith(tmax: pd.Series, hr: pd.Series) -> pd.Series:
    """ITH diario.

    Para sistemas pecuarios, evaluar temperatura y humedad por separado es
    engañoso: 33 °C con 60 % de HR y 33 °C con 85 % son situaciones muy
    distintas para un animal, porque la humedad impide disipar calor.
    """
    return (1.8 * tmax + 32) - (0.55 - 0.0055 * hr) * (1.8 * tmax - 26)


def _categoria_ith(valor: float, categorias: dict) -> str:
    for nombre, (minimo, maximo) in categorias.items():
        if (minimo is None or valor >= minimo) and (maximo is None or valor <= maximo):
            return nombre
    return "normal"


def indicadores_ith(pivote: pd.DataFrame, sistema: str) -> dict | None:
    """Solo para los sistemas marcados con usar_ith en umbrales.yml."""
    cfg = config.umbrales()
    sistemas = cfg["sistemas_productivos"]
    if not sistemas.get(sistema, {}).get("usar_ith"):
        return None
    if not {"tmax", "humedad_relativa"} <= set(pivote.columns):
        return None

    serie = calcular_ith(pivote.tmax, pivote.humedad_relativa).dropna()
    if serie.empty:
        return None

    categorias = cfg["ith"]["categorias"]
    etiquetas = serie.map(lambda v: _categoria_ith(v, categorias))

    return {
        "estado": cfg["ith"]["estado"],
        "ith_promedio": round(float(serie.mean()), 1),
        "ith_maximo": round(float(serie.max()), 1),
        "categoria_peor_dia": _categoria_ith(float(serie.max()), categorias),
        "dias_por_categoria": {
            c: int((etiquetas == c).sum()) for c in categorias
        },
    }


# ---------------------------------------------------------------------------
# Orquestación
# ---------------------------------------------------------------------------

def pivotar(df: pd.DataFrame, municipio_id: str) -> pd.DataFrame:
    """De formato largo a una fila por fecha y una columna por variable."""
    sub = df[df.municipio == municipio_id]
    if sub.empty:
        return pd.DataFrame()
    pivote = sub.pivot_table(
        index="fecha", columns="variable", values="valor", aggfunc="first"
    ).sort_index()
    pivote.index = pd.to_datetime(pivote.index)
    return pivote


def calcular(df: pd.DataFrame) -> dict:
    """Indicadores de todos los municipios, en todas las ventanas.

    La ventana se toma desde la última fecha con datos, no desde hoy: si el
    NRT tiene dos días de rezago, "última semana" debe seguir siendo una
    semana de datos y no cinco días.
    """
    ventanas = config.umbrales()["ventanas"]
    salida: dict[str, dict] = {}

    for m in config.municipios():
        pivote = pivotar(df, m.id)
        if pivote.empty:
            salida[m.id] = {"sin_datos": True}
            continue

        ultima = pivote.index.max()
        banderas = revisar_calidad(pivote)

        por_ventana = {}
        for nombre, dias in ventanas.items():
            desde = ultima - pd.Timedelta(days=dias - 1)
            trozo = pivote.loc[desde:ultima]
            if trozo.empty:
                continue

            bloque: dict = {"dias": dias}
            if "precipitacion" in trozo.columns:
                bloque["precipitacion"] = indicadores_precipitacion(
                    trozo.precipitacion, dias
                )
            bloque["temperatura"] = indicadores_temperatura(trozo, m.sistema_principal)
            bloque["humedad"] = indicadores_humedad(trozo, m.sistema_principal)
            ith = indicadores_ith(trozo, m.sistema_principal)
            if ith:
                bloque["ith"] = ith
            por_ventana[nombre] = bloque

        salida[m.id] = {
            "nombre": m.nombre,
            "departamento": m.departamento,
            "sistema_principal": m.sistema_principal,
            "ultima_fecha_con_dato": str(ultima.date()),
            "dias_con_dato": int(len(pivote)),
            "calidad": banderas.a_dict(),
            # Solo lo grave. El sesgo de amplitud se consulta en
            # calidad.amplitud_baja.fraccion.
            "tiene_alertas_calidad": banderas.hay_problemas,
            "ventanas": por_ventana,
        }

    return salida
