"""BHOOMI-AI backend — FastAPI app.

Run:
    uvicorn main:app --reload --port 8000

Docs:
    http://127.0.0.1:8000/docs

This implements the SIH26018 pipeline (input -> preprocessing -> OCR/AI
understanding -> validation -> confidence scoring -> human verification ->
trusted record) as real, working endpoints backed by SQLite (swap
DATABASE_URL for Postgres in production — see .env.example).

The existing static dashboard (index.html) is untouched; this API is
designed to be a drop-in data source for it. See README_BACKEND.md for how
each endpoint maps to a UI action.
"""
import json
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

import geo_import
import models
import pipeline
import schemas
from config import settings
from database import get_db
from security import (
    create_access_token, get_current_user, hash_password, require_admin, verify_password,
)
from seed_data import bootstrap

app = FastAPI(title="BHOOMI-AI API", version="1.0.0", description="Intelligent Land Record Digitization and Validation System — SIH26018")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    bootstrap()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row(record: models.Record) -> schemas.RecordRow:
    return schemas.RecordRow(
        id=record.id,
        name=record.file_name,
        state=record.state,
        date=record.uploaded_on.strftime("%d %b %Y"),
        status=record.status,
        confidence=record.confidence or 0,
    )


def _detail(record: models.Record) -> schemas.RecordDetail:
    try:
        fc = json.loads(record.field_confidence_json or "{}")
    except (TypeError, json.JSONDecodeError):
        fc = {}
    try:
        notes = json.loads(record.validation_notes_json or "[]")
    except (TypeError, json.JSONDecodeError):
        notes = []
    try:
        geometry = json.loads(record.geometry_geojson) if record.geometry_geojson else None
    except (TypeError, json.JSONDecodeError):
        geometry = None
    return schemas.RecordDetail(
        id=record.id, state=record.state, district=record.district, village=record.village,
        tehsil=record.tehsil, khasra=record.khasra, khata=record.khata, owner=record.owner,
        area=record.area, mutation_no=record.mutation_no, status=record.status,
        confidence=record.confidence or 0, field_confidence=schemas.FieldConfidence(**fc),
        validation_notes=notes, file_name=record.file_name, uploaded_on=record.uploaded_on,
        updated_at=record.updated_at, verified_by=record.verified_by, verified_at=record.verified_at,
        latitude=record.latitude, longitude=record.longitude, geometry=geometry,
        geometry_source=record.geometry_source, geometry_crs_note=record.geometry_crs_note,
        geometry_filename=record.geometry_filename, geometry_imported_at=record.geometry_imported_at,
    )


def _log(db: Session, record_id: str, action: str, actor: str, details: str = "") -> None:
    db.add(models.AuditLog(record_id=record_id, action=action, actor=actor, details=details))


def _next_record_id(db: Session) -> str:
    today = datetime.utcnow().strftime("%Y%m%d")
    count = db.query(models.Record).filter(models.Record.id.like(f"REC{today}-%")).count()
    return f"REC{today}-{count + 1:03d}"


def _setting(db: Session, key: str, default: bool) -> bool:
    row = db.query(models.Setting).filter(models.Setting.key == key).first()
    return (row.value == "true") if row else default


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    return {"status": "ok", "service": "BHOOMI-AI API"}


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@app.post("/api/auth/login", response_model=schemas.TokenResponse)
def login(payload: schemas.LoginRequest, db: Session = Depends(get_db)):
    user = db.query(models.User).filter(models.User.username == payload.username).first()
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password")
    if user.status != "Active":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account has been deactivated")
    token = create_access_token(subject=user.username)
    return schemas.TokenResponse(access_token=token, user=schemas.UserOut.model_validate(user))


@app.get("/api/auth/me", response_model=schemas.UserOut)
def me(current: models.User = Depends(get_current_user)):
    return current


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

@app.get("/api/dashboard", response_model=schemas.DashboardStats)
def dashboard(db: Session = Depends(get_db), current: models.User = Depends(get_current_user)):
    records = db.query(models.Record).order_by(models.Record.uploaded_on.desc()).all()
    total = len(records)
    processed = sum(1 for r in records if r.status != "Needs Review")
    verified = sum(1 for r in records if r.status == "Verified")
    needs_review = sum(1 for r in records if r.status == "Needs Review")

    by_state: dict[str, int] = {}
    for r in records:
        by_state[r.state] = by_state.get(r.state, 0) + 1

    pipeline_state = [
        {"name": "Input", "state": "complete"},
        {"name": "AI extract", "state": "complete"},
        {"name": "Validate", "state": "complete" if total else "pending"},
        {"name": "Human verify", "state": "active" if needs_review else "complete"},
        {"name": "Trusted record", "state": "complete" if verified else "pending"},
    ]

    return schemas.DashboardStats(
        total=total, processed=processed, verified=verified, needs_review=needs_review,
        by_state=by_state, recent=[_row(r) for r in records[:5]], pipeline=pipeline_state,
    )


