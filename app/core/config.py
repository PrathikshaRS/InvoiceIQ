from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application configuration, loaded from environment variables (or a .env file).
    Using pydantic-settings means every value is validated and type-checked at startup —
    if something required is missing or malformed, the app fails fast with a clear error
    instead of crashing later mid-request.
    """
    app_name: str = "InvoiceIQ"
    environment: str = "development"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()