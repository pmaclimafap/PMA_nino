"""Exportación del módulo de pronóstico al JSON que lee el tablero.

Separado de exportar.py porque aquel traduce al esquema heredado del
tablero de monitoreo y este define uno nuevo. Mezclarlos ataría el diseño
del pronóstico a decisiones tomadas para otra pantalla.

Estructura:

    {
      "generado_en": "...",
      "emisiones": {"wrf_ideam": "2026-10-01", ...},
      "modelos": {"gfs": {"nombre": ..., "tipo": ..., "horizonte_max": ...}},
      "referencia": {"climatologia": "1991-2020", "desfase_aplicado": true},
      "municipios": {
        "el_paso": {
          "dias":   [ {objetivo, horizonte, icono, tmax, tmax_anomalia, ...} ],
          "series": { "tmax": {"objetivo": [...],
                               "agregado": [...], "banda_sup": [...],
                               "banda_inf": [...],
                               "modelos": {"gfs": [...], ...}} }
        }
      }
    }

Los días alimentan las tarjetas; las series, el gráfico. El tramo observado
del gráfico no va aquí: lo aporta el módulo de monitoreo, y unirlos en el
frontend es lo que produce la línea continua sin costura.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from pipeline import configuracion as config

# Umbrales de lluvia para escoger el icono, en milímetros por día.
# No son los umbrales agronómicos: son solo para la presentación.
ICONO_LLUVIA = [(20.0, "storm"), (10.0, "rain"), (2.0, "rainlow")]


def _limpio(v):
    """None para NaN e infinitos: JSON no los admite y rompen el parseo."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    return None if (math.isnan(f) or math.isinf(f)) else round(f, 2)


def icono(lluvia_mm: float | None, probabilidad: float | None,
          tmax: float | None, umbral_tmax: float | None) -> str:
    """Icono del día, de la biblioteca del generador de infografías.

    La lluvia manda sobre el calor: un día caluroso y lluvioso se comunica
    mejor con nubes que con sol. Y si los modelos no se ponen de acuerdo
    sobre si llueve, se usa 'part' en lugar de elegir un bando.
    """
    if probabilidad is not None and 0.35 <= probabilidad <= 0.65:
        return "part"

    if lluvia_mm is not None and probabilidad is not None and probabilidad > 0.5:
        for minimo, nombre in ICONO_LLUVIA:
            if lluvia_mm >= minimo:
                return nombre

    if tmax is not None and umbral_tmax is not None and tmax > umbral_tmax:
        return "thermo_hot"
    if probabilidad is not None and probabilidad > 0.5:
        return "cloudy"
    return "sun"


def _dias(resumen: pd.DataFrame, umbral_tmax: float | None) -> list[dict]:
    """Tarjetas: una por día, desde mañana."""
    salida = []
    for r in resumen.sort_values("objetivo").itertuples():
        if getattr(r, "horizonte", 1) < 1:
            continue        # el día de la corrida ya es pasado para el tablero

        lluvia = _limpio(getattr(r, "precipitacion", None))
        prob = _limpio(getattr(r, "probabilidad_lluvia", None))
        tmax = _limpio(getattr(r, "tmax", None))

        salida.append({
            "objetivo": str(r.objetivo),
            "horizonte": int(r.horizonte),
            "icono": icono(lluvia, prob, tmax, umbral_tmax),
            "tmax": tmax,
            "tmin": _limpio(getattr(r, "tmin", None)),
            "tmax_anomalia": _limpio(getattr(r, "tmax_anomalia", None)),
            "tmin_anomalia": _limpio(getattr(r, "tmin_anomalia", None)),
            "precipitacion": lluvia,
            "probabilidad_lluvia": prob,
            "humedad_relativa": _limpio(getattr(r, "humedad_relativa", None)),
            "predictibilidad": _limpio(getattr(r, "predictibilidad", None)),
            "confianza": getattr(r, "confianza", None),
            "variable_limitante": getattr(r, "variable_limitante", None),
            "modelos": int(getattr(r, "modelos", 0)),
        })
    return salida


