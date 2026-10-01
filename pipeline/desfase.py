"""Desfase entre cada modelo y la referencia.

El nombre importa. Esto NO es "el sesgo contra la realidad": no tenemos
estaciones meteorológicas y no hacemos falta tenerlas. Lo que se mide es la
diferencia entre dos productos:

    desfase = promedio( modelo − observado )

Escribiendo cada producto como realidad más su error:

    modelo    = realidad + e_modelo
    observado = realidad + e_obs
    desfase   = e_modelo − e_obs

La realidad se cancela. Queda la diferencia entre errores, que es
exactamente lo que se necesita para poner el modelo en la escala de la
referencia. Después, al restar la climatología —que viene de esa misma
referencia— el error e_obs también se cancela y la anomalía queda limpia.

DOS ERRORES FÁCILES DE COMETER, Y POR QUÉ NO SE COMETEN AQUÍ

1. Promediar los pronósticos en vez de promediar la resta. El archivo
   disponible es de meses de El Niño fuerte: el promedio de los pronósticos
   sería una climatología corta con el evento adentro, y la anomalía daría
   cero justo cuando debería gritar. Al promediar la RESTA, el calor está en
   ambas columnas y se va.

2. Un desfase único por modelo. El error crece con el horizonte y cambia con
   la estación, porque el error de nubosidad no es el mismo en temporada
   seca que en lluviosa. Se estima por municipio, variable, mes y banda de
   horizonte.
"""

from __future__ import annotations

import pandas as pd

CLAVE = ["municipio", "modelo", "variable", "mes", "horizonte_banda"]


def banda(horizonte: int, bandas: list[list[int]]) -> str | None:
    for desde, hasta in bandas:
        if desde <= horizonte <= hasta:
            return f"{desde}-{hasta}"
    return None


def emparejar(pronosticos: pd.DataFrame, observado: pd.DataFrame
              ) -> pd.DataFrame:
    """Cruza cada pronóstico con lo que de verdad ocurrió ese día.

    El cruce es por municipio, variable y fecha objetivo. Solo sobreviven
    los pares completos: un pronóstico sin observación no aporta nada, y
    contarlo como cero contaminaría el promedio.
    """
    if pronosticos.empty or observado.empty:
        return pd.DataFrame()

    p = pronosticos.copy()
    o = observado.copy()
    p["objetivo"] = pd.to_datetime(p["objetivo"]).dt.date
    o["fecha"] = pd.to_datetime(o["fecha"]).dt.date

    # De lo observado solo interesa la fuente primaria: mezclar MSWEP y
    # CHIRPS como referencia daría un desfase medido contra dos varas.
    o = o.drop_duplicates(subset=["fecha", "municipio", "variable"], keep="first")
    o = o[["fecha", "municipio", "variable", "valor"]].rename(
        columns={"valor": "observado", "fecha": "objetivo"}
    )

    return p.merge(o, on=["objetivo", "municipio", "variable"], how="inner")


def calcular(pares: pd.DataFrame, bandas: list[list[int]],
             pares_minimos: int = 30) -> pd.DataFrame:
    """Desfase por municipio, modelo, variable, mes y banda de horizonte.

    Devuelve también cuántos pares lo respaldan y la dispersión del error.
    Con pocos pares el estimado no es confiable y el tablero debe mostrar la
    anomalía sin corregir, declarándolo.
    """
    if pares.empty:
        return pd.DataFrame()

    df = pares.copy()
    df["diferencia"] = df["valor"] - df["observado"]
    df["mes"] = pd.to_datetime(df["objetivo"]).dt.month
    df["horizonte_banda"] = [banda(h, bandas) for h in df["horizonte"]]
    df = df[df.horizonte_banda.notna()]

    agregado = (
        df.groupby(CLAVE)
        .agg(
            desfase=("diferencia", "mean"),
            dispersion=("diferencia", "std"),
            error_absoluto=("diferencia", lambda s: s.abs().mean()),
            pares=("diferencia", "size"),
        )
        .reset_index()
    )

    agregado["confiable"] = agregado.pares >= pares_minimos
    for col in ("desfase", "dispersion", "error_absoluto"):
        agregado[col] = agregado[col].round(3)
    return agregado


def aplicar(pronosticos: pd.DataFrame, desfases: pd.DataFrame,
            bandas: list[list[int]], variables: list[str],
            activo: bool = True) -> pd.DataFrame:
    """Resta el desfase a los pronósticos. Marca cuáles quedaron corregidos.

    Solo se corrigen las variables donde el sesgo es aditivo y estable: Tmax
    y Tmin. La precipitación tiene sesgo multiplicativo y sobre todo de
    frecuencia, y la humedad está acotada entre 0 y 100, así que una resta
    la sacaría de rango. Para esas dos se presenta la probabilidad de
    superar el umbral, que es mucho menos sensible al sesgo.
    """
    if pronosticos.empty:
        return pronosticos

    df = pronosticos.copy()
    df["corregido"] = False
    df["desfase_aplicado"] = 0.0

    if not activo or desfases.empty:
        return df

    df["mes"] = pd.to_datetime(df["objetivo"]).dt.month
    df["horizonte_banda"] = [banda(h, bandas) for h in df["horizonte"]]

    usables = desfases[desfases.confiable][CLAVE + ["desfase"]]
    unido = df.merge(usables, on=CLAVE, how="left")

    aplicable = unido.variable.isin(variables) & unido.desfase.notna()
    unido.loc[aplicable, "valor"] = (
        unido.loc[aplicable, "valor"] - unido.loc[aplicable, "desfase"]
    ).round(2)
    unido.loc[aplicable, "corregido"] = True
    unido.loc[aplicable, "desfase_aplicado"] = unido.loc[aplicable, "desfase"]

    return unido.drop(columns=["mes", "horizonte_banda", "desfase"])


def anomalia(pronosticos: pd.DataFrame, climatologia: pd.DataFrame
             ) -> pd.DataFrame:
    """Anomalía: pronóstico ya corregido menos la climatología del día.

    La climatología está indexada por día del año, no por fecha, para que
    sirva cualquier año. El 29 de febrero se mapea al 28 porque la serie de
    referencia se construyó con 365 días.
    """
    if pronosticos.empty or climatologia.empty:
        df = pronosticos.copy()
        df["anomalia"] = None
        return df

    df = pronosticos.copy()
    fechas = pd.to_datetime(df["objetivo"])
    bisiesto_tardio = fechas.dt.is_leap_year & (fechas.dt.month > 2)
    df["dia_del_anio"] = fechas.dt.dayofyear - bisiesto_tardio.astype(int)

    ref = climatologia[["municipio", "variable", "dia_del_anio", "valor"]].rename(
        columns={"valor": "normal"}
    )
    unido = df.merge(ref, on=["municipio", "variable", "dia_del_anio"], how="left")
    unido["anomalia"] = (unido["valor"] - unido["normal"]).round(2)
    return unido.drop(columns=["dia_del_anio"])
