"""diario.py — Pipeline diario de monitoreo.

Flujo:
  1. Listar metadata de origen, sin descargar nada.
  2. Comparar contra estado/ingesta: ¿nuevo, o revisado dentro de la ventana?
  3. Descargar solo eso. Un día normal son 2 o 3 archivos, no 60.
  4. Recortar de inmediato: global -> 4 departamentos -> 7 municipios.
  5. Guardar en Hugging Face.
  6. Leer la ventana completa, calcular indicadores y publicar el JSON.
  7. Descartar. El runner muere con los .nc dentro.

Idempotencia: la ventana se reconstruye desde el almacenamiento, no desde lo
descargado hoy. Si un día falla, al siguiente se autocorrige solo.

Degradación: si una variable falla, se registra y el resto continúa. El
portal no queda en blanco por una fuente caída.

Uso:
    python scripts/diario.py [--dias 60] [--seco]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

import geopandas as gpd  # noqa: E402

from pipeline import (  # noqa: E402
    almacenamiento as alm,
    configuracion as config,
    enso,
    exportar,
    extraccion,
    indicadores,
)
from pipeline.fuentes import gloh2o  # noqa: E402

DIAS_VENTANA = 60          # lo que se publica
DIAS_PUBLICADOS = 60

# Versión del extractor. El registro de ingesta guarda con qué versión se
# procesó cada archivo, y un archivo con versión menor se vuelve a procesar
# aunque ya esté registrado.
#
# Sin esto, el registro solo sabe QUE procesó un archivo, no QUÉ extrajo de
# él: al agregar la grilla, los 300 archivos ya estaban marcados como hechos
# y la grilla nunca se habría extraído.
#
#   1  solo series puntuales
#   2  series puntuales + grilla por pixel
VERSION_EXTRACCION = 2
DIR_PUBLICO = Path("publico/data")

lineas: list[str] = []


def log(t: str = "") -> None:
    print(t, flush=True)
    lineas.append(t)


def volcar_resumen() -> None:
    destino = os.environ.get("GITHUB_STEP_SUMMARY")
    if destino:
        with open(destino, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lineas) + "\n")


# ---------------------------------------------------------------------------
# 1 y 2. Qué hay que traer
# ---------------------------------------------------------------------------

def decidir_descargas(servicio, ingesta: pd.DataFrame, fechas: list[date],
                      forzar: bool = False) -> tuple[list[tuple], list[dict]]:
    """Compara metadata de origen contra el registro propio.

    Un archivo se procesa si nunca se vio, o si su fecha de modificación en
    origen es más reciente que la registrada. Esto último cubre las revisiones
    del NRT sin lógica especial.
    """
    ventana_revision = config.dias_reverificacion()
    hoy = date.today()
    pendientes, faltantes = [], []

    previos: dict[tuple, tuple] = {}
    if not ingesta.empty and not forzar:
        tiene_version = "version" in ingesta.columns
        for r in ingesta.itertuples():
            version = getattr(r, "version", 1) if tiene_version else 1
            previos[(r.carpeta, r.archivo)] = (
                r.modificado_origen,
                int(version) if pd.notna(version) else 1,
            )

    for var, cfg in config.variables_activas().items():
        clave = cfg["clave_carpeta"]
        for fecha in fechas:
            antiguedad = (hoy - fecha).days
            nombre = gloh2o.nombre_archivo(fecha)
            registro = previos.get((clave, nombre))
            visto = registro[0] if registro else None
            al_dia = bool(registro) and registro[1] >= VERSION_EXTRACCION

            # Ya procesado con el extractor actual y fuera de la ventana de
            # revisión: no hay nada que mirar.
            if al_dia and antiguedad > ventana_revision:
                continue

            try:
                remoto = gloh2o.buscar(servicio, cfg["carpeta_id"], nombre)
            except Exception as e:
                faltantes.append({"variable": var, "fecha": str(fecha),
                                  "motivo": f"consulta falló: {e}"})
                continue

            if remoto is None:
                if antiguedad <= 3:
                    continue  # rezago normal del NRT, no es un fallo
                faltantes.append({"variable": var, "fecha": str(fecha),
                                  "motivo": "no publicado"})
                continue

            # Se reprocesa si cambió en origen, o si se extrajo con una
            # versión anterior del extractor.
            if remoto.cambio_respecto_a(visto) or not al_dia:
                pendientes.append((var, cfg, fecha, remoto))

    return pendientes, faltantes


# ---------------------------------------------------------------------------
# 3, 4 y 5. Traer, reducir, guardar
# ---------------------------------------------------------------------------

def procesar(servicio, pendientes: list[tuple], tmp: Path
             ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, list[dict]]:
    municipios = config.municipios()
    hoy = date.today()
    # Geometrias para el recorte por poligono. Solo tres variables tienen
    # mapa en el tablero; extraer la grilla de las cinco seria desperdicio.
    geo = gpd.read_file(config.ruta_geojson()).set_index("id")
    filas, celdas, registros, errores = [], [], [], []

    for var, cfg, fecha, remoto in pendientes:
        destino = tmp / f"{var}_{remoto.nombre}"
        try:
            gloh2o.descargar(servicio, remoto, destino)
            da = extraccion.abrir(destino, cfg["candidatos"])
            sub = extraccion.recortar_region(da)
            df = extraccion.extraer_puntos(sub, municipios)
            da.close()

            madurez = config.madurez((hoy - fecha).days)
            ahora = datetime.now(timezone.utc)

            if var in exportar.GRILLAS:
                plano = sub.isel(time=0) if "time" in sub.dims else sub
                for m in municipios:
                    for c in extraccion.extraer_grilla(
                        plano, geo.loc[m.id, "geometry"]
                    ):
                        celdas.append({
                            "fecha": fecha, "municipio": m.id, "variable": var,
                            "lat": c["lat"], "lon": c["lon"], "valor": c["valor"],
                        })
            for r in df.itertuples():
                filas.append({
                    "fecha": fecha,
                    "municipio": r.municipio,
                    "variable": var,
                    "valor": r.valor,
                    "fuente": cfg["clave_carpeta"],
                    "madurez": madurez,
                    "actualizado": ahora,
                })

            registros.append({
                "carpeta": cfg["clave_carpeta"],
                "archivo": remoto.nombre,
                "modificado_origen": remoto.modificado,
                "procesado": datetime.now(timezone.utc).isoformat(),
                "estado": "ok",
                "version": VERSION_EXTRACCION,
            })

        except Exception as e:
            errores.append({"variable": var, "fecha": str(fecha),
                            "motivo": str(e)})
            registros.append({
                "carpeta": cfg["clave_carpeta"],
                "archivo": remoto.nombre,
                "modificado_origen": remoto.modificado,
                "procesado": datetime.now(timezone.utc).isoformat(),
                "estado": f"error: {e}"[:200],
                "version": VERSION_EXTRACCION,
            })
        finally:
            destino.unlink(missing_ok=True)

    return (pd.DataFrame(filas), pd.DataFrame(celdas),
            pd.DataFrame(registros), errores)


# ---------------------------------------------------------------------------
# 6. Publicar
# ---------------------------------------------------------------------------

def publicar(ventana: pd.DataFrame, indicadores_calc: dict,
             faltantes: list[dict], errores: list[dict]) -> None:
    """Escribe lo que consume el tablero.

    Nombre de archivo con fecha: una vez descargado, el navegador lo sirve de
    disco para siempre. El manifest, que sí se revalida, pesa ~1 KB.
    """
    DIR_PUBLICO.mkdir(parents=True, exist_ok=True)
    sello = date.today().isoformat().replace("-", "")

    largo = ventana.copy()
    largo["fecha"] = pd.to_datetime(largo["fecha"]).dt.strftime("%Y-%m-%d")

    series: dict[str, dict] = {}
    for (mid, var), grupo in largo.groupby(["municipio", "variable"]):
        g = grupo.sort_values("fecha")
        series.setdefault(mid, {})[var] = {
            "fechas": g.fecha.tolist(),
            "valores": [round(float(v), 2) for v in g.valor],
            "madurez": g.madurez.tolist(),
        }

    nombre_ventana = f"ventana-{sello}.json"
    (DIR_PUBLICO / nombre_ventana).write_text(
        json.dumps(
            {
                "generado": datetime.now(timezone.utc).isoformat(),
                "dias": DIAS_PUBLICADOS,
                "series": series,
                "indicadores": indicadores_calc,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )

    # El tablero prototipo cae en datos simulados si no encuentra su archivo.
    # Este manifest es la señal explícita de si hay datos reales o no.
    (DIR_PUBLICO / "manifest.json").write_text(
        json.dumps(
            {
                "generado": datetime.now(timezone.utc).isoformat(),
                "datos_reales": not ventana.empty,
                "archivos": {"monitoreo": nombre_ventana},
                "ultima_fecha_con_dato": (
                    str(pd.to_datetime(ventana.fecha).max().date())
                    if not ventana.empty else None
                ),
                "municipios_con_alertas_calidad": [
                    mid for mid, v in indicadores_calc.items()
                    if v.get("tiene_alertas_calidad")
                ],
                "incidencias": {"faltantes": faltantes, "errores": errores},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    log(f"Publicado: {nombre_ventana} "
        f"({(DIR_PUBLICO / nombre_ventana).stat().st_size / 1024:.0f} KB)")


# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dias", type=int, default=DIAS_VENTANA)
    ap.add_argument("--seco", action="store_true",
                    help="No escribe en Hugging Face ni publica")
    ap.add_argument("--forzar", action="store_true",
                    help="Ignora el registro de ingesta y reprocesa todo")
    args = ap.parse_args()

    inicio = datetime.now(timezone.utc)
    hoy = date.today()
    fechas = [hoy - timedelta(days=i) for i in range(1, args.dias + 1)]

    log("# Monitoreo diario")
    log()
    log(f"Corrida {inicio.isoformat(timespec='seconds')} · "
        f"ventana {fechas[-1]} a {fechas[0]}"
        + (" · MODO SECO" if args.seco else "")
        + (" · FORZADO" if args.forzar else ""))
    log()

    servicio = gloh2o.autenticar()
    ingesta = alm.leer_ingesta()
    log(f"Registro de ingesta: {len(ingesta)} archivo(s) ya procesado(s)."
        f" Extractor v{VERSION_EXTRACCION}.")

    pendientes, faltantes = decidir_descargas(
        servicio, ingesta, fechas, forzar=args.forzar
    )
    log(f"Por descargar: **{len(pendientes)}** archivo(s) "
        f"de {len(fechas) * len(config.variables_activas())} posibles.")
    if faltantes:
        log(f"Sin publicar o con fallo de consulta: {len(faltantes)}.")
    log()

    nuevos = celdas = registros = pd.DataFrame()
    errores: list[dict] = []
    if pendientes:
        with tempfile.TemporaryDirectory() as tmp:
            nuevos, celdas, registros, errores = procesar(
                servicio, pendientes, Path(tmp)
            )
        log(f"Extraídas {len(nuevos)} fila(s) y {len(celdas)} celda(s) de grilla."
            + (f" {len(errores)} error(es)." if errores else ""))
    else:
        log("Nada nuevo en origen.")
    log()

    if args.seco:
        log("Modo seco: no se escribe nada.")
        if not nuevos.empty:
            log("```")
            log(nuevos.head(15).to_string(index=False))
            log("```")
        return 1 if errores else 0

    if not nuevos.empty:
        rutas = alm.guardar_observado(nuevos)
        if not celdas.empty:
            rutas += alm.guardar_grilla(celdas)
        alm.guardar_ingesta(ingesta, registros)
        log(f"Guardado en: {', '.join(rutas)}")

    # La ventana se reconstruye desde el almacenamiento, no desde lo que se
    # descargó hoy: así el resultado es el mismo se haya fallado o no antes.
    meses = sorted({(f.year, f.month) for f in fechas})
    partes = [alm.leer(alm.ruta("observado", anio=a, mes=m)) for a, m in meses]
    partes = [p for p in partes if not p.empty]
    ventana = pd.concat(partes, ignore_index=True) if partes else pd.DataFrame()

    if ventana.empty:
        log("Sin datos en el almacenamiento. No se publica.")
        return 1

    ventana["fecha"] = pd.to_datetime(ventana["fecha"])
    ventana = ventana[ventana.fecha >= pd.Timestamp(fechas[-1])]

    # Grilla del mismo rango, para el mapa del tablero
    g_partes = [alm.leer(alm.ruta("observado_grilla", anio=a, mes=m))
                for a, m in meses]
    g_partes = [p for p in g_partes if not p.empty]
    grilla = pd.concat(g_partes, ignore_index=True) if g_partes else pd.DataFrame()
    if not grilla.empty:
        grilla["fecha"] = pd.to_datetime(grilla["fecha"])
        grilla = grilla[grilla.fecha >= pd.Timestamp(fechas[-1])]

    calc = indicadores.calcular(ventana)
    publicar(ventana, calc, faltantes, errores)

    # data.json en el esquema del tablero existente. Sin la grilla el
    # frontend genera una simulada y muestra variacion espacial inventada.
    # El índice ONI se consulta en cada corrida. Si la NOAA no responde,
    # obtener() devuelve valor nulo y el tablero muestra "sin dato": un
    # índice equivocado es peor que ninguno cuando se comunica si viene o
    # no una sequía.
    estado_enso = enso.obtener()
    if estado_enso.get("valor") is None:
        log(f"ENSO: sin dato — {estado_enso.get('categoria')}")
    else:
        log(f"ENSO: {estado_enso['valor']:+.2f} ({estado_enso['categoria']}) "
            f"· {estado_enso['trimestre']} · {estado_enso['historico']['texto']}")

    legado = exportar.construir(ventana, grilla, enso=estado_enso)
    ruta_legado = exportar.escribir(legado, Path("publico/data.json"))
    log(f"Exportado: data.json ({ruta_legado.stat().st_size / 1024:.0f} KB)"
        + ("" if not grilla.empty else
           " — **SIN GRILLA**: el mapa no tendrá datos reales. "
           "Correr con --forzar para extraerla."))

    con_alertas = [m for m, v in calc.items() if v.get("tiene_alertas_calidad")]
    if con_alertas:
        log(f"Alertas de calidad en: {', '.join(con_alertas)}")

    # Siempre se registra la corrida, con o sin novedades: es trazabilidad y
    # además mantiene vivo el cron (GitHub apaga los programados tras 60 días
    # sin actividad en el repositorio).
    alm.registrar_corrida({
        "inicio": inicio.isoformat(),
        "fin": datetime.now(timezone.utc).isoformat(),
        "archivos_descargados": len(pendientes),
        "filas_nuevas": len(nuevos),
        "errores": len(errores),
        "faltantes": len(faltantes),
        "ultima_fecha": str(ventana.fecha.max().date()),
    })

    log()
    log(f"Ventana publicada: {ventana.fecha.nunique()} día(s), "
        f"hasta {ventana.fecha.max().date()}.")
    return 1 if errores else 0


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
