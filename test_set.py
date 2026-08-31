from pydantic_settings import BaseSettings, SettingsConfigDict
class Settings(BaseSettings):
    DATABASE_URL: str = 'default'
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
s = Settings()
print(s.DATABASE_URL)
