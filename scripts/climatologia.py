"""climatologia.py — Referencia contra la que se mide la anomalía.

Se ejecuta UNA SOLA VEZ, no mensualmente. Treinta años de ERA5 por punto
desde la API histórica de Open-Meteo: una llamada por municipio, sin Drive y
sin cuotas. Produce un valor por municipio, variable y día del año.

POR QUÉ ERA5 Y NO OTRA COSA

ERA5 no es dato observado y también está sesgado. No importa: se usa como
REGLA DE MEDIR, no como verdad. La anomalía es una diferencia, y el error de
la regla se cancela mientras todo se mida con la misma.

Lo que aporta y ningún modelo puede: QUÉ ES NORMAL. Eso requiere décadas. El
archivo de pronósticos tiene unos meses, y además son de El Niño fuerte: una
referencia construida con eso daría anomalía cero durante el junio-julio-
agosto más cálido desde 1950.

POR QUÉ POR DÍA DEL AÑO

Si el sesgo de ERA5 tuviera estacionalidad, un promedio anual único no lo
cancelaría. Por día del año sí, siempre que el sesgo del día concreto se
parezca al del mismo día en el promedio de 30 años.

Uso:
    python scripts/climatologia.py [--seco] [--desde 1991] [--hasta 2020]
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from pipeline import almacenamiento as alm, configuracion as config  # noqa: E402
from pipeline.fuentes import open_meteo  # noqa: E402

lineas: list[str] = []


def log(t: str = "") -> None:
    print(t, flush=True)
    lineas.append(t)


def volcar_resumen() -> None:
    destino = os.environ.get("GITHUB_STEP_SUMMARY")
    if destino:
        with open(destino, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lineas) + "\n")


def main() -> int:
    cfg = config.pronostico()["climatologia"]
    ap = argparse.ArgumentParser()
    ap.add_argument("--seco", action="store_true")
    ap.add_argument("--desde", type=int, default=cfg["periodo"][0])
    ap.add_argument("--hasta", type=int, default=cfg["periodo"][1])
    args = ap.parse_args()

    municipios = config.municipios()
    # Solo las variables que ERA5 publica como diarias nativas. La humedad
    # relativa no está entre ellas, y su anomalía tampoco se usa: se presenta
    # como probabilidad de superar el umbral, que no necesita referencia.
    variables = {
        "tmax": "temperature_2m_max",
        "tmin": "temperature_2m_min",
        "precipitacion": "precipitation_sum",
    }

    log("# Climatología de referencia")
    log()
    log(f"ERA5 {args.desde}-{args.hasta} · {len(municipios)} municipios · "
        f"{len(variables)} variables")
    log()

    partes, fallos = [], []
    for m in municipios:
        punto = open_meteo.Punto(m.id, m.lat, m.lon)
        try:
            clim = open_meteo.climatologia(punto, variables, args.desde, args.hasta)
            clim = open_meteo.suavizar(clim, cfg["suavizado_dias"])
            partes.append(clim)
            resumen = clim[clim.variable == "tmax"].valor
            log(f"- {m.nombre}: {len(clim)} filas · "
                f"Tmax normal entre {resumen.min():.1f} y {resumen.max():.1f} °C")
        except Exception as e:
            fallos.append(f"{m.nombre}: {e}")
            print(f"::warning::climatología de {m.nombre}: {e}", flush=True)

    if not partes:
        print("::error::No se obtuvo climatología de ningún municipio.")
        return 1

    clim = pd.concat(partes, ignore_index=True)
    clim["calculado"] = datetime.now(timezone.utc).date().isoformat()
    clim["suavizado_dias"] = cfg["suavizado_dias"]

    log()
    log(f"**{len(clim)} filas** · "
        f"{clim.municipio.nunique()} municipios × {clim.variable.nunique()} "
        f"variables × 365 días")

    # Un municipio con menos de 365 días por variable quedaría con huecos en
    # la anomalía justo en las fechas faltantes, y eso no se nota en el
    # tablero: simplemente no aparecería el dato.
    cuenta = clim.groupby(["municipio", "variable"]).size()
    incompletos = cuenta[cuenta != 365]
    if not incompletos.empty:
        log()
        log("Series incompletas (deberían ser 365 días):")
        for (mun, var), n in incompletos.items():
            log(f"- {mun} / {var}: {n} días")

    if fallos:
        log()
        log(f"Municipios sin climatología: {len(fallos)}. "
            "La anomalía no se podrá calcular para ellos.")

    if args.seco:
        log()
        log("Modo seco: no se escribe.")
        log("```")
        muestra = clim[(clim.variable == "tmax") &
                       (clim.dia_del_anio.isin([1, 91, 182, 274]))]
        log(muestra.pivot(index="municipio", columns="dia_del_anio",
                          values="valor").to_string())
        log("```")
        return 0

    alm.escribir(
        clim, alm.ruta("climatologia"),
        f"climatología ERA5 {args.desde}-{args.hasta}: {len(clim)} filas",
    )
    log()
    log(f"Escrito en `{alm.ruta('climatologia')}`.")
    log()
    log("> Este archivo NO se recalcula mensualmente. Solo cambia si se "
        "decide cambiar el período de referencia, y entonces cambian todas "
        "las anomalías publicadas.")
    return 0


if __name__ == "__main__":
    try:
        codigo = main()
    except Exception:
        log("```")
        log(traceback.format_exc())
        log("```")
        codigo = 1
    finally:
        volcar_resumen()
    sys.exit(codigo)
