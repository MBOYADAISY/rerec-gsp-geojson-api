from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.database import get_db

router = APIRouter(tags=["OGC API - Features"])

# ---- Collection registry ----
# Add more entries here later to expose more views/tables as ArcGIS layers.
COLLECTIONS = {
    "project-stage-detail": {
        "table": "rerec_geospatial.vw_project_stage_detail",
        "id_field": "project_reference_code",
        "geometry_field": "geometry",
        "title": "Project Stage Detail",
        "description": "Project stage progress with geometry",
    }
}


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
@router.get("/collections/{collection_id}/items")
def collection_items(
    collection_id: str,
    request: Request,
    db: Session = Depends(get_db),
    limit: int = 100,
    offset: int = 0,
    bbox: str = None,
):
    meta = collection_or_404(collection_id)
    table = meta["table"]
    id_field = meta["id_field"]
    geom_field = meta["geometry_field"]

    limit = max(1, min(limit, 1000))  # cap to protect the server

    where_clause = ""
    params = {"limit": limit, "offset": offset}

    if bbox:
        try:
            minx, miny, maxx, maxy = [float(v) for v in bbox.split(",")]
        except ValueError:
            raise HTTPException(status_code=400, detail="bbox must be minx,miny,maxx,maxy")
        where_clause = f"WHERE {geom_field} && ST_MakeEnvelope(:minx, :miny, :maxx, :maxy, 4326)"
        params.update({"minx": minx, "miny": miny, "maxx": maxx, "maxy": maxy})

    # --- total count of matching records (for numberMatched / pagination) ---
    count_query = text(f"""
        SELECT COUNT(*) AS total
        FROM {table}
        {where_clause};
    """)
    count_params = {k: v for k, v in params.items() if k not in ("limit", "offset")}
    total_count = db.execute(count_query, count_params).scalar()

    # --- actual page of features ---
    query = text(f"""
        SELECT *, ST_AsGeoJSON({geom_field})::json AS geojson_geometry
        FROM {table}
        {where_clause}
        ORDER BY {id_field}
        LIMIT :limit OFFSET :offset;
    """)

    rows = db.execute(query, params).mappings().all()

    features = []
    for row in rows:
        row_dict = dict(row)
        geometry = row_dict.pop("geojson_geometry")
        row_dict.pop(geom_field, None)  # drop raw geometry, keep only GeoJSON version
        row_dict = apply_field_defaults(row_dict)
        features.append({
            "type": "Feature",
            "id": row_dict.get(id_field),
            "geometry": geometry,
            "properties": row_dict,
        })

    base = str(request.base_url).rstrip("/")
    links = [
        {"href": f"{base}/collections/{collection_id}/items", "rel": "self", "type": "application/geo+json"},
    ]

    # add a "next" link if there are more records beyond this page
    next_offset = offset + limit
    if next_offset < total_count:
        next_url = f"{base}/collections/{collection_id}/items?limit={limit}&offset={next_offset}"
        if bbox:
            next_url += f"&bbox={bbox}"
        links.append({"href": next_url, "rel": "next", "type": "application/geo+json"})

    return {
        "type": "FeatureCollection",
        "numberMatched": total_count,
        "numberReturned": len(features),
        "features": features,
        "links": links,
    }


# ---- 6. Single feature by ID ----
@router.get("/collections/{collection_id}/items/{feature_id}")
def collection_item(collection_id: str, feature_id: str, db: Session = Depends(get_db)):
    meta = collection_or_404(collection_id)
    table = meta["table"]
    id_field = meta["id_field"]
    geom_field = meta["geometry_field"]

    query = text(f"""
        SELECT *, ST_AsGeoJSON({geom_field})::json AS geojson_geometry
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