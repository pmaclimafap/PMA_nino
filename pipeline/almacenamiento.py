"""Persistencia en Hugging Face.

Tres semánticas distintas, y el módulo las hace explícitas porque mezclarlas
es la fuente clásica de errores en sistemas de alerta temprana:

  observado   reescribible. El dato de una fecha tiene un valor vigente, no
              una historia. Si hay que recalcular, se regenera el mes.
  pronóstico  INMUTABLE. Lo que se pronosticó el 29 de septiembre para el 5
              de octubre es un hecho histórico. Cuando sale la siguiente
              corrida, la anterior desaparece de la fuente: no se puede
              volver a obtener de ninguna parte.
  derivado    recalculable desde las dos anteriores. Se puede borrar.

Variable de entorno requerida: HF_TOKEN.
"""

from __future__ import annotations

import io
import os
from datetime import date
from pathlib import Path

import pandas as pd

from pipeline import configuracion as config

# Import diferido: permite usar las funciones de fusión (que son las que
# tienen lógica) sin tener huggingface_hub instalado.
_api = None


def _cliente():
    global _api
    if _api is None:
        from huggingface_hub import HfApi

        token = os.environ.get("HF_TOKEN")
        if not token:
            raise RuntimeError(
                "Falta la variable de entorno HF_TOKEN. Debe ser un token de "
                "escritura sobre el dataset."
            )
        _api = HfApi(token=token)
    return _api


def _cfg() -> dict:
    return config.fuentes()["almacenamiento"]


def ruta(clave: str, **campos) -> str:
    """Resuelve una plantilla de ruta declarada en fuentes.yml."""
    plantillas = _cfg()["rutas"]
    if clave not in plantillas:
        raise KeyError(f"Ruta '{clave}' no declarada. Hay: {sorted(plantillas)}")
    return plantillas[clave].format(**campos)


# ---------------------------------------------------------------------------
# Lectura y escritura genéricas
# ---------------------------------------------------------------------------

def existe(ruta_remota: str) -> bool:
    from huggingface_hub import file_exists

    try:
        return file_exists(
            repo_id=_cfg()["repo"],
            filename=ruta_remota,
            repo_type=_cfg()["tipo"],
            token=os.environ.get("HF_TOKEN"),
        )
    except Exception:
        return False


def leer(ruta_remota: str) -> pd.DataFrame:
    """Lee un Parquet del dataset. DataFrame vacío si no existe todavía."""
    from huggingface_hub import hf_hub_download
    from huggingface_hub.errors import EntryNotFoundError, RepositoryNotFoundError

    try:
        local = hf_hub_download(
            repo_id=_cfg()["repo"],
            filename=ruta_remota,
            repo_type=_cfg()["tipo"],
            token=os.environ.get("HF_TOKEN"),
        )
        return pd.read_parquet(local)
    except (EntryNotFoundError, RepositoryNotFoundError, FileNotFoundError):
        # La primera corrida siempre pasa por aquí.
        return pd.DataFrame()


def escribir(df: pd.DataFrame, ruta_remota: str, mensaje: str) -> None:
    """Sube un DataFrame como Parquet, en memoria y sin archivos temporales."""
    buffer = io.BytesIO()
    df.to_parquet(buffer, index=False, compression="snappy")
    buffer.seek(0)

    _cliente().upload_file(
        path_or_fileobj=buffer,
        path_in_repo=ruta_remota,
        repo_id=_cfg()["repo"],
        repo_type=_cfg()["tipo"],
        commit_message=mensaje,
    )


# ---------------------------------------------------------------------------
# Observado: reescribible
# ---------------------------------------------------------------------------

CLAVE_OBSERVADO = ["fecha", "municipio", "variable", "fuente"]


def fusionar_observado(existente: pd.DataFrame, nuevo: pd.DataFrame) -> pd.DataFrame:
    """Combina lo guardado con lo nuevo, dando prioridad a lo nuevo.

    `fuente` es parte de la clave a propósito: permite que MSWEP y CHIRPS
    coexistan para la misma fecha y municipio, y compararlos antes de elegir
    uno como primario. Sin ella, una fuente pisaría a la otra.
    """
    if existente.empty:
        return nuevo.sort_values(CLAVE_OBSERVADO).reset_index(drop=True)
    if nuevo.empty:
        return existente

    combinado = pd.concat([existente, nuevo], ignore_index=True)
    # keep="last" hace que lo nuevo gane: es lo que corresponde cuando
    # GloH2O revisa un archivo del NRT y el dato madura.
    combinado = combinado.drop_duplicates(subset=CLAVE_OBSERVADO, keep="last")
    return combinado.sort_values(CLAVE_OBSERVADO).reset_index(drop=True)


def guardar_observado(df: pd.DataFrame) -> list[str]:
    """Guarda observaciones, agrupadas por mes. Devuelve las rutas escritas."""
    if df.empty:
        return []

    df = df.copy()
    df["fecha"] = pd.to_datetime(df["fecha"])
    escritas = []

    for (anio, mes), grupo in df.groupby([df.fecha.dt.year, df.fecha.dt.month]):
        destino = ruta("monitoreo_puntos", anio=anio, mes=mes)
        fusionado = fusionar_observado(leer(destino), grupo)
        escribir(
            fusionado,
            destino,
            f"observado {anio}-{mes:02d}: {len(grupo)} filas actualizadas",
        )
        escritas.append(destino)

    return escritas


