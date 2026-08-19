try:
    from pydantic_settings import BaseSettings
except ImportError:
    from pydantic import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "Nurofin Executive AI"
    API_V1_STR: str = "/api/v1"
    SECRET_KEY: str = "a-very-secret-key-change-me-in-production"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 8 # 8 days
    
    POSTGRES_SERVER: str = "localhost"
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "qwerty"
    POSTGRES_DB: str = "nurofin_db"
    POSTGRES_PORT: str = "5432"
    
    GOOGLE_CLIENT_ID: str = "YOUR_GOOGLE_CLIENT_ID"
    GOOGLE_CLIENT_SECRET: str = "YOUR_GOOGLE_CLIENT_SECRET"
    GOOGLE_PROJECT_ID: str = "YOUR_PROJECT_ID"
    
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_REGION: str = "us-east-1"
    AWS_S3_BUCKET_NAME: str = ""
    
    DATABASE_URL: str | None = None
    
    @property
    def SQLALCHEMY_DATABASE_URI(self) -> str:
        import os

        # Priority 1: DATABASE_URL (Render, Heroku, managed databases)
        database_url = (self.DATABASE_URL or "").strip()
        if database_url:
            if database_url.startswith("postgres://"):
                database_url = "postgresql+psycopg://" + database_url[len("postgres://"):]
            elif database_url.startswith("postgresql://"):
                database_url = "postgresql+psycopg://" + database_url[len("postgresql://"):]
            return database_url

        # Priority 2: USE_SQLITE (local development / demo mode)
        if os.getenv("USE_SQLITE", "").lower() in ("true", "1"):
            return "sqlite+aiosqlite:///./nurofin_db.db"

        # Priority 3: POSTGRES_* environment variables
        if self.POSTGRES_SERVER and self.POSTGRES_USER and self.POSTGRES_PASSWORD and self.POSTGRES_DB and self.POSTGRES_PORT:
            return f"postgresql+psycopg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_SERVER}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

        # Priority 4: No database configuration found
        raise ValueError(
            "No database configuration found. Set one of: "
            "DATABASE_URL (preferred), USE_SQLITE=true, "
            "or POSTGRES_SERVER/POSTGRES_PORT/POSTGRES_USER/POSTGRES_PASSWORD/POSTGRES_DB."
        )

    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "ignore"

settings = Settings()

