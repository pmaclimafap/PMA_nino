"""migrar_almacenamiento.py — Mueve los datos a la jerarquía por módulo.

Operación de UNA SOLA VEZ. Copia los archivos de las rutas planas originales
a la estructura nueva, sin volver a descargar nada de GloH2O.

    observado/AAAA-MM.parquet          -> monitoreo/satelital/puntos/
    observado_grilla/AAAA-MM.parquet   -> monitoreo/satelital/grilla/
    estado/oni.parquet                 -> indices/oni.parquet

Se copia, no se mueve: las rutas viejas quedan intactas para que se puedan
revisar y borrar a mano desde la interfaz de Hugging Face. Hacerlo así evita
que un fallo a mitad de camino deje el dataset sin datos.

No vuelve a descargar de GloH2O a propósito: reprocesar los 300 archivos de
la ventana agota la cuota de descarga de la carpeta compartida.

Uso:
    HF_TOKEN=... python scripts/migrar_almacenamiento.py [--aplicar]

Sin --aplicar solo muestra qué haría.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import almacenamiento as alm  # noqa: E402

# Prefijos viejos -> plantilla nueva. Los archivos mensuales se enumeran
# listando el repositorio, no adivinando fechas.
MAPEO_MENSUAL = [
    ("observado/", "monitoreo_puntos"),
    ("observado_grilla/", "monitoreo_grilla"),
]
MAPEO_FIJO = [
    ("estado/oni.parquet", "oni"),
]


def listar_remoto() -> list[str]:
    from huggingface_hub import list_repo_files
    import os

    cfg = alm._cfg()
    return list_repo_files(
        repo_id=cfg["repo"],
        repo_type=cfg["tipo"],
        token=os.environ.get("HF_TOKEN"),
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--aplicar", action="store_true",
                    help="Ejecuta la copia. Sin esto solo simula.")
    args = ap.parse_args()

    archivos = listar_remoto()
    print(f"{len(archivos)} archivo(s) en el dataset\n")

    tareas: list[tuple[str, str]] = []

    for prefijo, clave in MAPEO_MENSUAL:
        for a in sorted(x for x in archivos if x.startswith(prefijo)):
            nombre = Path(a).stem                      # "2026-09"
            try:
                anio, mes = (int(x) for x in nombre.split("-"))
            except ValueError:
                print(f"  omitido (nombre inesperado): {a}")
                continue
            tareas.append((a, alm.ruta(clave, anio=anio, mes=mes)))

    for viejo, clave in MAPEO_FIJO:
        if viejo in archivos:
            tareas.append((viejo, alm.ruta(clave)))

    if not tareas:
        print("Nada que migrar. ¿Ya se hizo?")
        return 0

    ancho = max(len(v) for v, _ in tareas)
    for viejo, nuevo in tareas:
        ya = " (el destino ya existe, se sobrescribe)" if nuevo in archivos else ""
        print(f"  {viejo:<{ancho}}  ->  {nuevo}{ya}")

    if not args.aplicar:
        print(f"\n{len(tareas)} copia(s) pendientes. Repetir con --aplicar.")
        return 0

    print()
    for viejo, nuevo in tareas:
        df = alm.leer(viejo)
        if df.empty:
            print(f"  VACÍO, se omite: {viejo}")
            continue
        alm.escribir(df, nuevo, f"migración: {viejo} -> {nuevo}")
        print(f"  copiado ({len(df)} filas): {nuevo}")

    print("\nMigración terminada.")
    print("Las rutas viejas siguen ahí. Revisarlas y borrarlas a mano desde")
    print("la pestaña Files del dataset:")
    for prefijo, _ in MAPEO_MENSUAL:
        print(f"  - {prefijo}")
    for viejo, _ in MAPEO_FIJO:
        print(f"  - {viejo}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
