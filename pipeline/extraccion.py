"""Apertura de NetCDF, recorte regional y extracción de series puntuales.

El archivo global de MSWEP es una matriz de 1800 x 3600 (~6,5 millones de
celdas, ~25 MB en memoria). De ahí se necesitan 7 puntos. El recorte es la
operación que hace innecesario todo lo demás: reduce unas 900 veces.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

# Recuadro que cubre los cuatro departamentos con margen.
# Solo para reducir memoria antes del recorte fino por polígono.
RECUADRO = {"lat_min": 1.0, "lat_max": 13.0, "lon_min": -77.0, "lon_max": -70.0}

_ALIAS_LON = {"lon", "longitude", "x"}
_ALIAS_LAT = {"lat", "latitude", "y"}


def _nombre_variable(ds: xr.Dataset, candidatos: list[str]) -> str:
    """Cada producto nombra distinto la misma variable. Se prueban en orden."""
    for c in candidatos:
        if c in ds.data_vars:
            return c
    disponibles = list(ds.data_vars)
    if not disponibles:
        raise ValueError("El NetCDF no contiene variables de datos.")
    return disponibles[0]


def abrir(ruta: Path, candidatos: list[str]) -> xr.DataArray:
    """Abre un .nc y devuelve la variable con dimensiones normalizadas a lat/lon.

    Normaliza tres cosas que varían entre productos: el nombre interno de la
    variable, el nombre de las dimensiones espaciales y el rango de longitud.
    """
    ds = xr.open_dataset(ruta, engine="h5netcdf")
    da = ds[_nombre_variable(ds, candidatos)]

    renombrar = {}
    for dim in da.dims:
        d = str(dim).lower()
        if d in _ALIAS_LON and dim != "lon":
            renombrar[dim] = "lon"
        elif d in _ALIAS_LAT and dim != "lat":
            renombrar[dim] = "lat"
    if renombrar:
        da = da.rename(renombrar)

    if "lat" not in da.dims or "lon" not in da.dims:
        raise ValueError(f"No se hallaron dimensiones espaciales en {da.dims}.")

    # De 0–360 a −180/180 cuando haga falta
    if float(da.lon.max()) > 180:
        da = da.assign_coords(lon=((da.lon + 180) % 360) - 180).sortby("lon")

    # El valor de relleno (−9999) contaminaría los acumulados si el
    # decodificador no lo convirtió a NaN.
    relleno = ds[da.name].encoding.get("_FillValue")
    if relleno is not None:
        da = da.where(da != relleno)

    return da


def recortar_region(da: xr.DataArray) -> xr.DataArray:
    """Recorte al recuadro de los cuatro departamentos.

    La latitud puede venir descendente (de norte a sur), así que el orden del
    slice depende de cómo esté el eje.
    """
    lat_desc = float(da.lat[0]) > float(da.lat[-1])
    lat_slice = (
        slice(RECUADRO["lat_max"], RECUADRO["lat_min"])
        if lat_desc
        else slice(RECUADRO["lat_min"], RECUADRO["lat_max"])
    )
    return da.sel(lat=lat_slice, lon=slice(RECUADRO["lon_min"], RECUADRO["lon_max"]))


def extraer_puntos(da: xr.DataArray, municipios) -> pd.DataFrame:
    """Valor en los 7 municipios, en UNA sola operación vectorizada.

    Seleccionar punto por punto en un bucle multiplica las lecturas del
    archivo sin necesidad; xarray admite selección vectorizada.
    """
    ids = [m.id for m in municipios]
    lats = xr.DataArray([m.lat for m in municipios], dims="municipio",
                        coords={"municipio": ids})
    lons = xr.DataArray([m.lon for m in municipios], dims="municipio",
                        coords={"municipio": ids})

    sel = da.sel(lat=lats, lon=lons, method="nearest")

    if "time" in sel.dims:
        sel = sel.isel(time=0)

    return pd.DataFrame(
        {
            "municipio": ids,
            "valor": [float(v) for v in np.asarray(sel.values).ravel()],
            "lat_celda": [float(v) for v in np.asarray(sel.lat.values).ravel()],
            "lon_celda": [float(v) for v in np.asarray(sel.lon.values).ravel()],
        }
    )


def fecha_del_archivo(da: xr.DataArray):
    """Fecha del único paso temporal del archivo, si la trae."""
    if "time" not in da.coords:
        return None
    return pd.Timestamp(np.asarray(da["time"].values).ravel()[0]).date()


def extraer_grilla(da: xr.DataArray, geometria, crs="EPSG:4326") -> list[dict]:
    """Una celda por pixel dentro del polígono del municipio.

    Alimenta el mapa del tablero. Requiere rioxarray, que se importa aquí
    para que el resto del módulo no dependa de él.
    """
    import rioxarray  # noqa: F401  habilita el accesor .rio

    espacial = da.rio.set_spatial_dims(x_dim="lon", y_dim="lat", inplace=False)
    if not espacial.rio.crs:
        espacial = espacial.rio.write_crs(crs)

    recorte = espacial.rio.clip([geometria], crs, all_touched=True, drop=True)
    df = (
        recorte.to_dataframe(name="valor")
        .reset_index()
        .dropna(subset=["valor"])
    )

    return [
        {
            "lat": round(float(r.lat), 4),
            "lon": round(float(r.lon), 4),
            "valor": round(float(r.valor), 2),
        }
        for r in df.itertuples()
    ]
