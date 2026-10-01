"""Acceso a MSWEP y MSWX en el Drive de GloH2O.

Las carpetas están compartidas como "cualquiera con el enlace", así que una
cuenta de servicio puede leerlas sin permiso explícito. Se accede SIEMPRE por
ID de carpeta: no se navega por nombres ni se depende de "Compartido conmigo".

Principio: buscar cada archivo por nombre dentro de su carpeta (una llamada
por candidato) en lugar de listar carpetas con decenas de miles de archivos.
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

# Un .nc diario global pesa MBs. Por debajo de esto, Drive no entregó el
# contenido real (atajo sin resolver, archivo vacío o una página de error).
TAMANO_MINIMO_BYTES = 10_000

# GloH2O publica los archivos del NRT hacia las 01:35 UTC. Pedir uno
# mientras se esta escribiendo devuelve contenido truncado, y la cuota de
# descarga de la carpeta compartida tambien produce fallos transitorios.
# Ambos casos se resuelven reintentando con espera creciente.
REINTENTOS = 3
ESPERA_BASE_S = 4


def es_cuota_agotada(e: BaseException) -> bool:
    """¿Es la cuota de descarga del archivo compartido?

    Google limita las descargas POR ARCHIVO en carpetas compartidas, y la de
    GloH2O la usan miles de personas. La cuota se restablece en unas 24
    horas, así que reintentar dentro de la misma corrida no sirve de nada:
    solo gasta tiempo. Se distingue para no reintentar en vano y para que el
    pipeline pueda esperar al día siguiente.
    """
    texto = str(e)
    return "downloadQuotaExceeded" in texto or "download quota" in texto.lower()


class CuotaAgotada(IOError):
    """La cuota de descarga del archivo se agotó. Reintentable al día siguiente."""


@dataclass(frozen=True)
class ArchivoRemoto:
    """Metadata de un archivo en Drive, sin descargarlo."""

    id: str
    nombre: str
    tamano: int
    modificado: str  # ISO 8601, como lo reporta Drive

    def cambio_respecto_a(self, modificado_previo: str | None) -> bool:
        """¿Hay que reprocesarlo?

        Comparar la fecha de modificación, no la existencia: dentro de la
        ventana de 10 días GloH2O reescribe los archivos NRT en origen.
        """
        return modificado_previo is None or self.modificado > modificado_previo


def autenticar():
    """Cliente de Drive a partir del secreto GCP_SERVICE_ACCOUNT_KEY."""
    bruto = os.environ.get("GCP_SERVICE_ACCOUNT_KEY")
    if not bruto:
        raise RuntimeError(
            "Falta la variable de entorno GCP_SERVICE_ACCOUNT_KEY. "
            "Debe contener el JSON completo de la cuenta de servicio."
        )
    try:
        info = json.loads(bruto)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"GCP_SERVICE_ACCOUNT_KEY no es JSON válido ({e}). "
            "Debe incluir las llaves de apertura y cierre."
        ) from e

    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def nombre_archivo(fecha: date) -> str:
    """Convención de GloH2O: AAAADDD.nc (año + día juliano)."""
    return f"{fecha.year}{fecha.timetuple().tm_yday:03d}.nc"


def buscar(servicio, carpeta_id: str, nombre: str) -> ArchivoRemoto | None:
    """Busca un archivo por nombre dentro de una carpeta. None si no existe.

    Una sola llamada, sin listar la carpeta completa.
    """
    consulta = (
        f"name = '{nombre}' and '{carpeta_id}' in parents and trashed = false"
    )
    try:
        respuesta = (
            servicio.files()
            .list(
                q=consulta,
                spaces="drive",
                fields="files(id, name, size, modifiedTime)",
                pageSize=1,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )
    except HttpError as e:
        raise RuntimeError(
            f"Error consultando '{nombre}' en la carpeta {carpeta_id}: {e}"
        ) from e

    archivos = respuesta.get("files", [])
    if not archivos:
        return None
    a = archivos[0]
    return ArchivoRemoto(
        id=a["id"],
        nombre=a["name"],
        tamano=int(a.get("size") or 0),
        modificado=a.get("modifiedTime", ""),
    )


def descargar(servicio, archivo: ArchivoRemoto, destino: Path) -> Path:
    """Descarga a disco de forma atómica, con reintentos.

    Se escribe a un temporal y se renombra al final. Si el proceso muere a
    mitad, no queda un archivo truncado que mañana se dé por bueno.
    """
    ultimo_error: Exception | None = None
    for intento in range(1, REINTENTOS + 1):
        try:
            return _descargar_una_vez(servicio, archivo, destino)
        except Exception as e:
            ultimo_error = e
            # La cuota no se libera en segundos: reintentar es inútil.
            if es_cuota_agotada(e):
                raise CuotaAgotada(
                    f"'{archivo.nombre}': cuota de descarga del archivo agotada "
                    "en la carpeta compartida de GloH2O. Se restablece en unas "
                    "24 horas; el pipeline lo reintentará mañana."
                ) from e
            if intento < REINTENTOS:
                time.sleep(ESPERA_BASE_S * intento)
    raise IOError(
        f"'{archivo.nombre}' falló tras {REINTENTOS} intentos: {ultimo_error}"
    ) from ultimo_error


def _descargar_una_vez(servicio, archivo: ArchivoRemoto, destino: Path) -> Path:
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(
        dir=destino.parent, suffix=".parcial", delete=False
    ) as tmp:
        ruta_tmp = Path(tmp.name)

    try:
        peticion = servicio.files().get_media(fileId=archivo.id)
        with io.FileIO(ruta_tmp, "wb") as fh:
            descargador = MediaIoBaseDownload(fh, peticion, chunksize=8 * 1024 * 1024)
            terminado = False
            while not terminado:
                _, terminado = descargador.next_chunk()

        tamano = ruta_tmp.stat().st_size
        if tamano < TAMANO_MINIMO_BYTES:
            raise IOError(
                f"'{archivo.nombre}' pesa solo {tamano} bytes. Drive no entregó "
                "el contenido real (¿atajo sin resolver, archivo vacío o cuota "
                "de descarga excedida?)."
            )
        # Drive informa el tamaño en la metadata: si lo descargado no
        # coincide, llegó truncado y abrirlo daría un error confuso de
        # NetCDF en vez de señalar la causa.
        if archivo.tamano and tamano != archivo.tamano:
            raise IOError(
                f"'{archivo.nombre}' llegó truncado: {tamano} bytes de "
                f"{archivo.tamano} esperados."
            )

        ruta_tmp.replace(destino)
        return destino

    except Exception:
        ruta_tmp.unlink(missing_ok=True)
        raise


def obtener(servicio, carpeta_id: str, fecha: date, destino_dir: Path
            ) -> tuple[Path, ArchivoRemoto] | None:
    """Busca y descarga el archivo de una fecha. None si no está publicado.

    Que falte no es un error: el NRT tiene 1 a 3 días de rezago.
    """
    nombre = nombre_archivo(fecha)
    archivo = buscar(servicio, carpeta_id, nombre)
    if archivo is None:
        return None
    ruta = descargar(servicio, archivo, Path(destino_dir) / nombre)
    return ruta, archivo
