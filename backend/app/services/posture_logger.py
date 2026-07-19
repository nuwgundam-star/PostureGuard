"""PostureFrame 시계열 적재 (AI 학습 기반).

❗실시간 경로 무변경 원칙:
  - PostureFrame 을 '읽어서 기록만' 한다 (부수효과 0, 산식/risk/schemas 무변경).
  - 적재는 fire-and-forget: 실시간 broadcast 를 절대 블로킹하지 않고, 실패해도 WS 가 죽지 않는다.
  - 다운샘플: settings.posture_log_interval_seconds (기본 1초/행).
  - 영상·이미지 저장 금지 — 좌표·수치만.
  - is_demo=True (시연/더미) 프레임은 학습 제외용 플래그로 구분 기록.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from app.core.config import settings
from app.core.database import AsyncSessionLocal, LogSession, PostureLog
from app.models.schemas import PostureFrame

logger = logging.getLogger(__name__)


class PostureLogger:
    def __init__(self) -> None:
        self._session_id: int | None = None
        self._last_logged: float = 0.0  # time.monotonic 기준 마지막 적재 시각
        self._warned: bool = False  # 실패 로그 1회만

    async def start_session(self, note: str | None = None) -> None:
        # 스트림 시작 시 세션 1행 생성. DB 미가용이어도 서버가 죽지 않게 흡수.
        try:
            async with AsyncSessionLocal() as db:
                session = LogSession(note=note)
                db.add(session)
                await db.commit()
                await db.refresh(session)
                self._session_id = session.session_id
            self._last_logged = 0.0
            self._warned = False
            logger.info("시계열 세션 시작 (session_id=%s)", self._session_id)
        except Exception as exc:
            self._session_id = None
            logger.warning("시계열 세션 생성 실패 - 적재 비활성: %s", exc)

    async def end_session(self) -> None:
        # 스트림 종료 시 ended_at 기록. 실패해도 흡수.
        if self._session_id is None:
            return
        sid = self._session_id
        self._session_id = None
        try:
            async with AsyncSessionLocal() as db:
                session = await db.get(LogSession, sid)
                if session is not None and session.ended_at is None:
                    session.ended_at = datetime.now(timezone.utc)
                    await db.commit()
            logger.info("시계열 세션 종료 (session_id=%s)", sid)
        except Exception as exc:
            logger.warning("시계열 세션 종료 기록 실패: %s", exc)

    def maybe_log(self, frame: PostureFrame, is_demo: bool) -> None:
        # 실시간 stream loop 에서 호출. 다운샘플 통과 시에만 fire-and-forget 적재.
        # ❗동기 메서드 + create_task: broadcast 경로를 블로킹하지 않는다.
        if self._session_id is None:
            return
        now = time.monotonic()
        if now - self._last_logged < settings.posture_log_interval_seconds:
            return
        self._last_logged = now
        row = self._row_from_frame(frame, is_demo)
        # 실패는 _write 내부에서 흡수 -> "exception never retrieved" 경고 없음.
        asyncio.create_task(self._write(row))

    def _row_from_frame(self, frame: PostureFrame, is_demo: bool) -> dict:
        # PostureFrame 에서 좌표·수치만 추출 (영상/이미지 없음).
        sk = frame.skeleton
        return {
            "session_id": self._session_id,
            "ts": frame.timestamp,
            "neck_score": float(sk.neck_score),
            "z2": float(sk.z2),
            "shoulder_slope": float(sk.shoulder_slope),
            "trunk_tilt": float(sk.trunk_tilt),
            "calibrated": bool(sk.calibrated),
            "tracking_ok": bool(sk.tracking_ok),
            # 신규 종합 진단 지표 (None 허용 — 저신뢰 프레임).
            "head_tilt": None if sk.head_tilt is None else float(sk.head_tilt),
            "shoulder_asym": None if sk.shoulder_asym is None else float(sk.shoulder_asym),
            "posture_state": sk.posture_state,
            "cop_x": float(frame.cop.cop_x),
            "cop_y": float(frame.cop.cop_y),
            "fatigue": float(frame.fft.fatigue_score),
            "risk_score": float(frame.risk.score),
            "risk_level": int(frame.risk.level),
            "is_demo": bool(is_demo),
        }

    async def _write(self, row: dict) -> None:
        # 한 행 적재. 실패해도 조용히 흡수 (실시간 경로 보호).
        try:
            async with AsyncSessionLocal() as db:
                db.add(PostureLog(**row))
                await db.commit()
        except Exception as exc:
            if not self._warned:
                logger.warning("posture_log 적재 실패 (이후 동일 경고 생략): %s", exc)
                self._warned = True
