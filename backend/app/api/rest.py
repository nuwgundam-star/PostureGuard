from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException

from app.api.websocket import manager
from app.models.schemas import CalibrationRequest
from app.services.calibration_service import commit_save

router = APIRouter()

# 메모리 기반 임시 저장소(이후 DB 연동 Step에서 교체 예정)
_current_session: dict[str, Any] | None = None
_session_history: list[dict[str, Any]] = []
_calibration_store: dict[str, Any] | None = None


@router.post("/calibrate")
async def calibrate_posture(payload: CalibrationRequest) -> dict[str, Any]:
    # 기준 자세 등록 요청을 저장한다.
    global _calibration_store
    _calibration_store = {
        "user_id": payload.user_id,
        "duration_seconds": payload.duration_seconds,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    return {"ok": True, "calibration": _calibration_store}


@router.get("/history")
async def get_history() -> dict[str, list[dict[str, Any]]]:
    # 최근 세션 이력을 최신순으로 반환한다.
    return {"sessions": list(reversed(_session_history[-50:]))}


@router.get("/status")
async def get_status() -> dict[str, Any]:
    # 현재 장치 및 웹소켓 연결 상태를 반환한다.
    serial_connected = bool(manager._serial_reader._serial and manager._serial_reader._serial.is_open)  # noqa: SLF001
    camera_connected = not manager._mediapipe_service.is_dummy_mode  # noqa: SLF001
    return {
        "serial": {
            "connected": serial_connected,
            "dummy_mode": manager._serial_reader.is_dummy_mode,  # noqa: SLF001
        },
        "camera": {
            "connected": camera_connected,
            "dummy_mode": manager._mediapipe_service.is_dummy_mode,  # noqa: SLF001
        },
        "ws_clients": len(manager.active_connections),
    }


@router.post("/session/start")
async def start_session() -> dict[str, Any]:
    # 새 측정 세션을 시작한다.
    global _current_session
    if _current_session is not None:
        return {"ok": False, "message": "session already started", "session": _current_session}

    _current_session = {
        "session_id": str(uuid4()),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "ended_at": None,
        "total_warnings": 0,
    }
    return {"ok": True, "session": _current_session}


@router.post("/calibration/collect/{pose}")
async def calibration_collect(pose: Literal["good", "bad"], duration: float = 5.0) -> dict[str, Any]:
    """duration 초 동안 z2 를 수집하고 통계 + 품질 메트릭 반환.

    품질 부족 (vis<0.5 다수 / dummy / 어깨 비수평) 시 422 로 거부.
    성공 시 store 에 in-memory 로 보관 (save 호출 전까지 영속 안 함).
    """
    if duration <= 0 or duration > 30:
        raise HTTPException(status_code=400, detail="duration must be in (0, 30]")
    if not manager.active_connections:
        # stream loop 가 안 돌면 collector 에 frame 이 안 들어와서 timeout.
        raise HTTPException(status_code=409, detail="ws stream not active; connect a client first")

    result = await manager._calibration_collector.collect(pose, duration)  # noqa: SLF001
    if not result.get("ok"):
        raise HTTPException(status_code=422, detail=result)

    manager._calibration_store.set_pose(  # noqa: SLF001
        pose, value=float(result["z2_mean"]), std=float(result["z2_std"])
    )
    return result


@router.post("/calibration/save")
async def calibration_save() -> dict[str, Any]:
    """두 포즈 모두 수집됐고 z2_bad < z2_good 인 경우만 JSON 영속."""
    result = commit_save(manager._calibration_store)  # noqa: SLF001
    if not result.get("ok"):
        raise HTTPException(status_code=422, detail=result)
    # 영속 직후 forward_head 누적 EMA 초기화 (새 baseline 기준).
    manager._forward_head.reset()  # noqa: SLF001
    return result


@router.get("/calibration/status")
async def calibration_status() -> dict[str, Any]:
    return {
        **manager._calibration_store.status(),  # noqa: SLF001
        "collector": manager._calibration_collector.status_label,  # noqa: SLF001
    }


@router.post("/demo/scenario/{mode}")
async def demo_scenario(mode: Literal["good", "warning", "danger"]) -> dict[str, Any]:
    """단발 시연 시나리오 지정. 압력맵만 시나리오화하며 자세 점수는 미주입(정직성).

    danger 는 압력 기여 ~51 까지만 도달 -> level 2 는 발표자 거북목 자세 결합 필요.
    """
    manager._demo.set_mode(mode)  # noqa: SLF001
    return {"ok": True, "demo": manager._demo.status()}  # noqa: SLF001


@router.post("/demo/loop")
async def demo_loop(on: bool = True) -> dict[str, Any]:
    """자동 데모 루프 토글. 양호↔주의만 순회(무인이라 자세 결합 불가 -> 위험 제외)."""
    manager._demo.set_loop(on)  # noqa: SLF001
    return {"ok": True, "demo": manager._demo.status()}  # noqa: SLF001


@router.post("/demo/off")
async def demo_off() -> dict[str, Any]:
    """시연 종료 -> 실제 MQTT/serial 데이터 경로로 복귀."""
    manager._demo.off()  # noqa: SLF001
    return {"ok": True, "demo": manager._demo.status()}  # noqa: SLF001


@router.get("/demo/status")
async def demo_status() -> dict[str, Any]:
    return manager._demo.status()  # noqa: SLF001


@router.post("/session/end")
async def end_session() -> dict[str, Any]:
    # 진행 중 세션을 종료하고 이력에 저장한다.
    global _current_session
    if _current_session is None:
        return {"ok": False, "message": "no active session"}

    _current_session["ended_at"] = datetime.now(timezone.utc).isoformat()
    finished = dict(_current_session)
    _session_history.append(finished)
    _current_session = None
    return {"ok": True, "session": finished}