CLAVE_GRILLA = ["fecha", "municipio", "variable", "lat", "lon"]


def guardar_grilla(df: pd.DataFrame) -> list[str]:
    """Guarda la grilla por pixel, agrupada por mes.

    Va en su propia tabla y no junto a los puntos porque tiene otra
    granularidad: una fila por celda, no por municipio. Mezclarlas obligaría
    a filtrar por lat/lon nulos en cada consulta.
    """
    if df.empty:
        return []

    df = df.copy()
    df["fecha"] = pd.to_datetime(df["fecha"])
    escritas = []

    for (anio, mes), grupo in df.groupby([df.fecha.dt.year, df.fecha.dt.month]):
        destino = ruta("monitoreo_grilla", anio=anio, mes=mes)
        existente = leer(destino)
        if existente.empty:
            fusionado = grupo
        else:
            fusionado = pd.concat([existente, grupo], ignore_index=True)
            fusionado = fusionado.drop_duplicates(subset=CLAVE_GRILLA, keep="last")
        escribir(
            fusionado.sort_values(CLAVE_GRILLA),
            destino,
            f"grilla {anio}-{mes:02d}: {len(grupo)} celda(s)",
        )
        escritas.append(destino)

    return escritas


# ---------------------------------------------------------------------------
# Pronóstico: inmutable
# ---------------------------------------------------------------------------

def guardar_pronostico_diario(
    df: pd.DataFrame, emision: date, forzar: bool = False
) -> str | None:
    """Guarda una emisión. NO reescribe una que ya exista.

    Si el archivo está, la corrida se repitió el mismo día y sobrescribirlo
    solo puede destruir datos. Con `forzar=True` se sobrescribe, y es algo
    que debe decidir una persona, nunca el pipeline.
    """
    if df.empty:
        return None

    destino = ruta(
        "pronostico_diario", anio=emision.year, fecha=emision.isoformat()
    )

    if existe(destino) and not forzar:
        print(f"  {destino} ya existe; no se reescribe (usar forzar=True).")
        return None

    escribir(
        df, destino, f"pronóstico emitido {emision.isoformat()}: {len(df)} filas"
    )
    return destino


def guardar_pronostico_estacional(
    df: pd.DataFrame, emision: date, forzar: bool = False
) -> str | None:
    if df.empty:
        return None
    destino = ruta(
        "pronostico_estacional", anio=emision.year, mes=emision.month
    )
    if existe(destino) and not forzar:
        print(f"  {destino} ya existe; no se reescribe.")
        return None
    escribir(df, destino, f"pronóstico estacional {emision:%Y-%m}: {len(df)} filas")
    return destino


def leer_pronosticos(anio: int | None = None) -> pd.DataFrame:
    """Todas las emisiones diarias guardadas, para verificación."""
    from huggingface_hub import list_repo_files

    archivos = list_repo_files(
        repo_id=_cfg()["repo"],
        repo_type=_cfg()["tipo"],
        token=os.environ.get("HF_TOKEN"),
    )
    prefijo = f"pronostico/diario/{anio}/" if anio else "pronostico/diario/"
    objetivo = [a for a in archivos if a.startswith(prefijo) and a.endswith(".parquet")]
    if not objetivo:
        return pd.DataFrame()
    return pd.concat((leer(a) for a in objetivo), ignore_index=True)


# ---------------------------------------------------------------------------
# Estado del pipeline
# ---------------------------------------------------------------------------

CLAVE_INGESTA = ["carpeta", "archivo"]


def leer_ingesta() -> pd.DataFrame:
    """Registro de qué archivos de origen ya se procesaron.

    Es lo que permite descargar dos o tres archivos al día en lugar de
    sesenta: se compara la fecha de modificación en origen antes de bajar.
    """
    df = leer(ruta("ingesta"))
    if df.empty:
        return pd.DataFrame(
            columns=CLAVE_INGESTA + ["modificado_origen", "procesado", "estado"]
        )
    return df


def guardar_ingesta(existente: pd.DataFrame, nuevo: pd.DataFrame) -> None:
    if nuevo.empty:
        return
    combinado = pd.concat([existente, nuevo], ignore_index=True)
    combinado = combinado.drop_duplicates(subset=CLAVE_INGESTA, keep="last")
    escribir(
        combinado.sort_values(CLAVE_INGESTA),
        ruta("ingesta"),
        f"ingesta: {len(nuevo)} registro(s)",
    )


def registrar_corrida(fila: dict) -> None:
    """Añade una línea a la bitácora.

    Se escribe SIEMPRE, con o sin datos nuevos. Dos razones: trazabilidad
    para el donante, y que en repositorios públicos GitHub desactiva los
    workflows programados tras 60 días sin actividad. Un pipeline que solo
    escribe cuando hay novedades puede apagarse en silencio.
    """
    bitacora = leer(ruta("bitacora"))
    nueva = pd.DataFrame([fila])
    combinada = (
        nueva if bitacora.empty else pd.concat([bitacora, nueva], ignore_index=True)
    )
    escribir(combinada, ruta("bitacora"), f"bitácora: {fila.get('inicio', '')}")
