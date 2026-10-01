"""desfases.py — Estima el desfase de cada modelo contra la referencia.

Cruza el archivo de pronósticos emitidos con lo observado y promedia la
RESTA día por día. Se ejecuta el día 1 de cada mes.

POR QUÉ MENSUAL Y NO UNA SOLA VEZ

Estacionalidad: el error en Tmax no es el mismo en temporada seca que en
lluviosa, porque el error de nubosidad cambia.

Cambios de versión: una actualización de modelo desplaza el desfase y una
estimación vieja queda desfasada sin aviso. ECMWF pasó al ciclo 50R1 en mayo
de 2026.

Por eso el archivo guarda el desfase POR MES y conserva el historial: esa
serie es lo que detecta un cambio de modelo que nadie anunció.

Uso:
    HF_TOKEN=... python scripts/desfases.py [--seco] [--meses 12]
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from pipeline import (  # noqa: E402
    almacenamiento as alm,
    configuracion as config,
    desfase as dsf,
)

lineas: list[str] = []


def log(t: str = "") -> None:
    print(t, flush=True)
    lineas.append(t)


def volcar_resumen() -> None:
    destino = os.environ.get("GITHUB_STEP_SUMMARY")
    if destino:
        with open(destino, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lineas) + "\n")


def leer_pronosticos(meses: int) -> pd.DataFrame:
    """Todas las capturas archivadas dentro de la ventana."""
    from huggingface_hub import list_repo_files

    cfg = config.fuentes()["almacenamiento"]
    archivos = list_repo_files(
        repo_id=cfg["repo"], repo_type=cfg["tipo"],
        token=os.environ.get("HF_TOKEN"),
    )
    corte = date.today() - timedelta(days=meses * 31)
    objetivo = []
    for a in archivos:
        if not (a.startswith("pronostico/tiempo/") and a.endswith(".parquet")):
            continue
        try:
            if date.fromisoformat(Path(a).stem) >= corte:
                objetivo.append(a)
        except ValueError:
            continue

    if not objetivo:
        return pd.DataFrame()
    partes = [alm.leer(a) for a in sorted(objetivo)]
    return pd.concat([p for p in partes if not p.empty], ignore_index=True)


def leer_observado(meses: int) -> pd.DataFrame:
    """Serie observada del mismo período, desde el módulo de monitoreo."""
    hoy = date.today()
    partes = []
    for i in range(meses + 1):
        mes = (hoy.replace(day=1) - timedelta(days=i * 28)).replace(day=1)
        p = alm.leer(alm.ruta("monitoreo_puntos", anio=mes.year, mes=mes.month))
        if not p.empty:
            partes.append(p)
    return pd.concat(partes, ignore_index=True) if partes else pd.DataFrame()


def main() -> int:
    cfg = config.pronostico()["desfase"]
    ap = argparse.ArgumentParser()
    ap.add_argument("--seco", action="store_true")
    ap.add_argument("--meses", type=int, default=12,
                    help="Ventana de archivo a considerar")
    args = ap.parse_args()

    inicio = datetime.now(timezone.utc)
    log("# Desfases por modelo")
    log()
    log(f"Ventana: últimos {args.meses} meses · "
        f"pares mínimos para confiar: {cfg['pares_minimos']}")
    log()

    pron = leer_pronosticos(args.meses)
    obs = leer_observado(args.meses)
    log(f"Pronósticos archivados: {len(pron)} filas.")
    log(f"Observado: {len(obs)} filas.")

    if pron.empty or obs.empty:
        log()
        log("Sin datos suficientes. El archivo de pronósticos empieza a "
            "llenarse con la primera corrida del módulo de tiempo.")
        return 0

    pares = dsf.emparejar(pron, obs)
    log(f"Pares pronóstico-observado: **{len(pares)}**.")
    if pares.empty:
        log()
        log("Ningún pronóstico tiene observación correspondiente todavía. "
            "Es lo esperable si el archivo arrancó hace pocos días: un "
            "pronóstico a 16 días no se puede verificar hasta que pasen.")
        return 0

    tabla = dsf.calcular(pares, cfg["bandas_horizonte"], cfg["pares_minimos"])
    log()
    log(f"**{len(tabla)} combinaciones** de municipio, modelo, variable, "
        f"mes y banda. {int(tabla.confiable.sum())} con pares suficientes.")

    # El desfase debe CRECER con el horizonte. Si no lo hace, probablemente
    # haya pocos pares o un error de emparejamiento.
    log()
    log("Desfase medio por modelo y banda de horizonte (°C o mm):")
    log()
    resumen = (
        tabla[tabla.confiable]
        .pivot_table(index=["modelo", "variable"], columns="horizonte_banda",
                     values="desfase", aggfunc="mean")
        .round(2)
    )
    if resumen.empty:
        log("_Ninguna combinación tiene pares suficientes todavía._")
    else:
        log("```")
        log(resumen.to_string())
        log("```")

    previo = alm.leer(alm.ruta("sesgos"))
    if not previo.empty:
        comparable = previo.merge(
            tabla[dsf.CLAVE + ["desfase"]], on=dsf.CLAVE,
            how="inner", suffixes=("_antes", "_ahora"),
        )
        if not comparable.empty:
            cambio = (comparable.desfase_ahora - comparable.desfase_antes).abs()
            log()
            log(f"Cambio respecto al mes pasado: mediana "
                f"{cambio.median():.2f}, máximo {cambio.max():.2f}.")
            grandes = comparable[cambio > 1.0]
            if not grandes.empty:
                log()
                log(f"> {len(grandes)} combinaciones se movieron más de 1 "
                    "unidad. Un salto así suele indicar un cambio de versión "
                    "del modelo, no variabilidad del clima.")

    tabla["calculado"] = date.today().isoformat()

    if args.seco:
        log()
        log("Modo seco: no se escribe.")
        return 0

    alm.escribir(tabla, alm.ruta("sesgos"),
                 f"desfases: {len(tabla)} combinaciones, {len(pares)} pares")
    log()
    log(f"Escrito en `{alm.ruta('sesgos')}`.")

    alm.registrar_corrida({
        "inicio": inicio.isoformat(),
        "fin": datetime.now(timezone.utc).isoformat(),
        "proceso": "desfases",
        "pares": len(pares),
        "combinaciones": len(tabla),
        "confiables": int(tabla.confiable.sum()),
    })
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
