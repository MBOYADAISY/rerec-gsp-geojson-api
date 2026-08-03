from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.routes.projects import router as projects_router
from app.routes.ogc import router as ogc_router

app = FastAPI(
    title="REREC Geospatial API",
    version="1.0.0"
)

# CORS: required for browser-based clients (ArcGIS Online, dashboards, etc.)
# to be able to call this API directly. Without this, the browser blocks the
# request before it even reaches the server, which shows up in ArcGIS as a
# generic "layer not accessible" error rather than a clear message.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)

app.include_router(projects_router)
app.include_router(ogc_router)