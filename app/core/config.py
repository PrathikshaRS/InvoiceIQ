from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    #Initial Settings
    app_name: str = "InvoiceIQ"
    environment: str = "development"

    # Upload Settings
    upload_dir: str = "uploads"
    max_upload_size_mb: int = 10
    allowed_extensions: set[str] = {".pdf", ".png", ".jpg", ".jpeg"}

    #OCR Settings
    ocr_dpi: int = 300
    tesseract_cmd: str = ""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()