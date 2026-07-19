"""거북목 캘리브레이션 모듈.

CalibrationStore: z2_good / z2_bad / z2_std 를 JSON 으로 영속화.
CalibrationCollector: 매 frame z2/품질을 수집하고 통계·품질가드 적용.
"""

from __future__ import annotations

import asyncio
import json
import logging
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

# 캘리브레이션 데이터 경로 (backend/data/calibration.json). .gitignore 대상.
_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_CALIB_PATH = _DATA_DIR / "calibration.json"

# 품질 가드 임계값.
MIN_AVG_VIS = 0.5           # 평균 visibility 가 이 이하면 frame 거부
MAX_SHOULDER_SLOPE = 0.15   # 어깨 비수평 과대 frame 거부
MIN_VALID_FRAMES = 12       # 실제 fps (~3 @ FullHD complexity=2) 기준 5~7초 수집 보장


class CalibrationStore:
    """JSON 영속. 두 z2 값과 std 를 보관."""

    def __init__(self) -> None:
        self.z2_good: float | None = None
        self.z2_bad: float | None = None
        self.z2_std: float | None = None
        self.created_at: datetime | None = None
        self.load()

    @property
    def calibrated(self) -> bool:
        return self.z2_good is not None and self.z2_bad is not None

    @property
    def span(self) -> float:
        # 거북목 신호의 동적 범위 (정자세 - 거북목).
        if not self.calibrated:
            return 0.0
        return float(self.z2_good - self.z2_bad)  # type: ignore[operator]

    def load(self) -> None:
        if not _CALIB_PATH.exists():
            return
        try:
            with open(_CALIB_PATH, encoding="utf-8") as f:
                data = json.load(f)
            self.z2_good = float(data["z2_good"])
            self.z2_bad = float(data["z2_bad"])
            self.z2_std = float(data.get("z2_std", 0.0))
            created = data.get("created_at")
            self.created_at = datetime.fromisoformat(created) if created else None
        except Exception as exc:
            logger.warning("calibration.json 로드 실패: %s", exc)

    def save(self) -> None:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "z2_good": self.z2_good,
            "z2_bad": self.z2_bad,
            "z2_std": self.z2_std,
            "created_at": (self.created_at or datetime.now(timezone.utc)).isoformat(),
        }
        with open(_CALIB_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    def set_pose(self, pose: Literal["good", "bad"], value: float, std: float) -> None:
        # 검증 (z2_bad < z2_good) 은 commit 단계에서 수행. 여기는 보관만.
        if pose == "good":
            self.z2_good = value
            # z2_std 는 정자세 분산만 추적 (점수화의 노이즈 기준).
            self.z2_std = std
        else:
            self.z2_bad = value
        self.created_at = datetime.now(timezone.utc)

    def status(self) -> dict:
        return {
            "calibrated": self.calibrated,
            "z2_good": self.z2_good,
            "z2_bad": self.z2_bad,
            "z2_std": self.z2_std,
            "span": self.span if self.calibrated else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class _PendingSession:
    """진행 중인 단일 포즈 수집 상태."""

    def __init__(self, pose: Literal["good", "bad"], duration: float) -> None:
        self.pose = pose
        self.duration = duration
        self.started_at = time.monotonic()
        self.accepted: list[float] = []
        self.rejected_vis = 0
        self.rejected_dummy = 0
        self.rejected_slope = 0
        self.vis_sum = 0.0
        self.vis_n = 0

    def add_frame(self, z2: float, avg_vis: float, dummy: bool, sh_slope: float) -> None:
        if dummy:
            self.rejected_dummy += 1
            return
        if avg_vis < MIN_AVG_VIS:
            self.rejected_vis += 1
            return
        if abs(sh_slope) > MAX_SHOULDER_SLOPE:
            self.rejected_slope += 1
            return
        self.accepted.append(z2)
        self.vis_sum += avg_vis
        self.vis_n += 1

    def is_due(self) -> bool:
        return (time.monotonic() - self.started_at) >= self.duration


class CalibrationCollector:
    """능동 수집 상태 관리. websocket frame 빌더가 add_frame 으로 데이터를 흘려넣는다."""

    def __init__(self) -> None:
        self._pending: _PendingSession | None = None
        self._done_event: asyncio.Event | None = None

    @property
    def status_label(self) -> str:
        if self._pending is None:
            return "idle"
        return f"collecting_{self._pending.pose}"

    def is_collecting(self) -> bool:
        return self._pending is not None

    def add_frame(self, z2: float, avg_vis: float, dummy: bool, sh_slope: float) -> None:
        if self._pending is None:
            return
        self._pending.add_frame(z2, avg_vis, dummy, sh_slope)
        if self._pending.is_due() and self._done_event is not None:
            self._done_event.set()

    async def collect(self, pose: Literal["good", "bad"], duration: float) -> dict:
        """duration 초 동안 frame 을 수집하고 통계·품질 결과를 반환한다.

        반환 dict 의 "ok" 가 False 면 호출자는 422 등으로 응답해야 한다.
        """
        if self._pending is not None:
            return {"ok": False, "reason": "another calibration in progress"}

        self._pending = _PendingSession(pose, duration)
        self._done_event = asyncio.Event()

        try:
            # duration 보다 약간 여유 (frame rate 가 낮으면 늦게 done 됨).
            try:
                await asyncio.wait_for(self._done_event.wait(), timeout=duration + 5.0)
            except asyncio.TimeoutError:
                pass
            session = self._pending
        finally:
            self._pending = None
            self._done_event = None

        return _build_result(session)


def _build_result(session: _PendingSession) -> dict:
    n = len(session.accepted)
    if n < MIN_VALID_FRAMES:
        return {
            "ok": False,
            "reason": f"insufficient valid frames ({n} < {MIN_VALID_FRAMES})",
            "frames_accepted": n,
            "rejected_vis": session.rejected_vis,
            "rejected_dummy": session.rejected_dummy,
            "rejected_slope": session.rejected_slope,
        }
    z2_mean = statistics.mean(session.accepted)
    z2_std = statistics.pstdev(session.accepted) if n > 1 else 0.0
    avg_vis = (session.vis_sum / session.vis_n) if session.vis_n > 0 else 0.0
    return {
        "ok": True,
        "pose": session.pose,
        "frames_accepted": n,
        "z2_mean": z2_mean,
        "z2_std": z2_std,
        "avg_vis": avg_vis,
        "rejected_vis": session.rejected_vis,
        "rejected_dummy": session.rejected_dummy,
        "rejected_slope": session.rejected_slope,
    }


def commit_save(store: CalibrationStore) -> dict:
    """두 포즈 모두 수집됐고 z2_bad < z2_good 인 경우만 JSON 영속."""
    if store.z2_good is None or store.z2_bad is None:
        return {"ok": False, "reason": "both poses must be collected first"}
    if not (store.z2_bad < store.z2_good):
        return {
            "ok": False,
            "reason": "z2_bad must be smaller than z2_good (turtle neck should be closer to camera)",
            "z2_good": store.z2_good,
            "z2_bad": store.z2_bad,
        }
    store.save()
    return {"ok": True, **store.status()}
