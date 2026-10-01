"""Carga de la configuración del pipeline.

Toda la configuración vive en pipeline/config/*.yml. El código no debe
contener IDs de carpetas, umbrales ni coordenadas: si un valor cambia, tiene
que verse en el historial de un archivo de datos, no enterrado en un script.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parent
DIR_CONFIG = RAIZ / "config"


def _cargar(nombre: str) -> dict:
    ruta = DIR_CONFIG / nombre
    if not ruta.exists():
        raise FileNotFoundError(f"No se encontró {ruta}")
    with ruta.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@lru_cache(maxsize=1)
def fuentes() -> dict:
    return _cargar("fuentes.yml")


@lru_cache(maxsize=1)
def umbrales() -> dict:
    return _cargar("umbrales.yml")


@lru_cache(maxsize=1)
def pronostico() -> dict:
    """Configuración del módulo de pronóstico (modelos, pesos, confianza)."""
    return _cargar("pronostico.yml")


@lru_cache(maxsize=1)
def _municipios_raw() -> dict:
    return _cargar("municipios.yml")


@dataclass(frozen=True)
class Municipio:
    id: str
    nombre: str
    departamento: str
    lat: float
    lon: float
    sistema_principal: str


@lru_cache(maxsize=1)
def municipios() -> tuple[Municipio, ...]:
    return tuple(
        Municipio(
            id=m["id"],
            nombre=m["nombre"],
            departamento=m["departamento"],
            lat=m["punto"]["lat"],
            lon=m["punto"]["lon"],
            sistema_principal=m["sistema_principal"],
        )
        for m in _municipios_raw()["municipios"]
    )


def ruta_geojson() -> Path:
    return DIR_CONFIG / "municipios.geojson"


def carpeta_id(clave: str) -> str:
    """ID de Drive de una carpeta declarada en fuentes.yml."""
    carpetas = fuentes()["carpetas"]
    if clave not in carpetas:
        raise KeyError(
            f"'{clave}' no está en fuentes.yml. Disponibles: {sorted(carpetas)}"
        )
    return carpetas[clave]["id"]


def variables_activas() -> dict[str, dict]:
    """Variables en uso, con su carpeta de origen ya resuelta a un ID."""
    salida = {}
    for nombre, cfg in fuentes()["variables"].items():
        salida[nombre] = {
            "candidatos": cfg["candidatos"],
            "unidad": cfg["unidad"],
            "clave_carpeta": cfg["fuente"],
            "carpeta_id": carpeta_id(cfg["fuente"]),
        }
    return salida


def dias_reverificacion() -> int:
    """Ventana en la que el NRT todavía se revisa en origen."""
    return int(fuentes()["nrt"]["dias_reverificacion"])


def madurez(dias_desde_la_fecha: int) -> str:
    """Estado de maduración de un dato según su antigüedad.

    GloH2O actualiza progresivamente los archivos NRT: primero fuentes
    rápidas, luego versiones más confiables y, hacia el quinto día, ERA5
    reemplaza a GDAS.
    """
    tabla = fuentes()["nrt"]["madurez"]
    for estado, (desde, hasta) in tabla.items():
        if dias_desde_la_fecha >= desde and (hasta is None or dias_desde_la_fecha <= hasta):
            return estado
    return "consolidado"

