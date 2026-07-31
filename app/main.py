from fastapi import FastAPI
from app.routes.projects import router as projects_router
from app.routes.ogc import router as ogc_router

app = FastAPI(
    title="REREC Geospatial API",
    version="1.0.0"
)

app.include_router(projects_router)
app.include_router(ogc_router)