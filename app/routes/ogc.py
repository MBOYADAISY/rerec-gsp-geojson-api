from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.orm import Session
import json

from app.services.database import get_db

router = APIRouter(tags=["OGC API - Features"])

# ---- Collection registry ----
# Add more entries here later to expose more views/tables as ArcGIS layers.
#
# "geom_expression" is optional — if omitted, the raw geometry_field is used as-is.
# Use it when you want ArcGIS to receive a cheaper/simpler geometry (e.g. a
# centroid point instead of a full polygon) while still reading from the same
# underlying view/table.
COLLECTIONS = {
    "project-stage-detail": {
        "table": "rerec_geospatial.vw_project_stage_detail",
        "id_field": "project_reference_code",
        "geometry_field": "geometry",
        "title": "Project Stage Detail",
        "description": "Project stage progress with geometry",
    },
    # "project-stage-detail-points": {
    #     "table": "rerec_geospatial.vw_project_stage_detail",
    #     "id_field": "project_reference_code",
    #     "geometry_field": "geometry",
    #     "geom_expression": "ST_Centroid(geometry)",
    #     "title": "Project Stage Detail (Points)",
    #     "description": "Lightweight point version...",
    # },
}

# Max rows any single request can return. Lowered from 1000 -> 200 to keep
# per-request memory use safe on a 512MB instance. This does NOT reduce how
# much data ends up on the ArcGIS map -- ArcGIS follows the "next" link and
# keeps paging automatically until it has every record, it just does it in
# more, smaller requests instead of fewer, larger ones.
MAX_LIMIT = 200
DEFAULT_LIMIT = 100


def collection_or_404(collection_id: str):
    if collection_id not in COLLECTIONS:
        raise HTTPException(status_code=404, detail="Collection not found")
    return COLLECTIONS[collection_id]


# ---- Null-handling defaults ----
# ArcGIS infers its field schema from the FIRST feature it reads. If a field is
# null in that row, ArcGIS drops the field entirely from the layer's schema,
# even if later rows have real values. To avoid this, we replace nulls with a
# safe, type-appropriate default before returning the response.
FIELD_DEFAULTS = {
    "funding": "",
    "grid_solar": "",
    "request_date": "",  # empty string placeholder; ArcGIS treats this as a valid (blank) date field
    "lag_days": 0,
    "delay_reason": "",
}


def apply_field_defaults(row_dict: dict) -> dict:
    for field, default in FIELD_DEFAULTS.items():
        if field in row_dict and row_dict[field] is None:
            row_dict[field] = default
    return row_dict


# ---- 1. Landing page ----
@router.get("/")
def landing_page(request: Request):
    base = str(request.base_url).rstrip("/")
    return {
        "title": "REREC Geo API - OGC Features",
        "description": "OGC API - Features service for REREC spatial data",
        "links": [
            {"href": f"{base}/", "rel": "self", "type": "application/json", "title": "This document"},
            {"href": f"{base}/conformance", "rel": "conformance", "type": "application/json", "title": "Conformance"},
            {"href": f"{base}/collections", "rel": "data", "type": "application/json", "title": "Collections"},
        ],
    }


# ---- 2. Conformance ----
@router.get("/conformance")
def conformance():
    return {
        "conformsTo": [
            "http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/core",
            "http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/oas30",
            "http://www.opengis.net/spec/ogcapi-features-1/1.0/conf/geojson",
        ]
    }


# ---- 3. Collections list ----
@router.get("/collections")
def collections(request: Request):
    base = str(request.base_url).rstrip("/")
    result = []
    for cid, meta in COLLECTIONS.items():
        result.append({
            "id": cid,
            "title": meta["title"],
            "description": meta["description"],
            "links": [
                {"href": f"{base}/collections/{cid}", "rel": "self", "type": "application/json"},
                {"href": f"{base}/collections/{cid}/items", "rel": "items", "type": "application/geo+json"},
            ],
        })
    return {"collections": result, "links": [{"href": f"{base}/collections", "rel": "self"}]}


# ---- 4. Single collection metadata ----
@router.get("/collections/{collection_id}")
def collection_detail(collection_id: str, request: Request):
    meta = collection_or_404(collection_id)
    base = str(request.base_url).rstrip("/")
    return {
        "id": collection_id,
        "title": meta["title"],
        "description": meta["description"],
        "links": [
            {"href": f"{base}/collections/{collection_id}/items", "rel": "items", "type": "application/geo+json"},
        ],
        "extent": {},
        "itemType": "feature",
    }


