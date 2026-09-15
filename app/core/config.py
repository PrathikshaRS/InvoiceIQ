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
    ocr_dpi: int = 150
    tesseract_cmd: str = ""

    #MongoDB Settings
    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_db_name: str = "invoiceiq"
    mongodb_collection_name: str = "invoices"

    #MS SQL Server Settings
    sqlserver_host: str = "localhost"
    sqlserver_port: int = 1433
    sqlserver_database: str = "invoiceiq"
    sqlserver_username: str = "sa"
    sqlserver_password: str = ""
    sqlserver_driver: str = "ODBC Driver 18 for SQL Server"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()