def _series(agregado: pd.DataFrame, detalle: pd.DataFrame,
            municipio: str) -> dict:
    """Series del gráfico: agregado, banda de dispersión y cada modelo.

    La banda es el agregado más y menos una desviación entre modelos. Es lo
    que comunica la incertidumbre directamente: donde se abre, los modelos
    discrepan.
    """
    salida: dict[str, dict] = {}
    ag = agregado[agregado.municipio == municipio]
    det = detalle[detalle.municipio == municipio]

    for variable, grupo in ag.groupby("variable"):
        grupo = grupo.sort_values("objetivo")
        fechas = [str(o) for o in grupo.objetivo]
        valores = [_limpio(v) for v in grupo.valor]
        desv = [_limpio(v) or 0.0 for v in grupo.dispersion]

        bloque = {
            "objetivo": fechas,
            "agregado": valores,
            "banda_sup": [
                None if v is None else round(v + d, 2)
                for v, d in zip(valores, desv)
            ],
            "banda_inf": [
                None if v is None else round(v + 0 - d, 2)
                for v, d in zip(valores, desv)
            ],
            "predictibilidad": [_limpio(v) for v in grupo.predictibilidad],
            "modelos": {},
        }
        if "anomalia" in grupo.columns:
            bloque["anomalia"] = [_limpio(v) for v in grupo.anomalia]

        # Cada modelo alineado con las MISMAS fechas del agregado. Un modelo
        # que no llega hasta el final deja None, no desaparece: así el
        # frontend corta su línea en lugar de estirarla.
        por_modelo = det[det.variable == variable]
        for modelo, g in por_modelo.groupby("modelo"):
            mapa = dict(zip((str(o) for o in g.objetivo), g.valor))
            bloque["modelos"][modelo] = [_limpio(mapa.get(f)) for f in fechas]

        salida[variable] = bloque

    return salida


def construir(agregado: pd.DataFrame, resumen: pd.DataFrame,
              detalle: pd.DataFrame, emisiones: dict[str, str],
              desfase_aplicado: bool = False) -> dict:
    cfg = config.pronostico()
    umbrales = config.umbrales()["sistemas_productivos"]
    municipios = config.municipios()

    salida_mun: dict[str, dict] = {}
    for m in municipios:
        res = resumen[resumen.municipio == m.id]
        if res.empty:
            continue
        umbral = umbrales.get(m.sistema_principal, {}).get("tmax")
        salida_mun[m.id] = {
            "nombre": m.nombre,
            "departamento": m.departamento,
            # Para centrar el mapa de Windy sin que el frontend tenga que
            # cargar el geojson entero.
            "lat": round(m.lat, 4),
            "lon": round(m.lon, 4),
            "sistema_principal": m.sistema_principal,
            "umbral_tmax": umbral,
            "umbral_hr": umbrales.get(m.sistema_principal, {}).get(
                "humedad_relativa"),
            "dias": _dias(res, umbral),
            "series": _series(agregado, detalle, m.id),
        }

    return {
        "generado_en": datetime.now(timezone.utc).isoformat(),
        "emisiones": emisiones,
        "modelos": {
            k: {"nombre": v["nombre"], "tipo": v["tipo"],
                "horizonte_max": v["horizonte_max"],
                "resolucion_km": v.get("resolucion_km")}
            for k, v in cfg["modelos"].items()
        },
        "referencia": {
            "climatologia": f"ERA5 {cfg['climatologia']['periodo'][0]}-"
                            f"{cfg['climatologia']['periodo'][1]}",
            # El tablero DEBE declarar si el valor mostrado está corregido:
            # sin desfase aplicado la anomalía arrastra el sesgo del modelo.
            "desfase_aplicado": desfase_aplicado,
        },
        "municipios": salida_mun,
    }


def escribir(datos: dict, destino: Path) -> Path:
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(
        json.dumps(datos, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return destino
