"""
explorar_drive.py — Diagnóstico de acceso a las carpetas de GloH2O.

No descarga datos. Verifica que la cuenta de servicio pueda leer las carpetas
compartidas por enlace y mapea su estructura, devolviendo el ID de cada
subcarpeta y los archivos más recientes de las carpetas terminales.

PODA: MSWX contiene ramas de pronóstico (Mid, Long) organizadas como una
carpeta por fecha de inicialización y, dentro, una por cada miembro del
ensamble: decenas de miles de carpetas. Recorrerlas completas es inviable.
Por eso:

  - Si un nivel tiene más de MAX_HERMANOS subcarpetas, solo se exploran la
    primera y la última, y se reporta el total.
  - Un tope global de llamadas evita cualquier desborde.

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

PROFUNDIDAD_MAX = 6      # MSWX/Mid/Temp/<fecha>/<miembro>/Daily
MAX_HERMANOS = 4         # más que esto, se muestrea en vez de recorrer
ARCHIVOS_A_MOSTRAR = 8
MAX_LLAMADAS = 400       # freno de seguridad

rutas_terminales: list[tuple[str, str]] = []
lineas: list[str] = []
llamadas = 0


def log(texto: str = "") -> None:
    print(texto, flush=True)
    lineas.append(texto)


def autenticar():
    bruto = os.environ.get("GCP_SERVICE_ACCOUNT_KEY")
    if not bruto:
        sys.exit("ERROR: falta la variable de entorno GCP_SERVICE_ACCOUNT_KEY.")
    try:
        info = json.loads(bruto)
    except json.JSONDecodeError as e:
        sys.exit(
            f"ERROR: GCP_SERVICE_ACCOUNT_KEY no es JSON valido ({e}). "
            "Debe contener el archivo completo, incluidas las llaves."
        )
    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    return (
        build("drive", "v3", credentials=creds, cache_discovery=False),
        info.get("client_email", "desconocida"),
    )


def presupuesto_agotado() -> bool:
    return llamadas >= MAX_LLAMADAS


def listar(servicio, carpeta_id: str, solo_carpetas: bool,
           orden: str | None = None, limite: int | None = None) -> list[dict]:
    global llamadas
    consulta = f"'{carpeta_id}' in parents and trashed = false"
    if solo_carpetas:
        consulta += f" and (mimeType = '{MIME_CARPETA}' or mimeType = '{MIME_ATAJO}')"

    items, token = [], None
    while True:
        llamadas += 1
        resultado = servicio.files().list(
            q=consulta,
            fields="nextPageToken, files(id, name, mimeType, size, modifiedTime)",
            pageToken=token,
            pageSize=min(limite or 1000, 1000),
            orderBy=orden,
            supportsAllDrives=True,
            includeItemsFromAllDrives=True,
        ).execute()
        items.extend(resultado.get("files", []))
        token = resultado.get("nextPageToken")
        if not token or limite or presupuesto_agotado():
            break
    return items[:limite] if limite else items


def resolver_atajo(servicio, item: dict) -> tuple[str, str]:
    global llamadas
    if item.get("mimeType") != MIME_ATAJO:
        return item["id"], item["mimeType"]
    llamadas += 1
    detalle = servicio.files().get(
        fileId=item["id"], fields="shortcutDetails", supportsAllDrives=True
    ).execute()
    sd = detalle.get("shortcutDetails", {})
    return sd.get("targetId", item["id"]), sd.get("targetMimeType", "")


def muestra_archivos(servicio, carpeta_id: str, sangria: str) -> None:
    """Primer y ultimos archivos, sin paginar decenas de miles.

    Los nombres siguen la convencion AAAADDD.nc, asi que el orden alfabetico
    coincide con el cronologico.
    """
    try:
        recientes = listar(servicio, carpeta_id, False, "name desc", ARCHIVOS_A_MOSTRAR)
        antiguos = listar(servicio, carpeta_id, False, "name", 1)
    except HttpError as e:
        log(f"{sangria}  no se pudieron listar archivos: {e}")
        return

    if not recientes:
        log(f"{sangria}  (sin archivos)")
        return

    if antiguos:
        log(f"{sangria}  primer archivo: {antiguos[0]['name']}")
    log(f"{sangria}  {'archivo':<16}{'bytes':>12}  modificado")
    for f in recientes:
        tam = int(f.get("size") or 0)
        log(f"{sangria}  {f['name']:<16}{tam:>12,}  {f.get('modifiedTime', '?')}")


def recorrer(servicio, nombre: str, carpeta_id: str, ruta: str, nivel: int) -> None:
    sangria = "  " * nivel
    log(f"{sangria}{nombre}/  ->  {carpeta_id}")

    if presupuesto_agotado():
        log(f"{sangria}  [tope de llamadas alcanzado]")
        return
    if nivel >= PROFUNDIDAD_MAX:
        log(f"{sangria}  [profundidad maxima]")
        return

    try:
        subs = listar(servicio, carpeta_id, solo_carpetas=True, orden="name")
    except HttpError as e:
        log(f"{sangria}  ERROR al listar: {e}")
        return

    # Sin subcarpetas: es terminal, aqui viven los .nc
    if not subs:
        rutas_terminales.append((ruta, carpeta_id))
        muestra_archivos(servicio, carpeta_id, sangria)
        return

    # Demasiados hermanos (fechas de inicializacion, miembros del ensamble):
    # se muestrea en vez de recorrer todo.
    if len(subs) > MAX_HERMANOS:
        log(f"{sangria}  [{len(subs)} subcarpetas - se exploran la primera y la ultima]")
        log(f"{sangria}  primera: {subs[0]['name']}   ultima: {subs[-1]['name']}")
        a_explorar = [subs[0], subs[-1]]
    else:
        a_explorar = subs

    for sub in a_explorar:
        if presupuesto_agotado():
            log(f"{sangria}  [tope de llamadas alcanzado]")
            return
        real_id, real_mime = resolver_atajo(servicio, sub)
        if real_mime and real_mime != MIME_CARPETA:
            continue
        recorrer(servicio, sub["name"], real_id, f"{ruta}/{sub['name']}", nivel + 1)


def main() -> None:
    servicio, correo = autenticar()
    log("# Exploracion de las carpetas de GloH2O")
    log()
    log(f"Cuenta de servicio: `{correo}`")
    log()
    log("```")

    fallos = 0
    for etiqueta, raiz_id in RAICES.items():
        log(f"=== {etiqueta} ===")
        try:
            meta = servicio.files().get(
                fileId=raiz_id, fields="id, name, mimeType", supportsAllDrives=True
            ).execute()
        except HttpError as e:
            fallos += 1
            log(f"  NO SE PUDO ACCEDER a {raiz_id}")
            log(f"  {e}")
            log("  Si el codigo es 404, la carpeta no es publica por enlace y hay")
            log("  que pedirle a GloH2O que la comparta con la cuenta de servicio.")
            log()
            continue
        recorrer(servicio, meta["name"], raiz_id, meta["name"], 0)
        log()

    log("```")

    if rutas_terminales:
        log("## Carpetas terminales")
        log()
        log("```yaml")
        log("carpetas:")
        for ruta, cid in sorted(rutas_terminales):
            clave = ruta.replace("/", "_").lower()
            log(f"  {clave}:")
            log(f"    ruta: {ruta}")
            log(f"    id: {cid}")
        log("```")

    log()
    log(f"Llamadas a la API: {llamadas} de {MAX_LLAMADAS}.")
    if presupuesto_agotado():
        log("Se alcanzo el tope; la exploracion quedo incompleta.")

    if fallos:
        sys.exit(f"Termino con {fallos} carpeta(s) raiz inaccesible(s).")
    log(f"Acceso verificado. {len(rutas_terminales)} carpeta(s) terminal(es).")


if __name__ == "__main__":
    try:
        main()
    finally:
        resumen = os.environ.get("GITHUB_STEP_SUMMARY")
        if resumen:
            with open(resumen, "a", encoding="utf-8") as fh:
                fh.write("\n".join(lineas) + "\n")
