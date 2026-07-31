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
    return {"message": "projects endpoint works"}

@router.get("/views/project-stage-detail")
def get_project_stage_detail(db: Session = Depends(get_db)):
    result = db.execute(text("""
        SELECT *
        FROM rerec_geospatial.vw_project_stage_detail
        ORDER BY project_reference_code;
    """))
    return [dict(row._mapping) for row in result]