from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.rest import router as rest_router
from app.api.websocket import manager, router as websocket_router
from app.core.config import settings
from app.core.database import init_db
from app.services.mqtt_subscriber import mqtt_subscriber

# app.* 로거의 INFO 진단 로그(MQTT 프레임 누적 카운터 등)를 콘솔로 보내기 위한 핸들러 설정.
# uvicorn 의 자체 로거(uvicorn.*) 와 분리해, propagate=False 로 중복 출력을 막는다.
_app_logger = logging.getLogger("app")
if not _app_logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(levelname)s [%(name)s] %(message)s"))
    _app_logger.addHandler(_handler)
    _app_logger.setLevel(logging.INFO)
    _app_logger.propagate = False

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 앱 시작 시 DB 초기화 및 센서/카메라 파이프라인 준비
    # 하드웨어/DB 미준비 상태에서도 더미 모드로 서버가 떠야 하므로 실패는 모두 흡수
    try:
        await init_db()
    except Exception as exc:
        logger.warning("DB 초기화 실패 - 더미 모드로 기동합니다: %s", exc)
    try:
        await manager._serial_reader.connect()  # noqa: SLF001
    except Exception as exc:
        logger.warning("시리얼 리더 연결 실패 - 더미 모드로 기동합니다: %s", exc)
    try:
        await manager._mediapipe_service.start()  # noqa: SLF001
    except Exception as exc:
        logger.warning("MediaPipe 서비스 시작 실패 - 더미 모드로 기동합니다: %s", exc)
    try:
        await mqtt_subscriber.connect(manager)
    except Exception as exc:
        logger.warning("MQTT 브로커 연결 실패 - 더미 모드로 기동합니다: %s", exc)
    try:
        yield
    finally:
        # 앱 종료 시 리소스를 안전하게 정리
        try:
            await mqtt_subscriber.disconnect()
        except Exception as exc:
            logger.warning("MQTT 종료 중 오류: %s", exc)
        try:
            await manager._serial_reader.disconnect()  # noqa: SLF001
        except Exception as exc:
            logger.warning("시리얼 리더 종료 중 오류: %s", exc)
        try:
            await manager._mediapipe_service.stop()  # noqa: SLF001
        except Exception as exc:
            logger.warning("MediaPipe 서비스 종료 중 오류: %s", exc)


def create_app() -> FastAPI:
    # 백엔드 FastAPI 앱 생성
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        description="PostureGuard backend API",
        lifespan=lifespan,
    )

    # 프론트 개발 서버(localhost:3000) 접근 허용
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(rest_router, prefix="/api")
    app.include_router(websocket_router, prefix="/ws")

    @app.get("/health")
    async def health() -> dict[str, str]:
        # 서버 헬스 체크 엔드포인트
        return {"status": "ok", "app": settings.app_name}

    return app


app = create_app()
