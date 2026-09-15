# BHOOMI-AI Backend (SIH26018)

A real FastAPI backend implementing the pitch-deck pipeline:

```
INPUT → PREPROCESSING → AI UNDERSTANDING (OCR) → VALIDATION ENGINE
      → CONFIDENCE ENGINE → HUMAN VERIFICATION → OUTPUT (trusted record)
```

The existing `index.html` dashboard was **not modified** — it still runs
standalone with its own in-memory/localStorage data. This backend is a
separate, complete REST API sitting next to it, ready to be wired in
whenever you want the UI to talk to a real server instead of
`localStorage`.

## What it actually does (not a mock)

- **Real OCR** via Tesseract (`pytesseract`) on uploaded images, with a
  preprocessing step (grayscale, contrast normalization, upscaling small
  scans) and a document-quality score.
- **Real text extraction** for text-based PDFs (via `pypdf`), OCR fallback
  for scanned PDFs (via `pdf2image` + Tesseract).
- **Rule-based field extraction** (owner, khasra, khata, village, tehsil,
  district, area, mutation number) using label/keyword matching in English
  and Hindi — swappable later for a trained NER model without touching
  anything else.
- **Validation engine**: required-field checks, khasra/area format checks,
  and duplicate/conflict detection against records already in the
  database (same khasra + village, different owner → flagged).
- **Confidence engine**: per-field and overall confidence (0–100),
  combining OCR confidence with how cleanly a label/value pair was found.
- **Human verification**: records below the confidence threshold, or with
  a conflict, are marked `Needs Review`; officers finalize them through
  `PUT /api/records/{id}/verify`, which writes an audit trail entry.
- **Persistence**: SQLite by default (zero config, one file), swappable
  for PostgreSQL by changing one environment variable.
- **Auth**: JWT-based login, seeded with the same `admin` / `demo123`
  credentials the login screen pre-fills.

## System requirements

Besides Python 3.10+, the OCR pipeline needs two system packages, plus the
Hindi language pack for Devanagari-script land records:

```bash
# Ubuntu / Debian
sudo apt-get install tesseract-ocr tesseract-ocr-hin poppler-utils

# macOS (Homebrew) — the core tesseract formula ships all language packs
brew install tesseract poppler

# Windows
# Tesseract: https://github.com/UB-Mannheim/tesseract/wiki
#   (the installer has a language-selection screen — tick "Hindi")
# Poppler:   https://github.com/oschwartz10612/poppler-windows/releases
# then set TESSERACT_CMD / POPPLER_PATH in .env (see .env.example)
```

Text-based PDFs work without Poppler (they use the embedded text layer).
Poppler is only needed to OCR **scanned/image PDFs**.

If `tesseract-ocr-hin` isn't installed, OCR silently falls back to
English-only instead of failing the upload (see `pipeline.ocr_document`) —
Hindi label matching (खसरा, मालिक, गांव, जिला, …) in `pipeline.py` still
works either way since that runs on plain text, but recognition accuracy on
Devanagari scans will be poor until the language pack is installed.
Language pack(s) used are controlled by `OCR_LANGUAGES` in `.env` (default
`eng+hin`).

## Run it

```bash
cd backend
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env             # then edit SECRET_KEY at minimum
uvicorn main:app --reload --port 8000
```

- Interactive API docs: http://127.0.0.1:8000/docs
- On first run the SQLite DB is created at `backend/data/bhoominex.db`
  and seeded with the same 5 demo records and 3 users the static
  dashboard ships with, so `/api/dashboard` and `/api/records` look
  identical to the localStorage version out of the box.

Seeded logins:

| Username        | Password      | Role          |
|------------------|--------------|---------------|
| `admin`          | `demo123`    | Administrator |
| `priya.sharma`   | `verifier123`| Verifier      |
| `rahul.kumar`    | `operator123`| Operator      |

Try the pipeline without a real scan:

```bash
python scripts/make_sample_document.py   # writes scripts/sample_record.png
```
then upload that file through `/docs` → `POST /api/upload`.

## API → UI action map

