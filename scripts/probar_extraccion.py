"""probar_extraccion.py — Prueba del flujo completo para unos pocos días.

Descarga, recorta y extrae los 7 municipios para todas las variables activas.
No escribe resultados en ningún lado: es una validación de que la cadena
funciona de punta a punta antes de montar el pipeline diario.

Valida a la vez:
  - que la cuenta de servicio lee las carpetas de GloH2O
  - que los IDs de fuentes.yml son correctos
  - qué nombre interno tiene la variable en cada producto
  - el rezago real del NRT en cada variable
  - las fechas de modificación, base de la ventana de 10 días

Uso:
    GCP_SERVICE_ACCOUNT_KEY='{...}' python scripts/probar_extraccion.py [dias]
"""

from __future__ import annotations

import os
import sys
import tempfile
import traceback
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import configuracion as config, extraccion  # noqa: E402
from pipeline.fuentes import gloh2o  # noqa: E402

DIAS_POR_DEFECTO = 5

lineas: list[str] = []


def log(texto: str = "") -> None:
    print(texto, flush=True)
    lineas.append(texto)


def volcar_resumen() -> None:
    destino = os.environ.get("GITHUB_STEP_SUMMARY")
    if destino:
        with open(destino, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lineas) + "\n")


def main() -> int:
    dias = int(sys.argv[1]) if len(sys.argv) > 1 else DIAS_POR_DEFECTO
    hoy = date.today()
    fechas = [hoy - timedelta(days=i) for i in range(1, dias + 1)]

    municipios = config.municipios()
    variables = config.variables_activas()

    log("# Prueba de extracción")
    log()
    log(f"Fechas: {fechas[-1]} a {fechas[0]} · "
        f"{len(variables)} variables · {len(municipios)} municipios")
    log()

    servicio = gloh2o.autenticar()
    problemas = 0

    with tempfile.TemporaryDirectory() as tmp:
        for var, cfg in variables.items():
            log(f"## {var}  ({cfg['unidad']})")
            log()
            log(f"Carpeta: `{cfg['clave_carpeta']}` → `{cfg['carpeta_id']}`")
            log()

            filas, disponibles = [], 0

            for fecha in fechas:
                nombre = gloh2o.nombre_archivo(fecha)
                try:
                    remoto = gloh2o.buscar(servicio, cfg["carpeta_id"], nombre)
                except Exception as e:
                    log(f"- `{nombre}` ({fecha}): ERROR al consultar — {e}")
                    problemas += 1
                    continue

                if remoto is None:
                    # No es un fallo: el NRT tiene 1 a 3 días de rezago.
                    log(f"- `{nombre}` ({fecha}): aún no publicado")
                    continue

                disponibles += 1
                antiguedad = (hoy - fecha).days
                log(f"- `{nombre}` ({fecha}): {remoto.tamano:,} bytes · "
                    f"modificado {remoto.modificado} · "
                    f"madurez **{config.madurez(antiguedad)}**")

                try:
                    ruta = gloh2o.descargar(servicio, remoto, Path(tmp) / nombre)
                    da = extraccion.abrir(ruta, cfg["candidatos"])
                    if not filas:
                        log(f"  variable interna: `{da.name}` · "
                            f"malla {dict(da.sizes)}")
                    sub = extraccion.recortar_region(da)
                    df = extraccion.extraer_puntos(sub, municipios)
                    filas.append((fecha, dict(zip(df.municipio, df.valor))))
                except Exception as e:
                    log(f"  ERROR procesando: {e}")
                    problemas += 1
                finally:
                    (Path(tmp) / nombre).unlink(missing_ok=True)

            log()
            if filas:
                encabezado = "| fecha | " + " | ".join(
                    m.nombre.split()[0] for m in municipios) + " |"
                log(encabezado)
                log("|" + "---|" * (len(municipios) + 1))
                for fecha, valores in sorted(filas):
                    celdas = " | ".join(
                        f"{valores[m.id]:.1f}" for m in municipios)
                    log(f"| {fecha} | {celdas} |")
            else:
                log("_Sin datos extraídos para esta variable._")
                problemas += 1
            log()
            log(f"Disponibles: {disponibles} de {len(fechas)} días.")
            log()

    log("---")
    if problemas:
        log(f"Terminó con {problemas} problema(s).")
        return 1
    log("Flujo completo verificado.")
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
