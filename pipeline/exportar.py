"""Exportación al esquema que consume el tablero.

El tablero (dashboard-monitoreo.html) ya está construido y validado, así que
en lugar de reescribirlo se traduce nuestro esquema al suyo. Espera:

    {
      "generado_en": "ISO datetime",
      "municipios": {
        "Los Palmitos (Sucre)": {
          "precip":  [{date, value}, ...],
          "tavg":    [...], "tmin": [...], "tmax": [...], "relhum": [...],
          "grid":    [{lon, lat, valores: [...]}],   // alineado con precip
          "grid_tavg": [...], "grid_relhum": [...]
        }
      },
      "observaciones": [...],
      "enso": {valor, categoria, actualizado, fuente_url}
    }

La grilla NO es opcional: si falta, el tablero genera una simulada y muestra
variación espacial inventada dentro del municipio.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from pipeline import configuracion as config

# Nuestros nombres -> los del tablero
VARIABLES = {
    "precipitacion": "precip",
    "temperatura": "tavg",
    "tmin": "tmin",
    "tmax": "tmax",
    "humedad_relativa": "relhum",
}

# Solo tres variables tienen mapa en el tablero
GRILLAS = {
    "precipitacion": "grid",
    "temperatura": "grid_tavg",
    "humedad_relativa": "grid_relhum",
}


def _nombres_tablero() -> dict[str, str]:
    """id interno -> nombre que el tablero usa como clave."""
    crudo = config._municipios_raw()["municipios"]
    return {m["id"]: m.get("nombre_tablero", m["nombre"]) for m in crudo}


def _serie(grupo: pd.DataFrame) -> list[dict]:
    g = grupo.sort_values("fecha")
    return [
        {"date": f.strftime("%Y-%m-%d"), "value": round(float(v), 2)}
        for f, v in zip(g.fecha, g.valor)
    ]


def _grilla(grupo: pd.DataFrame, fechas: list[pd.Timestamp]) -> list[dict]:
    """Transpone de una fila por celda y día a una serie por celda.

    El tablero espera `valores` alineado posición por posición con las fechas
    de la serie puntual de esa variable. Si una celda no tiene dato en un día,
    se rellena con None para no desalinear el arreglo.
    """
    celdas = []
    for (lat, lon), datos in grupo.groupby(["lat", "lon"]):
        por_fecha = dict(zip(datos.fecha, datos.valor))
        celdas.append({
            "lon": round(float(lon), 4),
            "lat": round(float(lat), 4),
            "valores": [
                round(float(por_fecha[f]), 2) if f in por_fecha else None
                for f in fechas
            ],
        })
    return celdas


def construir(
    puntos: pd.DataFrame,
    grilla: pd.DataFrame | None = None,
    enso: dict | None = None,
    observaciones: list[dict] | None = None,
) -> dict:
    nombres = _nombres_tablero()
    puntos = puntos.copy()
    puntos["fecha"] = pd.to_datetime(puntos["fecha"])

    if grilla is not None and not grilla.empty:
        grilla = grilla.copy()
        grilla["fecha"] = pd.to_datetime(grilla["fecha"])

    salida_mun: dict[str, dict] = {}

    for mid, etiqueta in nombres.items():
        del_mun = puntos[puntos.municipio == mid]
        if del_mun.empty:
            continue

        bloque: dict = {}
        fechas_por_var: dict[str, list] = {}

        for interno, externo in VARIABLES.items():
            serie = del_mun[del_mun.variable == interno]
            if serie.empty:
                bloque[externo] = []
                continue
            serie = serie.sort_values("fecha")
            bloque[externo] = _serie(serie)
            fechas_por_var[interno] = list(serie.fecha)

        if grilla is not None and not grilla.empty:
            g_mun = grilla[grilla.municipio == mid]
            for interno, campo in GRILLAS.items():
                g_var = g_mun[g_mun.variable == interno]
                fechas = fechas_por_var.get(interno, [])
                bloque[campo] = (
                    _grilla(g_var, fechas) if not g_var.empty and fechas else []
                )

        salida_mun[etiqueta] = bloque

    return {
        "generado_en": datetime.now(timezone.utc).isoformat(),
        "municipios": salida_mun,
        "observaciones": observaciones or [],
        "enso": enso or {
            "valor": None,
            "categoria": "Pendiente de conectar",
            "actualizado": "",
            "fuente_url": (
                "https://www.cpc.ncep.noaa.gov/products/analysis_monitoring/"
                "enso_advisory/ensodisc.shtml"
            ),
        },
    }


def escribir(datos: dict, destino: Path) -> Path:
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(
        json.dumps(datos, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return destino
