"""Pydantic schemas for request bodies and API responses."""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------- Auth ----------

class LoginRequest(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    name: str
    role: str
    department: str
    status: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class UserCreate(BaseModel):
    username: str
    password: str = Field(min_length=4)
    name: str
    role: str = "Operator"
    department: str = "Land Records"


# ---------- Records ----------

class RecordRow(BaseModel):
    """Shape the dashboard / records table expects: name, state, date, status."""
    id: str
    name: str
    state: str
    date: str
    status: str
    confidence: int


class FieldConfidence(BaseModel):
    owner: Optional[int] = None
    khasra: Optional[int] = None
    khata: Optional[int] = None
    village: Optional[int] = None
    tehsil: Optional[int] = None
    district: Optional[int] = None
    area: Optional[int] = None
    mutation_no: Optional[int] = None


class RecordDetail(BaseModel):
    """Shape the "Validation Result" view expects, plus extras."""
    id: str
    state: str
    district: Optional[str] = None
    village: Optional[str] = None
    tehsil: Optional[str] = None
    khasra: Optional[str] = None
    khata: Optional[str] = None
    owner: Optional[str] = None
    area: Optional[str] = None
    mutation_no: Optional[str] = None
    status: str
    confidence: int
    field_confidence: FieldConfidence
    validation_notes: list[str] = []
    file_name: str
    uploaded_on: datetime
    updated_at: datetime
    verified_by: Optional[str] = None
    verified_at: Optional[datetime] = None

    # --- GIS ---
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    geometry: Optional[dict] = None          # GeoJSON geometry, WGS84, when imported
    geometry_source: Optional[str] = None     # "shapefile" | "geojson" | "seed" | None
    geometry_crs_note: Optional[str] = None
    geometry_filename: Optional[str] = None
    geometry_imported_at: Optional[datetime] = None


class VerifyRequest(BaseModel):
    owner: str
    khasra: str
    khata: Optional[str] = None
    village: str
    tehsil: Optional[str] = None
    district: str
    area: str
    mutation_no: Optional[str] = None
    status: str = "Verified"  # officer can also send back "Needs Review" with a note


class UploadMeta(BaseModel):
    """Optional form fields alongside the uploaded file."""
    state: Optional[str] = None
    status: Optional[str] = None  # manual override, otherwise pipeline decides


class UploadResponse(BaseModel):
    record: RecordDetail
    message: str


# ---------- Dashboard / analytics ----------

class DashboardStats(BaseModel):
    total: int
    processed: int
    verified: int
    needs_review: int
    by_state: dict[str, int]
    recent: list[RecordRow]
    pipeline: list[dict]


class AnalyticsResponse(BaseModel):
    verify_rate: int
    process_rate: int
    pending_review: int
    total: int


class MapFeature(BaseModel):
    """One record plotted on the Map View. `latitude`/`longitude` are the
    real, per-record centroid whenever geometry has been imported;
    otherwise they're null and the frontend falls back to an approximate
    state-centroid marker (see STATE_COORDS in index.html)."""
    id: str
    name: str
    state: str
    district: Optional[str] = None
    village: Optional[str] = None
    owner: Optional[str] = None
    khasra: Optional[str] = None
    status: str
    confidence: int
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    geometry: Optional[dict] = None           # GeoJSON geometry (Polygon/MultiPolygon), when imported


class GeometryImportResponse(BaseModel):
    id: str
    latitude: float
    longitude: float
    geometry: dict
    geometry_source: str
    crs_note: str
    message: str


# ---------- Settings ----------

class SettingsPayload(BaseModel):
    email_notifications: bool = True
    auto_validation: bool = True
    compact_dashboard: bool = False


# ---------- Audit ----------

class AuditEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    record_id: str
    action: str
    actor: str
    details: Optional[str] = None
    timestamp: datetime
