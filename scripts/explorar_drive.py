"""
explorar_drive.py — Diagnóstico de acceso a las carpetas de GloH2O.

No descarga datos. Verifica que la cuenta de servicio pueda leer las carpetas
compartidas por enlace y mapea su estructura, devolviendo el ID de cada
subcarpeta y los archivos más recientes de las carpetas Daily.

La salida es el insumo para configurar pipeline/config/fuentes.yml.

Uso:
    GCP_SERVICE_ACCOUNT_KEY='{...}' python scripts/explorar_drive.py
"""

import json
import os
import sys

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
MIME_CARPETA = "application/vnd.google-apps.folder"
MIME_ATAJO = "application/vnd.google-apps.shortcut"

# Carpetas raíz compartidas por GloH2O (públicas por enlace)
RAICES = {
    "MSWEP": "1Kok05OPVESTpyyan7NafR-2WwuSJ4TO9",
    "MSWX": "1R1KRmldXmLj_09kzE2wKSrbXUzU3pQN9",
}

PROFUNDIDAD_MAX = 4
ARCHIVOS_A_MOSTRAR = 8

# Acumula las rutas terminales para proponer la configuración al final
rutas_daily: list[tuple[str, str]] = []
lineas_resumen: list[str] = []


def log(texto: str = "") -> None:
    """Escribe en consola y en el resumen del job de GitHub Actions."""
    print(texto, flush=True)
    lineas_resumen.append(texto)


def autenticar():
    bruto = os.environ.get("GCP_SERVICE_ACCOUNT_KEY")
    if not bruto:
        sys.exit("ERROR: falta la variable de entorno GCP_SERVICE_ACCOUNT_KEY.")
    try:
        info = json.loads(bruto)
    except json.JSONDecodeError as e:
        sys.exit(
            f"ERROR: GCP_SERVICE_ACCOUNT_KEY no es JSON válido ({e}). "
            "Debe contener el archivo completo, incluidas las llaves { }."
        )
    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    servicio = build("drive", "v3", credentials=creds, cache_discovery=False)
    return servicio, info.get("client_email", "desconocida")


def resolver_atajo(servicio, item: dict) -> tuple[str, str]:
    """Devuelve (id_real, mime_real). Los atajos apuntan a otro objeto."""
    if item.get("mimeType") != MIME_ATAJO:
        return item["id"], item["mimeType"]
    detalle = (
        servicio.files()
        .get(fileId=item["id"], fields="shortcutDetails", supportsAllDrives=True)
        .execute()
    )
    sd = detalle.get("shortcutDetails", {})
    return sd.get("targetId", item["id"]), sd.get("targetMimeType", "")


def listar(servicio, carpeta_id: str, solo_carpetas: bool,
           orden: str | None = None, limite: int | None = None) -> list[dict]:
    consulta = f"'{carpeta_id}' in parents and trashed = false"
    if solo_carpetas:
        consulta += (
            f" and (mimeType = '{MIME_CARPETA}' or mimeType = '{MIME_ATAJO}')"
        )

    items, token = [], None
    while True:
        peticion = servicio.files().list(
            q=consulta,
            fields="nextPageToken, files(id, name, mimeType, size, modifiedTime)",
            pageToken=token,
            pageSize=min(limite or 1000, 1000),
            orderBy=orden,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        )
        resultado = peticion.execute()
        items.extend(resultado.get("files", []))
        token = resultado.get("nextPageToken")
        # Con límite basta la primera página; sin él, se pagina todo
        if not token or limite:
            break
    return items[:limite] if limite else items


def mostrar_muestra_archivos(servicio, ruta: str, carpeta_id: str) -> None:
    """Los N más recientes y el más antiguo, sin paginar miles de archivos.

    Los nombres siguen la convención AAAADDD.nc (año + día juliano), así que
    ordenar por nombre equivale a ordenar cronológicamente.
    """
    try:
        recientes = listar(servicio, carpeta_id, solo_carpetas=False,
                           orden="name desc", limite=ARCHIVOS_A_MOSTRAR)
        antiguos = listar(servicio, carpeta_id, solo_carpetas=False,
                          orden="name", limite=1)
    except HttpError as e:
        log(f"      no se pudieron listar archivos: {e}")
        return

    if not recientes:
        log("      (carpeta sin archivos)")
        return

    if antiguos:
        log(f"      primer archivo: {antiguos[0]['name']}")
    log(f"      {'archivo':<16}{'bytes':>12}  modificado")
    for f in recientes:
        tam = int(f.get("size") or 0)
        log(f"      {f['name']:<16}{tam:>12,}  {f.get('modifiedTime', '?')}")


def recorrer(servicio, nombre: str, carpeta_id: str, ruta: str, nivel: int) -> None:
    sangria = "  " * nivel
    log(f"{sangria}{nombre}/  ->  {carpeta_id}")

    if nivel >= PROFUNDIDAD_MAX:
        log(f"{sangria}  (profundidad máxima alcanzada)")
        return

    try:
        subcarpetas = listar(servicio, carpeta_id, solo_carpetas=True, orden="name")
    except HttpError as e:
        log(f"{sangria}  ERROR al listar: {e}")
        return

    # Una carpeta sin subcarpetas es terminal: contiene los .nc
    if not subcarpetas:
        rutas_daily.append((ruta, carpeta_id))
        mostrar_muestra_archivos(servicio, ruta, carpeta_id)
        return

    for sub in subcarpetas:
        real_id, real_mime = resolver_atajo(servicio, sub)
        if real_mime and real_mime != MIME_CARPETA:
            continue
        recorrer(servicio, sub["name"], real_id,
                 f"{ruta}/{sub['name']}", nivel + 1)


def main() -> None:
    servicio, correo = autenticar()
    log("# Exploración de las carpetas de GloH2O")
    log()
    log(f"Cuenta de servicio: `{correo}`")
    log()
    log("```")

    fallos = 0
    for etiqueta, raiz_id in RAICES.items():
        log(f"=== {etiqueta} ===")
        try:
            meta = (
                servicio.files()
                .get(fileId=raiz_id, fields="id, name, mimeType",
                     supportsAllDrives=True)
                .execute()
            )
        except HttpError as e:
            fallos += 1
            log(f"  NO SE PUDO ACCEDER a {raiz_id}")
            log(f"  {e}")
            log("  Si el código es 404, la carpeta no es pública por enlace y hay")
            log("  que pedirle a GloH2O que la comparta con la cuenta de servicio.")
            log()
            continue

        recorrer(servicio, meta["name"], raiz_id, meta["name"], 0)
        log()

    log("```")

    if rutas_daily:
        log("## Rutas terminales encontradas")
        log()
        log("Copiar a `pipeline/config/fuentes.yml`:")
        log()
        log("```yaml")
        log("carpetas:")
        for ruta, cid in sorted(rutas_daily):
            clave = ruta.replace("/", "_").lower()
            log(f"  {clave}:")
            log(f"    ruta: {ruta}")
            log(f"    id: {cid}")
        log("```")

    if fallos:
        sys.exit(f"Terminó con {fallos} carpeta(s) raíz inaccesible(s).")

    log()
    log(f"Acceso verificado. {len(rutas_daily)} carpeta(s) terminal(es) mapeada(s).")


if __name__ == "__main__":
    try:
        main()
    finally:
        resumen = os.environ.get("GITHUB_STEP_SUMMARY")
        if resumen:
            with open(resumen, "a", encoding="utf-8") as fh:
                fh.write("\n".join(lineas_resumen) + "\n")