@app.get("/api/analytics", response_model=schemas.AnalyticsResponse)
def analytics(db: Session = Depends(get_db), current: models.User = Depends(get_current_user)):
    records = db.query(models.Record).all()
    total = len(records)
    verified = sum(1 for r in records if r.status == "Verified")
    processed = sum(1 for r in records if r.status != "Needs Review")
    review = sum(1 for r in records if r.status == "Needs Review")
    return schemas.AnalyticsResponse(
        verify_rate=round(verified / total * 100) if total else 0,
        process_rate=round(processed / total * 100) if total else 0,
        pending_review=review, total=total,
    )


@app.get("/api/map", response_model=list[schemas.MapFeature])
def map_view(db: Session = Depends(get_db), current: models.User = Depends(get_current_user)):
    """One feature per record. `latitude`/`longitude`/`geometry` are the
    real, imported parcel data when present; null otherwise — the
    dashboard's Map View falls back to an approximate state centroid for
    those and flags them as approximate, rather than hiding them."""
    records = db.query(models.Record).order_by(models.Record.uploaded_on.desc()).all()
    features = []
    for r in records:
        try:
            geometry = json.loads(r.geometry_geojson) if r.geometry_geojson else None
        except json.JSONDecodeError:
            geometry = None
        features.append(schemas.MapFeature(
            id=r.id, name=r.file_name, state=r.state, district=r.district, village=r.village,
            owner=r.owner, khasra=r.khasra, status=r.status, confidence=r.confidence or 0,
            latitude=r.latitude, longitude=r.longitude, geometry=geometry,
        ))
    return features


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------

@app.get("/api/records", response_model=list[schemas.RecordRow])
def list_records(
    q: Optional[str] = Query(None, description="Free-text filter across file name / state / status"),
    state: Optional[str] = None,
    status_: Optional[str] = Query(None, alias="status"),
    db: Session = Depends(get_db),
    current: models.User = Depends(get_current_user),
):
    query = db.query(models.Record)
    if state:
        query = query.filter(models.Record.state == state)
    if status_:
        query = query.filter(models.Record.status == status_)
    records = query.order_by(models.Record.uploaded_on.desc()).all()
    if q:
        needle = q.lower()
        records = [
            r for r in records
            if needle in r.file_name.lower() or needle in r.state.lower() or needle in r.status.lower()
            or (r.owner and needle in r.owner.lower()) or (r.khasra and needle in r.khasra.lower())
            or (r.village and needle in r.village.lower())
        ]
    return [_row(r) for r in records]


@app.get("/api/records/{record_id}", response_model=schemas.RecordDetail)
def get_record(record_id: str, db: Session = Depends(get_db), current: models.User = Depends(get_current_user)):
    record = db.get(models.Record, record_id)
    if not record:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Record not found")
    return _detail(record)


@app.get("/api/records/{record_id}/file")
def get_record_file(record_id: str, db: Session = Depends(get_db), current: models.User = Depends(get_current_user)):
    """Serves the original uploaded scan/PDF, used by the side-by-side
    human-verification screen so the officer can compare the source
    document against the AI-extracted fields."""
    record = db.get(models.Record, record_id)
    if not record:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Record not found")
    if not record.source_path or not Path(record.source_path).exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No source document stored for this record")
    return FileResponse(record.source_path)


@app.delete("/api/records/{record_id}")
def delete_record(record_id: str, db: Session = Depends(get_db), current: models.User = Depends(require_admin)):
    record = db.get(models.Record, record_id)
    if not record:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Record not found")
    db.delete(record)
    db.commit()
    return {"deleted": True, "id": record_id}


@app.put("/api/records/{record_id}/verify", response_model=schemas.RecordDetail)
def verify_record(
    record_id: str, payload: schemas.VerifyRequest,
    db: Session = Depends(get_db), current: models.User = Depends(get_current_user),
):
    record = db.get(models.Record, record_id)
    if not record:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Record not found")

    fields_for_check = {
        "owner": pipeline.ExtractedField(value=payload.owner, matched_label=True),
        "khasra": pipeline.ExtractedField(value=payload.khasra, matched_label=True),
        "village": pipeline.ExtractedField(value=payload.village, matched_label=True),
        "district": pipeline.ExtractedField(value=payload.district, matched_label=True),
    }
    notes, blocking = pipeline.validate_fields(fields_for_check, db, exclude_id=record.id)

    record.owner = payload.owner
    record.khasra = payload.khasra
    record.khata = payload.khata or record.khata
    record.village = payload.village
    record.tehsil = payload.tehsil or record.tehsil
    record.district = payload.district
    record.area = payload.area
    record.mutation_no = payload.mutation_no or record.mutation_no
    record.validation_notes_json = json.dumps(notes)

    if blocking:
        record.status = "Needs Review"
    else:
        record.status = payload.status or "Verified"
        record.confidence = 100 if record.status == "Verified" else record.confidence
        record.verified_by = current.name
        record.verified_at = datetime.utcnow()

    record.updated_at = datetime.utcnow()
    _log(db, record.id, "verified" if not blocking else "verify_attempted",
         current.name, "Officer reviewed and corrected fields" if not blocking else "; ".join(notes))
    db.commit()
    db.refresh(record)
    return _detail(record)


