"""pronostico.py — Captura diaria de pronósticos de tiempo.

Reúne tres fuentes en una sola tabla y la archiva:

    Open-Meteo    5 modelos deterministas + WeatherNext 2 (ensamble)
    WRF-IDEAM     modelo regional de 10 km sobre Colombia
    CHIRPS-GEFS   lluvia, ya corregida por sesgo

Lo que se guarda es la EMISIÓN, no el agregado. El promedio ponderado, la
dispersión y la probabilidad se recalculan al publicar: son derivados, y
guardarlos obligaría a regenerarlos cada vez que cambie un peso.

El archivo es INMUTABLE. Lo que se pronosticó hoy para dentro de cinco días
no se puede volver a obtener de ninguna parte: cuando sale la corrida
siguiente, la anterior desaparece de la fuente. Sin ese registro no se
pueden calibrar los umbrales que activan las medidas anticipatorias, ni
rendir cuentas de qué se anunció y cuándo.

Degradación: cada fuente se intenta por separado. Si una cae, las demás se
archivan igual. Perder un modelo de ocho es una pérdida de precisión; perder
la captura entera es una pérdida de historia.

Uso:
    python scripts/pronostico.py [--seco] [--forzar] [--fecha AAAA-MM-DD]
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from pipeline import (  # noqa: E402
    agregacion,
    almacenamiento as alm,
    configuracion as config,
    desfase as dsf,
    exportar_pronostico,
)
from pipeline.fuentes import chirps, ideam, open_meteo  # noqa: E402

COLUMNAS = ["emision", "objetivo", "horizonte", "municipio",
            "variable", "modelo", "valor"]

lineas: list[str] = []


def log(t: str = "") -> None:
    print(t, flush=True)
    lineas.append(t)


def aviso(t: str) -> None:
    print(f"::warning::{t}", flush=True)


def volcar_resumen() -> None:
    destino = os.environ.get("GITHUB_STEP_SUMMARY")
    if destino:
        with open(destino, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lineas) + "\n")


def _normalizar(df: pd.DataFrame, emision: datetime) -> pd.DataFrame:
    """Añade emisión y horizonte, y deja solo las columnas del contrato.

    El horizonte se mide desde la fecha de la emisión, no desde hoy: una
    corrida 00Z de ayer y una de hoy no tienen la misma anticipación aunque
    apunten al mismo día, y toda comparación entre modelos se hace a igual
    horizonte.
    """
    if df.empty:
        return pd.DataFrame(columns=COLUMNAS)

    df = df.copy()
    df["emision"] = emision
    df["objetivo"] = pd.to_datetime(df["objetivo"]).dt.date
    df["horizonte"] = [(o - emision.date()).days for o in df["objetivo"]]
    # Un horizonte negativo significa que la fuente devolvió días previos a
    # su propia corrida: es un error de parseo, no un pronóstico.
    return df[df.horizonte >= 0][COLUMNAS]


# ---------------------------------------------------------------------------
# Fuentes
# ---------------------------------------------------------------------------

def desde_open_meteo(municipios) -> tuple[pd.DataFrame, list[str]]:
    cfg = config.pronostico()
    captura = cfg["captura"]
    avisos: list[str] = []

    puntos = [open_meteo.Punto(m.id, m.lat, m.lon) for m in municipios]

    deterministas = {
        k: v["id_api"] for k, v in cfg["modelos"].items()
        if v["adaptador"] == "open_meteo"
    }
    diarias = {
        k: v["open_meteo_diaria"] for k, v in cfg["variables"].items()
        if "open_meteo_diaria" in v
    }
    horaria = cfg["variables"]["humedad_relativa"].get("open_meteo_horaria")

    partes: list[pd.DataFrame] = []

    try:
        df, emision = open_meteo.pronostico(
            puntos, deterministas, diarias, horaria,
            dias=captura["horizonte_dias"],
            zona_horaria=captura["zona_horaria"],
        )
        partes.append(_normalizar(df, emision))
        log(f"- Open-Meteo: {len(df)} filas de {len(deterministas)} modelos.")
    except Exception as e:
        avisos.append(f"Open-Meteo deterministas: {e}")

    ensambles = {
        k: v for k, v in cfg["modelos"].items()
        if v["adaptador"] == "open_meteo_ensamble"
    }
    for nombre, mcfg in ensambles.items():
        try:
            df, emision = open_meteo.ensamble(
                puntos, mcfg["id_api"], nombre, diarias,
                dias=captura["horizonte_dias"],
                zona_horaria=captura["zona_horaria"],
            )
            partes.append(_normalizar(df, emision))
            log(f"- {mcfg['nombre']}: {len(df)} filas.")
        except Exception as e:
            avisos.append(f"{mcfg['nombre']}: {e}")

    vacio = pd.DataFrame(columns=COLUMNAS)
    return (pd.concat(partes, ignore_index=True) if partes else vacio), avisos


def desde_ideam(municipios, corrida: date | None) -> tuple[pd.DataFrame, list[str]]:
    puntos = [ideam.Punto(m.id, m.lat, m.lon) for m in municipios]
    try:
        df, emision, avisos = ideam.pronostico(puntos, corrida)
        log(f"- WRF-IDEAM: {len(df)} filas, corrida {emision.date()}.")
        return _normalizar(df, emision), avisos
    except Exception as e:
        return pd.DataFrame(columns=COLUMNAS), [f"WRF-IDEAM: {e}"]


def desde_chirps(municipios, corrida: date | None) -> tuple[pd.DataFrame, list[str]]:
    puntos = [chirps.Punto(m.id, m.lat, m.lon) for m in municipios]
    try:
        df, emision, avisos = chirps.pronostico(puntos, corrida)
        log(f"- CHIRPS-GEFS: {len(df)} filas, corrida {emision.date()}.")
        return _normalizar(df, emision), avisos
    except Exception as e:
        return pd.DataFrame(columns=COLUMNAS), [f"CHIRPS-GEFS: {e}"]


# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seco", action="store_true",
                    help="No escribe en Hugging Face")
    ap.add_argument("--forzar", action="store_true",
                    help="Sobrescribe la captura del día si ya existe")
    ap.add_argument("--fecha", help="Corrida a capturar (AAAA-MM-DD)")
    args = ap.parse_args()

    corrida = date.fromisoformat(args.fecha) if args.fecha else None
    captura = corrida or date.today()
    inicio = datetime.now(timezone.utc)
    municipios = config.municipios()

    log("# Pronóstico de tiempo")
    log()
    log(f"Captura {captura} · {len(municipios)} municipios"
        + (" · MODO SECO" if args.seco else ""))
    log()

    partes, avisos = [], []
    for obtener in (
        lambda: desde_open_meteo(municipios),
        lambda: desde_ideam(municipios, corrida),
        lambda: desde_chirps(municipios, corrida),
    ):
        df, av = obtener()
        if not df.empty:
            partes.append(df)
        avisos += av

    if not partes:
        print("::error::Ninguna fuente respondió. No hay nada que archivar.")
        log("Ninguna fuente respondió.")
        return 1

    todo = pd.concat(partes, ignore_index=True)
    todo = todo.sort_values(["modelo", "municipio", "variable", "objetivo"])
    todo = todo.reset_index(drop=True)

    log()
    log(f"**{len(todo)} filas** · {todo.modelo.nunique()} modelos · "
        f"{todo.variable.nunique()} variables · "
        f"horizonte {todo.horizonte.min()} a {todo.horizonte.max()} días.")
    log()
    log("| modelo | filas | variables | horizonte |")
    log("|---|---|---|---|")
    for modelo, g in todo.groupby("modelo"):
        log(f"| {modelo} | {len(g)} | {g.variable.nunique()} | "
            f"{g.horizonte.max()} d |")

    for a in avisos:
        aviso(a)
    if avisos:
        log()
        log(f"Incidencias: {len(avisos)}. Ver advertencias del job.")

    if args.seco:
        log()
        log("Modo seco: no se escribe.")
        log("```")
        log(todo.head(12).to_string(index=False))
        log("```")
        return 0

    destino = alm.guardar_pronostico_tiempo(todo, captura, forzar=args.forzar)
    log()
    if destino:
        log(f"Archivado en `{destino}`.")
    else:
        log("La captura de hoy ya existe y no se reescribe. "
            "Usar --forzar si de verdad hace falta.")

    # ---- Derivados: se recalculan siempre, nunca se archivan ----
    cfg_desfase = config.pronostico()["desfase"]
    sesgos = alm.leer(alm.ruta("sesgos"))
    clim = alm.leer(alm.ruta("climatologia"))

    corregido = cfg_desfase["aplicar"] and not sesgos.empty
    variables_corregibles = [
        k for k, v in config.pronostico()["variables"].items()
        if v.get("corregir_desfase")
    ]
    ajustado = dsf.aplicar(
        todo, sesgos, cfg_desfase["bandas_horizonte"],
        variables_corregibles, activo=corregido,
    )
    if corregido:
        n = int(ajustado.corregido.sum())
        log(f"Desfase aplicado a {n} de {len(ajustado)} filas.")
    else:
        aviso("Sin desfases estimados todavía: la anomalía se publica SIN "
              "corregir y el tablero lo declara.")

    con_anomalia = dsf.anomalia(ajustado, clim)
    if clim.empty:
        aviso("Sin climatología de referencia: no hay anomalía. "
              "Correr el workflow 05 con que=climatologia.")

    agregado = agregacion.agregar(con_anomalia)
    resumen = agregacion.resumen_dia(agregado)
    log(f"Agregados {len(agregado)} registros · {len(resumen)} días-municipio.")

    emisiones = {
        m: str(pd.to_datetime(g.emision.iloc[0]).date())
        for m, g in todo.groupby("modelo")
    }
    datos = exportar_pronostico.construir(
        agregado, resumen, con_anomalia, emisiones, desfase_aplicado=corregido
    )
    ruta_json = exportar_pronostico.escribir(
        datos, Path("publico/pronostico.json")
    )
    log(f"Exportado: pronostico.json ({ruta_json.stat().st_size / 1024:.0f} KB)")

    alm.registrar_corrida({
        "inicio": inicio.isoformat(),
        "fin": datetime.now(timezone.utc).isoformat(),
        "proceso": "pronostico_tiempo",
        "filas": len(todo),
        "modelos": int(todo.modelo.nunique()),
        "incidencias": len(avisos),
        "desfase_aplicado": bool(corregido),
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
