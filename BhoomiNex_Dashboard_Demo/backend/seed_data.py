"""Creates tables (if needed) and seeds demo data so the dashboard looks
exactly like the standalone localStorage demo on first run.

Seeded login: username `admin`, password `demo123` (same defaults the
login screen pre-fills), matching the UI's <input value="admin"> /
<input value="demo123">.
"""
import json
from datetime import datetime, timedelta

from sqlalchemy import inspect, text

from database import Base, SessionLocal, engine
import models
from security import hash_password

# A small rectangle around REC20260914-001's village, just so the demo has
# one record with a real imported-style polygon out of the box (the rest
# only get a point centroid — closer to what a fresh install actually
# looks like before anyone has imported shapefiles/GeoJSON).
_REC_001_POLYGON = {
    "type": "Polygon",
    "coordinates": [[
        [77.2660, 28.1478], [77.2692, 28.1478], [77.2692, 28.1498], [77.2660, 28.1498], [77.2660, 28.1478],
    ]],
}

SEED_USERS = [
    dict(username="admin", password="demo123", name="Admin", role="Administrator", department="Land Records"),
    dict(username="priya.sharma", password="verifier123", name="Priya Sharma", role="Verifier", department="Revenue"),
    dict(username="rahul.kumar", password="operator123", name="Rahul Kumar", role="Operator", department="Digitization"),
]

# Mirrors initialRecords in the frontend's inline <script>, filled out with
# the extra land-record fields the Validation Result view displays.
SEED_RECORDS = [
    dict(
        id="REC20260914-001", file_name="khasra_001.jpg", state="Haryana", status="Verified",
        owner="Ramesh Kumar", khasra="123/2", khata="45", village="Aurangabad",
        tehsil="Ballabgarh", district="Palwal", area="2.50 Acres", mutation_no="789",
        confidence=97, days_ago=1,
        # Real imported-style parcel: point + polygon, so the demo shows what
        # a shapefile/GeoJSON import looks like on the map out of the box.
        latitude=28.1488, longitude=77.2676, geometry=_REC_001_POLYGON, geometry_source="seed",
    ),
    dict(
        id="REC20260914-002", file_name="land_record.pdf", state="Uttar Pradesh", status="Processed",
        owner="Suresh Yadav", khasra="88/1", khata="12", village="Rampur",
        tehsil="Sadar", district="Lucknow", area="1.75 Acres", mutation_no="221",
        confidence=82, days_ago=1,
        # Approximate village-level point only — no boundary imported yet.
        latitude=26.8467, longitude=80.9462, geometry=None, geometry_source=None,
    ),
    dict(
        id="REC20260913-003", file_name="jamabandi.png", state="Rajasthan", status="Needs Review",
        owner="Mohan Lal", khasra="45/3", khata="9", village="Sanganer",
        tehsil="Sanganer", district="Jaipur", area="3.10 Acres", mutation_no="512",
        confidence=58, days_ago=2,
        # No geometry imported at all — Map View will fall back to the
        # state centroid for this one, flagged as approximate.
        latitude=None, longitude=None, geometry=None, geometry_source=None,
    ),
    dict(
        id="REC20260913-004", file_name="patta_doc.jpg", state="Tamil Nadu", status="Verified",
        owner="Karthik Raja", khasra="12/4", khata="3", village="Kelambakkam",
        tehsil="Thiruporur", district="Chengalpattu", area="0.90 Acres", mutation_no="147",
        confidence=96, days_ago=2,
        latitude=12.7910, longitude=80.2213, geometry=None, geometry_source=None,
    ),
    dict(
        id="REC20260912-005", file_name="record_2026.pdf", state="Maharashtra", status="Processed",
        owner="Anita Deshmukh", khasra="67/2", khata="21", village="Shirur",
        tehsil="Shirur", district="Pune", area="4.20 Acres", mutation_no="903",
        confidence=84, days_ago=3,
        latitude=None, longitude=None, geometry=None, geometry_source=None,
    ),
]

# Columns added for per-record GIS geometry. Kept here (rather than a full
# Alembic migration) so an existing SQLite demo DB from before this feature
# still works after a git pull — `create_all` only creates missing *tables*,
# not new columns on ones that already exist.
_GEOMETRY_COLUMNS = {
    "latitude": "FLOAT",
    "longitude": "FLOAT",
    "geometry_geojson": "TEXT",
    "geometry_source": "VARCHAR(40)",
    "geometry_crs_note": "VARCHAR(255)",
    "geometry_filename": "VARCHAR(255)",
    "geometry_imported_at": "DATETIME",
}


def _ensure_geometry_columns() -> None:
    inspector = inspect(engine)
    if "records" not in inspector.get_table_names():
        return  # create_all (called right before this) will create it fresh, columns included
    existing = {c["name"] for c in inspector.get_columns("records")}
    missing = {name: coltype for name, coltype in _GEOMETRY_COLUMNS.items() if name not in existing}
    if not missing:
        return
    with engine.begin() as conn:
        for name, coltype in missing.items():
            conn.execute(text(f"ALTER TABLE records ADD COLUMN {name} {coltype}"))


def bootstrap() -> None:
    Base.metadata.create_all(bind=engine)
    _ensure_geometry_columns()
    db = SessionLocal()
    try:
        if db.query(models.User).count() == 0:
            for u in SEED_USERS:
                db.add(models.User(
                    username=u["username"],
                    password_hash=hash_password(u["password"]),
                    name=u["name"],
                    role=u["role"],
                    department=u["department"],
                    status="Active",
                ))

        if db.query(models.Record).count() == 0:
            now = datetime.utcnow()
            for r in SEED_RECORDS:
                uploaded = now - timedelta(days=r["days_ago"])
                db.add(models.Record(
                    id=r["id"], file_name=r["file_name"], state=r["state"], status=r["status"],
                    owner=r["owner"], khasra=r["khasra"], khata=r["khata"], village=r["village"],
                    tehsil=r["tehsil"], district=r["district"], area=r["area"], mutation_no=r["mutation_no"],
                    confidence=r["confidence"], field_confidence_json="{}", validation_notes_json="[]",
                    source_path="", raw_text="", uploaded_on=uploaded, updated_at=uploaded,
                    verified_by=("Admin" if r["status"] == "Verified" else None),
                    verified_at=(uploaded if r["status"] == "Verified" else None),
                    latitude=r.get("latitude"), longitude=r.get("longitude"),
                    geometry_geojson=json.dumps(r["geometry"]) if r.get("geometry") else None,
                    geometry_source=r.get("geometry_source"),
                    geometry_crs_note=("Demo seed coordinate — not surveyed" if r.get("latitude") is not None else None),
                    geometry_imported_at=(uploaded if r.get("geometry_source") else None),
                ))
                db.flush()
                db.add(models.AuditLog(record_id=r["id"], action="seeded", actor="system",
                                        details="Initial demo record"))

        if db.query(models.Setting).count() == 0:
            db.add_all([
                models.Setting(key="email_notifications", value="true"),
                models.Setting(key="auto_validation", value="true"),
                models.Setting(key="compact_dashboard", value="false"),
            ])

        db.commit()
    finally:
        db.close()