| UI action (index.html)                     | Endpoint                                  |
|----------------------------------------------|--------------------------------------------|
| Login form submit                             | `POST /api/auth/login`                      |
| Dashboard stat cards + "Recent Uploads" table | `GET /api/dashboard`                        |
| "Records by State" donut                      | `GET /api/dashboard` (`by_state`)           |
| "+ Upload" / "Upload Record" button            | `POST /api/upload` (multipart: `file`, `state`) |
| "Validation Result" screen                    | response of `POST /api/upload`              |
| "All Records" table + filter box              | `GET /api/records?q=`                       |
| "Search Land Records"                         | `GET /api/records?q=`                       |
| Record "View" modal                           | `GET /api/records/{id}`                     |
| Side-by-side human verification screen        | `GET /api/records/{id}` + `GET /api/records/{id}/file` (source scan) + `PUT /api/records/{id}/verify` |
| Officer correcting + approving a record       | `PUT /api/records/{id}/verify`              |
| Analytics tab bars/rates                      | `GET /api/analytics`                        |
| Map View pins                                 | `GET /api/map` (per-record; see GIS section below) |
| Record modal → "Import geometry"              | `POST /api/records/{id}/geometry` (multipart `file`), `DELETE /api/records/{id}/geometry` |
| Users tab                                     | `GET /api/users`, `POST /api/users`, `DELETE /api/users/{id}` |
| Settings toggles                              | `GET /api/settings`, `PUT /api/settings`    |
| Record audit history                          | `GET /api/audit-log?record_id=`             |

All endpoints except `/health` and `/api/auth/login` require
`Authorization: Bearer <token>` from the login response.

## GIS: per-record geometry

`GET /api/map` now returns one feature per record instead of a per-state
count. Each feature carries `latitude`/`longitude` and, if a boundary was
imported, a `geometry` (GeoJSON Polygon/MultiPolygon). Records with no
import still come back (with `latitude`/`longitude` null) — `index.html`
falls back to an approximate state centroid for those and marks them
visually as approximate, so nothing silently disappears from the map.

To attach real geometry to a record:

```
POST /api/records/{id}/geometry
Content-Type: multipart/form-data
file: <either>
  - a .geojson / .json file (bare geometry, a Feature, or a
    FeatureCollection — the first feature is used), assumed WGS84 per the
    GeoJSON spec, or
  - a .zip containing a shapefile (.shp required; .dbf/.shx used if
    present; .prj used to detect the source CRS and reproject to WGS84
    with pyproj — if there's no .prj, the coordinates are assumed to
    already be WGS84)
```

The backend derives a centroid (stored as `latitude`/`longitude`) and
stores the full geometry as GeoJSON, both normalized to EPSG:4326. Errors
(bad zip, missing `.shp`, an unparseable `.prj`, an out-of-range centroid
signalling an undetected projected CRS) come back as `400` with a message
safe to show directly. `DELETE /api/records/{id}/geometry` clears it back
to the approximate fallback. Both actions are exposed from the record
"View" modal in `index.html` and write an `audit_log` entry
(`geometry_imported` / `geometry_removed`).

New columns on `records`: `latitude`, `longitude`, `geometry_geojson`,
`geometry_source` (`shapefile` | `geojson` | `seed`), `geometry_crs_note`,
`geometry_filename`, `geometry_imported_at`. `seed_data.bootstrap()`
adds these columns to an existing SQLite `records` table automatically
(`ALTER TABLE ... ADD COLUMN`) so a pre-existing demo DB keeps working —
for PostgreSQL in production, use a real migration tool (Alembic) instead.

Dependencies added for this: `pyshp` (shapefile reading), `shapely`
(geometry validation/centroid/union), `pyproj` (CRS reprojection) — see
`requirements.txt`.

## Using PostgreSQL instead of SQLite

The app already reads its database driver entirely from `DATABASE_URL`, so
no code changes are needed — just point it at Postgres:

```bash
cd backend
docker compose up -d                # starts a local Postgres (see docker-compose.yml)
cp .env.example .env
# in .env: comment out the sqlite DATABASE_URL line and uncomment the
# postgresql+psycopg2://bhoominex:bhoominex@localhost:5432/bhoominex line
uvicorn main:app --reload --port 8000
```

Tables and demo seed data are created automatically on startup either way
(`seed_data.bootstrap()`), so `/api/dashboard` and `/api/records` look the
same on Postgres as they did on SQLite.

## Next steps for production

- Run schema changes through a proper migration tool (Alembic) instead of
  `create_all` once the schema needs to evolve after go-live.
- Put uploaded files in object storage (S3/GCS) rather than local disk.
- Replace the regex-based extractor in `pipeline.py` with a trained
  layout-aware NER model (e.g., LayoutLM) once you have labelled data —
  the surrounding validation/confidence/verification flow doesn't need
  to change.
- Add role-based field-level access control and encryption at rest for
  the "sensitive information" row from the feasibility slide.
- Rotate `SECRET_KEY` and put it in a real secrets manager.
