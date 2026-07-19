from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # 애플리케이션 기본 이름
    app_name: str = Field(default="PostureGuard", alias="APP_NAME")
    # 비동기 DB 접속 URL. PostgreSQL 미설치 -> 로컬 SQLite(aiosqlite) 로 운용.
    # 추후 PG 이전 시 이 URL 만 교체 (예: postgresql+asyncpg://...).
    database_url: str = Field(
        default="sqlite+aiosqlite:///./postureguard.db",
        alias="DATABASE_URL",
    )
    # 시계열 적재 다운샘플 주기(초). 1초당 1행이 기본.
    posture_log_interval_seconds: float = Field(default=1.0, alias="POSTURE_LOG_INTERVAL_SECONDS")
    # 시리얼 포트 및 통신 속도 설정
    serial_port: str = Field(default="COM3", alias="SERIAL_PORT")
    baud_rate: int = Field(default=460800, alias="BAUD_RATE")
    # FSR 매트릭스 크기 설정
    fsr_rows: int = Field(default=16, alias="FSR_ROWS")
    fsr_cols: int = Field(default=16, alias="FSR_COLS")
    # 초당 샘플링 횟수
    sample_rate: int = Field(default=20, alias="SAMPLE_RATE")
    # MQTT 브로커 접속 정보
    mqtt_broker_host: str = Field(default="localhost", alias="MQTT_BROKER_HOST")
    mqtt_broker_port: int = Field(default=1883, alias="MQTT_BROKER_PORT")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        populate_by_name=True,
        extra="ignore",
    )


settings = Settings()