# ---------------------------------------------------------------------------
# GIS: per-record parcel geometry
# ---------------------------------------------------------------------------

GEOMETRY_EXTENSIONS = {".geojson", ".json", ".zip"}


@app.post("/api/records/{record_id}/geometry", response_model=schemas.GeometryImportResponse)
async def import_geometry(
    record_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current: models.User = Depends(get_current_user),
):
    """Import real parcel geometry for one record from a GeoJSON file or a
    zipped shapefile (.shp + .dbf/.shx, optionally .prj). Coordinates are
    normalized to WGS84 and a centroid is derived for `latitude`/`longitude`,
    which is what powers the precise marker/polygon in Map View — records
    without an import still show up there via an approximate state centroid."""
    record = db.get(models.Record, record_id)
    if not record:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Record not found")
    if not file.filename:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A GeoJSON file or shapefile ZIP is required")

    suffix = Path(file.filename).suffix.lower()
    if suffix not in GEOMETRY_EXTENSIONS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Upload a .geojson/.json file, or a .zip containing a shapefile (.shp/.shx/.dbf, ideally .prj)",
        )

    contents = await file.read()
    if len(contents) > settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"File exceeds {settings.MAX_UPLOAD_SIZE_MB}MB limit")

    try:
        if suffix == ".zip":
            imported = geo_import.import_shapefile_zip_bytes(contents)
            source = "shapefile"
        else:
            imported = geo_import.import_geojson_bytes(contents)
            source = "geojson"
    except geo_import.GeometryImportError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    record.latitude = imported.latitude
    record.longitude = imported.longitude
    record.geometry_geojson = json.dumps(imported.geometry)
    record.geometry_source = source
    record.geometry_crs_note = imported.crs_note
    record.geometry_filename = file.filename
    record.geometry_imported_at = datetime.utcnow()
    record.updated_at = datetime.utcnow()

    _log(db, record.id, "geometry_imported", current.name,
         f"{source} · {file.filename} · {imported.feature_count} shape(s) · {imported.crs_note}")
    db.commit()
    db.refresh(record)

    return schemas.GeometryImportResponse(
        id=record.id, latitude=record.latitude, longitude=record.longitude,
        geometry=imported.geometry, geometry_source=source, crs_note=imported.crs_note,
        message=f"Parcel geometry imported from {file.filename}.",
    )


@app.delete("/api/records/{record_id}/geometry")
def clear_geometry(record_id: str, db: Session = Depends(get_db), current: models.User = Depends(get_current_user)):
    record = db.get(models.Record, record_id)
    if not record:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Record not found")
    record.latitude = None
    record.longitude = None
    record.geometry_geojson = None
    record.geometry_source = None
    record.geometry_crs_note = None
    record.geometry_filename = None
    record.geometry_imported_at = None
    record.updated_at = datetime.utcnow()
    _log(db, record.id, "geometry_removed", current.name, "")
    db.commit()
    return {"cleared": True, "id": record_id}


# ---------------------------------------------------------------------------
# Upload / pipeline
# ---------------------------------------------------------------------------

