"""Central configuration for the BHOOMI-AI backend.

Everything here can be overridden with environment variables or a `.env`
file (copy `.env.example` to `.env` to get started). Nothing sensitive is
hard-coded except safe local-dev defaults.
"""
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Security ---
    SECRET_KEY: str = "dev-only-secret-change-me"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 480

    # --- Database ---
    DATABASE_URL: str = f"sqlite:///{BASE_DIR / 'data' / 'bhoominex.db'}"

    # --- CORS ---
    ALLOWED_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:5500,http://127.0.0.1:5500,null"

    # --- File storage ---
    UPLOAD_DIR: Path = BASE_DIR / "data" / "uploads"
    MAX_UPLOAD_SIZE_MB: int = 20

    # --- OCR ---
    TESSERACT_CMD: str | None = None
    POPPLER_PATH: str | None = None
    # Tesseract language pack(s), "+"-joined. "eng+hin" reads mixed English/
    # Hindi land records (Devanagari script) — requires the tesseract-ocr-hin
    # system package (see README_BACKEND.md). Falls back to "eng" only if
    # the hin traineddata isn't installed (Tesseract raises, pipeline catches it).
    OCR_LANGUAGES: str = "eng+hin"

    @property
    def allowed_origins_list(self) -> list[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]


settings = Settings()
settings.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
(BASE_DIR / "data").mkdir(parents=True, exist_ok=True)
