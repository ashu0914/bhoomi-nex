"""Per-record GIS geometry import: GeoJSON files and shapefile ZIPs.

Everything a `Record` stores is normalized to a single GeoJSON geometry
(Polygon/MultiPolygon, or whatever the source contains) in WGS84
(EPSG:4326, i.e. plain lat/lng), plus a derived centroid — that's what
`main.py` writes to `Record.geometry_geojson` / `.latitude` / `.longitude`.

Two input shapes are supported:

- **.geojson / .json** — a bare geometry, a Feature, or a FeatureCollection
  (first feature's geometry is used). Assumed to already be WGS84, per the
  GeoJSON spec (RFC 7946 §4).
- **.zip** — a shapefile bundle (.shp required; .dbf/.shx used if present;
  .prj used to detect the source coordinate system). If a .prj is present
  and isn't already WGS84, coordinates are reprojected with pyproj.
  Multiple shapes in one file are unioned into a single geometry (a parcel
  is expected to be one shape, but this keeps oddly-split exports usable).

Raises `GeometryImportError` with a message safe to show the user directly
for anything that goes wrong (bad zip, missing .shp, unparseable .prj,
out-of-range centroid, etc.) — callers should catch this and turn it into
a 400 response rather than a 500.
"""
from __future__ import annotations

import io
import json
import zipfile
from dataclasses import dataclass

import pyproj
import shapefile as pyshp
from shapely.geometry import mapping, shape
from shapely.ops import transform as shp_transform
from shapely.ops import unary_union


class GeometryImportError(Exception):
    """A user-facing problem with the uploaded geometry file."""


@dataclass
class ImportedGeometry:
    geometry: dict       # GeoJSON geometry dict, WGS84
    latitude: float       # centroid, decimal degrees
    longitude: float      # centroid, decimal degrees
    crs_note: str          # human-readable note on the source CRS / reprojection
    feature_count: int    # number of source shapes/features found


def _check_centroid(lat: float, lng: float) -> None:
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise GeometryImportError(
            "Computed centroid is out of valid latitude/longitude range "
            f"({lat:.4f}, {lng:.4f}). The source file is likely in a projected "
            "coordinate system that wasn't detected — check its .prj / CRS."
        )


def _validate(geom):
    if not geom.is_valid:
        geom = geom.buffer(0)
    if geom.is_empty:
        raise GeometryImportError("The parsed geometry is empty.")
    return geom


# ---------------------------------------------------------------------------
# GeoJSON
# ---------------------------------------------------------------------------

def import_geojson_bytes(data: bytes) -> ImportedGeometry:
    try:
        payload = json.loads(data)
    except json.JSONDecodeError as exc:
        raise GeometryImportError(f"Not valid JSON: {exc}") from exc

    if not isinstance(payload, dict) or "type" not in payload:
        raise GeometryImportError("Not a recognizable GeoJSON object.")

    if payload["type"] == "FeatureCollection":
        feats = payload.get("features") or []
        if not feats:
            raise GeometryImportError("GeoJSON FeatureCollection has no features.")
        geom_json = feats[0].get("geometry")
        count = len(feats)
    elif payload["type"] == "Feature":
        geom_json = payload.get("geometry")
        count = 1
    else:
        geom_json = payload
        count = 1

    if not geom_json:
        raise GeometryImportError("No geometry found in the GeoJSON file.")

    try:
        geom = shape(geom_json)
    except Exception as exc:
        raise GeometryImportError(f"Could not parse geometry: {exc}") from exc

    geom = _validate(geom)
    centroid = geom.centroid
    _check_centroid(centroid.y, centroid.x)

    return ImportedGeometry(
        geometry=mapping(geom),
        latitude=centroid.y,
        longitude=centroid.x,
        crs_note="Assumed EPSG:4326 (GeoJSON coordinates are WGS84 per spec).",
        feature_count=count,
    )


# ---------------------------------------------------------------------------
# Shapefile ZIP
# ---------------------------------------------------------------------------

def _reproject_to_wgs84(geom, prj_text: str):
    try:
        src_crs = pyproj.CRS.from_wkt(prj_text)
    except Exception as exc:
        raise GeometryImportError(
            f"Could not parse the .prj coordinate system ({exc}); "
            "used the raw coordinates as-is, which may be wrong."
        ) from exc

    if src_crs.to_epsg() == 4326:
        return geom, "Source .prj is already EPSG:4326 (WGS84) — no reprojection needed."

    try:
        transformer = pyproj.Transformer.from_crs(src_crs, "EPSG:4326", always_xy=True)
        reprojected = shp_transform(lambda x, y, z=None: transformer.transform(x, y), geom)
    except Exception as exc:
        raise GeometryImportError(f"Failed to reproject from source CRS: {exc}") from exc

    label = src_crs.name or (f"EPSG:{src_crs.to_epsg()}" if src_crs.to_epsg() else "source CRS")
    return reprojected, f"Reprojected from {label} to EPSG:4326."


def import_shapefile_zip_bytes(data: bytes) -> ImportedGeometry:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise GeometryImportError(f"Not a valid ZIP archive: {exc}") from exc

    names = zf.namelist()
    shp_name = next((n for n in names if n.lower().endswith(".shp")), None)
    if not shp_name:
        raise GeometryImportError(
            "ZIP does not contain a .shp file. Include the .shp, .shx and .dbf "
            "(and ideally .prj) together in one ZIP."
        )
    base = shp_name[:-4]

    def _read(ext: str) -> bytes | None:
        for n in names:
            if n.lower() == (base + ext).lower():
                return zf.read(n)
        return None

    shp_bytes = zf.read(shp_name)
    dbf_bytes = _read(".dbf")
    shx_bytes = _read(".shx")
    prj_bytes = _read(".prj")

    try:
        reader = pyshp.Reader(
            shp=io.BytesIO(shp_bytes),
            dbf=io.BytesIO(dbf_bytes) if dbf_bytes else None,
            shx=io.BytesIO(shx_bytes) if shx_bytes else None,
        )
        shapes = reader.shapes()
    except Exception as exc:
        raise GeometryImportError(f"Could not read the shapefile: {exc}") from exc

    if not shapes:
        raise GeometryImportError("Shapefile contains no shapes.")

    geoms = []
    for s in shapes:
        try:
            geoms.append(shape(s.__geo_interface__))
        except Exception:
            continue
    if not geoms:
        raise GeometryImportError("None of the shapefile's shapes could be converted to geometry.")

    combined = geoms[0] if len(geoms) == 1 else unary_union(geoms)

    if prj_bytes:
        combined, crs_note = _reproject_to_wgs84(combined, prj_bytes.decode("utf-8", errors="ignore"))
    else:
        crs_note = "No .prj file included — assumed the coordinates are already EPSG:4326 (WGS84)."

    combined = _validate(combined)
    centroid = combined.centroid
    _check_centroid(centroid.y, centroid.x)

    return ImportedGeometry(
        geometry=mapping(combined),
        latitude=centroid.y,
        longitude=centroid.x,
        crs_note=crs_note,
        feature_count=len(shapes),
    )