@app.post("/api/upload", response_model=schemas.UploadResponse)
async def upload_document(
    file: UploadFile = File(...),
    state: str = Form("Haryana"),
    status_override: Optional[str] = Form(None, alias="status"),
    db: Session = Depends(get_db),
    current: models.User = Depends(get_current_user),
):
    if not file.filename:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A document is required")

    suffix = Path(file.filename).suffix.lower()
    if suffix not in (pipeline.IMAGE_EXTENSIONS | pipeline.PDF_EXTENSIONS):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Unsupported file type: {suffix or 'unknown'}")

    contents = await file.read()
    if len(contents) > settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"File exceeds {settings.MAX_UPLOAD_SIZE_MB}MB limit")

    stored_name = f"{uuid.uuid4().hex}{suffix}"
    stored_path = settings.UPLOAD_DIR / stored_name
    with open(stored_path, "wb") as f:
        f.write(contents)

    record_id = _next_record_id(db)
    _log(db, record_id, "uploaded", current.name, f"Original filename: {file.filename}")

    try:
        result = pipeline.run_pipeline(stored_path, db)
    except Exception as exc:  # OCR/poppler misconfigured, corrupt file, etc.
        record = models.Record(
            id=record_id, file_name=file.filename, state=state, status="Needs Review",
            confidence=0, field_confidence_json="{}",
            validation_notes_json=json.dumps([f"Automated processing failed: {exc}. Please enter fields manually."]),
            source_path=str(stored_path), raw_text="", uploaded_on=datetime.utcnow(), updated_at=datetime.utcnow(),
        )
        db.add(record)
        _log(db, record_id, "pipeline_error", "system", str(exc))
        db.commit()
        db.refresh(record)
        return schemas.UploadResponse(record=_detail(record), message="Upload saved, but automated extraction failed — please review manually.")

    final_status = status_override or result.status
    field_conf = {name: f.confidence for name, f in result.fields.items()}

    record = models.Record(
        id=record_id, file_name=file.filename, state=state, status=final_status,
        owner=result.fields["owner"].value, khasra=result.fields["khasra"].value,
        khata=result.fields["khata"].value, village=result.fields["village"].value,
        tehsil=result.fields["tehsil"].value, district=result.fields["district"].value,
        area=result.fields["area"].value, mutation_no=result.fields["mutation_no"].value,
        confidence=result.overall_confidence, field_confidence_json=json.dumps(field_conf),
        validation_notes_json=json.dumps(result.validation_notes),
        source_path=str(stored_path), raw_text=result.raw_text[:8000],
        uploaded_on=datetime.utcnow(), updated_at=datetime.utcnow(),
    )
    if final_status == "Verified":
        record.verified_by = current.name
        record.verified_at = datetime.utcnow()

    db.add(record)
    _log(db, record_id, "auto_validated", "system",
         f"confidence={result.overall_confidence}, status={final_status}, notes={len(result.validation_notes)}")
    db.commit()
    db.refresh(record)

    message = (
        "Record validated successfully." if final_status == "Verified" else
        "Document processed. Minor points may need a check." if final_status == "Processed" else
        "Uncertain fields flagged — please route this record to a verifier."
    )
    return schemas.UploadResponse(record=_detail(record), message=message)


# ---------------------------------------------------------------------------
# Users (admin)
# ---------------------------------------------------------------------------

@app.get("/api/users", response_model=list[schemas.UserOut])
def list_users(db: Session = Depends(get_db), current: models.User = Depends(get_current_user)):
    return db.query(models.User).order_by(models.User.id).all()


@app.post("/api/users", response_model=schemas.UserOut, status_code=status.HTTP_201_CREATED)
def create_user(payload: schemas.UserCreate, db: Session = Depends(get_db), current: models.User = Depends(require_admin)):
    if db.query(models.User).filter(models.User.username == payload.username).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "Username already exists")
    user = models.User(
        username=payload.username, password_hash=hash_password(payload.password),
        name=payload.name, role=payload.role, department=payload.department, status="Active",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@app.delete("/api/users/{user_id}")
def delete_user(user_id: int, db: Session = Depends(get_db), current: models.User = Depends(require_admin)):
    user = db.get(models.User, user_id)
    if not user:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    if user.username == "admin":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The primary admin account cannot be removed")
    db.delete(user)
    db.commit()
    return {"deleted": True, "id": user_id}


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

@app.get("/api/settings", response_model=schemas.SettingsPayload)
def get_settings(db: Session = Depends(get_db), current: models.User = Depends(get_current_user)):
    return schemas.SettingsPayload(
        email_notifications=_setting(db, "email_notifications", True),
        auto_validation=_setting(db, "auto_validation", True),
        compact_dashboard=_setting(db, "compact_dashboard", False),
    )


@app.put("/api/settings", response_model=schemas.SettingsPayload)
def update_settings(payload: schemas.SettingsPayload, db: Session = Depends(get_db), current: models.User = Depends(get_current_user)):
    mapping = {
        "email_notifications": payload.email_notifications,
        "auto_validation": payload.auto_validation,
        "compact_dashboard": payload.compact_dashboard,
    }
    for key, value in mapping.items():
        row = db.query(models.Setting).filter(models.Setting.key == key).first()
        str_value = "true" if value else "false"
        if row:
            row.value = str_value
        else:
            db.add(models.Setting(key=key, value=str_value))
    db.commit()
    return payload


# ---------------------------------------------------------------------------
# Audit trail
# ---------------------------------------------------------------------------

@app.get("/api/audit-log", response_model=list[schemas.AuditEntry])
def audit_log(
    record_id: Optional[str] = None, limit: int = 100,
    db: Session = Depends(get_db), current: models.User = Depends(get_current_user),
):
    query = db.query(models.AuditLog)
    if record_id:
        query = query.filter(models.AuditLog.record_id == record_id)
    return query.order_by(models.AuditLog.timestamp.desc()).limit(limit).all()
