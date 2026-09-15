# BHOOMI-AI · BhoomiNex

SIH 2026 prototype for **SIH26018 — Intelligent Land Record Digitization and Validation System**.

The application is intentionally split by responsibility:

```text
frontend/  React + Vite officer dashboard
backend/   FastAPI REST API (mock data for the MVP)
```

## Product flow

1. Securely receive a scanned land document.
2. Preprocess it, then run OCR, layout understanding and entity extraction.
3. Validate formats, business rules, duplicates and conflicts.
4. Score confidence at field level.
5. Send only uncertain fields to a human verifier.
6. Save the approved record with an audit event, ready for PostgreSQL, API and GIS integration.

## Run locally

### Backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

API docs: `http://127.0.0.1:8000/docs`

### Frontend

```powershell
cd frontend
npm install
npm run dev
```

Open `http://127.0.0.1:5173`.

The backend now runs the full SIH26018 pipeline for real: Tesseract OCR,
rule-based field extraction, a validation + confidence engine, JWT auth,
and a SQLite database (seeded with the same demo records/users the
dashboard ships with). See `backend/README_BACKEND.md` for setup, system
requirements (Tesseract + Poppler), and a table mapping every UI action to
its API endpoint. `index.html` and `frontend/` were left exactly as they
were — this is a drop-in API layer, not a UI change.

For production: point `DATABASE_URL` at PostgreSQL, move uploaded files to
object storage, and swap the regex-based extractor for a trained
layout-aware NER model once labelled data is available.
