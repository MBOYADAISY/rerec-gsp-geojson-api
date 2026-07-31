from sqlalchemy import text
from app.services.database import SessionLocal

db = SessionLocal()

try:
    result = db.execute(text("""
        SELECT COUNT(*)
        FROM rerec_geospatial.projects;
    """))

    print("Projects:", result.scalar())

finally:
    db.close()