# ---- 5. Items (the actual features) ----
# Streams the response instead of building the full feature list in memory
# first. On a 512MB instance, buffering ~200 rows of heavy MultiPolygon
# geometry as Python objects (row dicts + feature dicts + the final JSON
# string) before sending anything was likely what pushed memory over the
# limit -- streaming means only one row's worth of data needs to be in
# memory at any given moment while the response is sent.
@router.get("/collections/{collection_id}/items")
def collection_items(
    collection_id: str,
    request: Request,
    db: Session = Depends(get_db),
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
    bbox: str = None,
):
    meta = collection_or_404(collection_id)
    table = meta["table"]
    id_field = meta["id_field"]
    geom_field = meta["geometry_field"]
    geom_expr = meta.get("geom_expression", geom_field)

    limit = max(1, min(limit, MAX_LIMIT))

    where_clause = ""
    params = {"limit": limit, "offset": offset}

    if bbox:
        try:
            minx, miny, maxx, maxy = [float(v) for v in bbox.split(",")]
        except ValueError:
            raise HTTPException(status_code=400, detail="bbox must be minx,miny,maxx,maxy")
        where_clause = f"WHERE {geom_expr} && ST_MakeEnvelope(:minx, :miny, :maxx, :maxy, 4326)"
        params.update({"minx": minx, "miny": miny, "maxx": maxx, "maxy": maxy})

    count_query = text(f"""
        SELECT COUNT(*) AS total
        FROM {table}
        {where_clause};
    """)
    count_params = {k: v for k, v in params.items() if k not in ("limit", "offset")}
    total_count = db.execute(count_query, count_params).scalar()

    query = text(f"""
        SELECT *, ST_AsGeoJSON({geom_expr})::json AS geojson_geometry
        FROM {table}
        {where_clause}
        ORDER BY {id_field}
        LIMIT :limit OFFSET :offset;
    """)

    base = str(request.base_url).rstrip("/")

    links = [
        {"href": f"{base}/collections/{collection_id}/items", "rel": "self", "type": "application/geo+json"},
    ]
    next_offset = offset + limit
    if next_offset < total_count:
        next_url = f"{base}/collections/{collection_id}/items?limit={limit}&offset={next_offset}"
        if bbox:
            next_url += f"&bbox={bbox}"
        links.append({"href": next_url, "rel": "next", "type": "application/geo+json"})

    def generate():
        yield '{"type": "FeatureCollection", '
        yield f'"numberMatched": {total_count}, '
        yield '"features": ['

        # server_side_cursors: execute() returns a result we iterate row by
        # row rather than calling .all(), so SQLAlchemy/psycopg doesn't need
        # to materialize every row in memory before we start streaming.
        result = db.execute(query, params)
        first = True
        count = 0
        for row in result.mappings():
            row_dict = dict(row)
            geometry = row_dict.pop("geojson_geometry")
            row_dict.pop(geom_field, None)
            row_dict = apply_field_defaults(row_dict)
            feature = {
                "type": "Feature",
                "id": row_dict.get(id_field),
                "geometry": geometry,
                "properties": row_dict,
            }
            if not first:
                yield ","
            yield json.dumps(feature, default=str)
            first = False
            count += 1

        yield f'], "numberReturned": {count}, '
        yield '"links": ' + json.dumps(links)
        yield '}'

    return StreamingResponse(generate(), media_type="application/geo+json")


# ---- 6. Single feature by ID ----
@router.get("/collections/{collection_id}/items/{feature_id}")
def collection_item(collection_id: str, feature_id: str, db: Session = Depends(get_db)):
    meta = collection_or_404(collection_id)
    table = meta["table"]
    id_field = meta["id_field"]
    geom_field = meta["geometry_field"]
    geom_expr = meta.get("geom_expression", geom_field)

    query = text(f"""
        SELECT *, ST_AsGeoJSON({geom_expr})::json AS geojson_geometry
        FROM {table}
        WHERE {id_field} = :feature_id;
    """)
    row = db.execute(query, {"feature_id": feature_id}).mappings().first()

    if not row:
        raise HTTPException(status_code=404, detail="Feature not found")

    row_dict = dict(row)
    geometry = row_dict.pop("geojson_geometry")
    row_dict.pop(geom_field, None)
    row_dict = apply_field_defaults(row_dict)

    return {
        "type": "Feature",
        "id": row_dict.get(id_field),
        "geometry": geometry,
        "properties": row_dict,
    }