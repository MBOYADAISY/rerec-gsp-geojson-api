from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.services.database import get_db

router = APIRouter(tags=["Projects"])


@router.get("/projects")
def get_projects(db: Session = Depends(get_db)):
    result = db.execute(text("""
        SELECT
            project_reference_code,
            project_reference_number,
            project_name,
            constituency_pcode,
            project_category
        FROM rerec_geospatial.projects
        ORDER BY project_name;
    """))
    return [dict(row._mapping) for row in result]


@router.get("/api/projects.geojson")
def projects_geojson(db: Session = Depends(get_db)):
    # NOTE: projects itself has no geometry column -- constituency_pcode is
    # the only spatial link, via administrative_units. Joining here so this
    # endpoint returns real GeoJSON (constituency boundary per project)
    # instead of the placeholder stub it had before.
    result = db.execute(text("""
        SELECT
            p.project_reference_code,
            p.project_reference_number,
            p.project_name,
            p.constituency_pcode,
            p.project_category,
            ST_AsGeoJSON(au.geometry)::json AS geojson_geometry
        FROM rerec_geospatial.projects p
        LEFT JOIN rerec_geospatial.administrative_units au
            ON au.constituency_pcode::text = p.constituency_pcode::text
        ORDER BY p.project_name;
    """))

    features = []
    for row in result.mappings():
        row_dict = dict(row)
        geometry = row_dict.pop("geojson_geometry")
        features.append({
            "type": "Feature",
            "id": row_dict.get("project_reference_code"),
            "geometry": geometry,
            "properties": row_dict,
        })

    return {"type": "FeatureCollection", "features": features}


@router.get("/views/project-stage-detail")
def get_project_stage_detail(db: Session = Depends(get_db)):
    # NOTE: previously returned the raw geometry column as-is, which comes
    # back as unusable WKB/binary rather than something a map or JSON
    # client can render. Converting via ST_AsGeoJSON like the OGC endpoints
    # do, so this is consistent with the rest of the API.
    result = db.execute(text("""
        SELECT *, ST_AsGeoJSON(geometry)::json AS geojson_geometry
        FROM rerec_geospatial.vw_project_stage_detail
        ORDER BY project_reference_code, stage_order;
    """))

    rows = []
    for row in result.mappings():
        row_dict = dict(row)
        row_dict["geometry"] = row_dict.pop("geojson_geometry")
        rows.append(row_dict)

    return rows