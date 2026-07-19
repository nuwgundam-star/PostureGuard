from __future__ import annotations

from datetime import datetime, timezone
from typing import AsyncGenerator

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.config import settings


class Base(DeclarativeBase):
    # SQLAlchemy 선언형 베이스 클래스
    pass


class PostureSession(Base):
    # 자세 측정 세션 메타 정보 테이블
    __tablename__ = "posture_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    total_warnings: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class PostureReading(Base):
    # 프레임 단위 자세 분석 결과 테이블
    __tablename__ = "posture_readings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    session_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("posture_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    cop_x: Mapped[float] = mapped_column(Float, nullable=False)
    cop_y: Mapped[float] = mapped_column(Float, nullable=False)
    neck_angle: Mapped[float] = mapped_column(Float, nullable=False)
    shoulder_slope: Mapped[float] = mapped_column(Float, nullable=False)
    risk_level: Mapped[int] = mapped_column(Integer, nullable=False)
    risk_score: Mapped[float] = mapped_column(Float, nullable=False)


class Calibration(Base):
    # 기준 자세(캘리브레이션) 데이터 테이블
    __tablename__ = "calibrations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    baseline_map: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class LogSession(Base):
    # AI 학습용 시계열 세션 (백엔드 스트림 가동 단위). 압력 상세는 FSR 도입 후 합류.
    __tablename__ = "session"

    session_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(String, nullable=True)


class PostureLog(Base):
    # 프레임 시계열 적재 (다운샘플 1초/행). 좌표·수치만 — 영상/이미지 저장 안 함.
    # is_demo: 시연/더미 프레임 구분(학습 시 제외용).
    __tablename__ = "posture_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("session.session_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    # 상체(스켈레톤)
    neck_score: Mapped[float] = mapped_column(Float, nullable=False)
    z2: Mapped[float] = mapped_column(Float, nullable=False)
    shoulder_slope: Mapped[float] = mapped_column(Float, nullable=False)
    trunk_tilt: Mapped[float] = mapped_column(Float, nullable=False)
    calibrated: Mapped[bool] = mapped_column(Boolean, nullable=False)
    tracking_ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # 종합 진단 신규 지표 (절대 0기준, 캘리브 없음). 기존 행 호환 위해 nullable.
    head_tilt: Mapped[float | None] = mapped_column(Float, nullable=True)
    shoulder_asym: Mapped[float | None] = mapped_column(Float, nullable=True)
    posture_state: Mapped[str | None] = mapped_column(String, nullable=True)
    # 하체(압력 요약) — FSR 후 상세 컬럼/테이블 확장
    cop_x: Mapped[float] = mapped_column(Float, nullable=False)
    cop_y: Mapped[float] = mapped_column(Float, nullable=False)
    fatigue: Mapped[float] = mapped_column(Float, nullable=False)
    # 종합
    risk_score: Mapped[float] = mapped_column(Float, nullable=False)
    risk_level: Mapped[int] = mapped_column(Integer, nullable=False)
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


# 비동기 엔진/세션 팩토리 구성
engine = create_async_engine(settings.database_url, echo=False, future=True)
AsyncSessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    # 애플리케이션 시작 시 테이블 자동 생성 + 경량 마이그레이션(신규 컬럼 ADD).
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _migrate_posture_log(conn)


# create_all 은 기존 테이블에 컬럼을 추가하지 않으므로, 기존 posture_log 에
# 신규 컬럼이 없으면 ALTER TABLE ADD COLUMN (nullable) 으로 보강한다. 기존 행은 보존.
_POSTURE_LOG_NEW_COLUMNS: dict[str, str] = {
    "head_tilt": "FLOAT",
    "shoulder_asym": "FLOAT",
    "posture_state": "VARCHAR",
}


async def _migrate_posture_log(conn) -> None:
    result = await conn.exec_driver_sql("PRAGMA table_info(posture_log)")
    existing = {row[1] for row in result.fetchall()}  # row[1] = column name
    if not existing:
        return  # 테이블 자체가 없으면(다른 DB 백엔드 등) 건드리지 않음
    for name, col_type in _POSTURE_LOG_NEW_COLUMNS.items():
        if name not in existing:
            await conn.exec_driver_sql(f"ALTER TABLE posture_log ADD COLUMN {name} {col_type}")


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    # FastAPI 의존성 주입용 비동기 세션 제공
    async with AsyncSessionLocal() as session:
        yield session
