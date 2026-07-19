from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

import numpy as np
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.config import settings
from app.models.schemas import (
    AvatarLandmarks,
    AvatarPoint,
    CoPData,
    DemoState,
    FFTData,
    PostureFrame,
    PressureData,
    RiskData,
    SkeletonData,
)
from app.services.algorithms import analyze_fft, calculate_cop, calculate_risk_level, calculate_skeleton_metrics
from app.services.calibration_service import CalibrationCollector, CalibrationStore
from app.services.demo_scenario import DemoScenario
from app.services.forward_head import ForwardHeadProcessor
from app.services.posture_logger import PostureLogger
from app.services.posture_state import PostureStateProcessor
from app.services.mediapipe_service import MediaPipeService
from app.services.serial_reader import FSRSerialReader

router = APIRouter()
logger = logging.getLogger(__name__)


# MQTT 로 들어온 프레임이 stream loop 가 소비하기까지 신선하다고 간주할 한계 시간(초).
# 이보다 오래된 MQTT 데이터는 무시하고 시리얼 더미로 fallback 한다.
MQTT_FRESHNESS_SECONDS = 0.2

# FFT 분석 윈도 길이(초). cop_history 를 '최근 N초'(타임스탬프 기준)로 롤링한다.
# 프레임 수 기준(과거 sample_rate*30)은 실제 fps 가 낮으면 윈도가 wall-clock 으로
# 과도하게 길어져(예: 5fps -> 120초) 주파수축이 왜곡됐다. 시간 기준으로 고정.
FFT_WINDOW_SECONDS = 30.0
# 폭주 방어용 하드 캡(타임스탬프 prune 의 backstop). 고fps 에서도 충분.
_COP_HISTORY_HARD_CAP = 4000

# 사이드뷰 아바타용 7개 landmark 의 mediapipe 인덱스 매핑.
_AVATAR_IDX: dict[str, int] = {
    "nose": 0,
    "ear_l": 7,
    "ear_r": 8,
    "shoulder_l": 11,
    "shoulder_r": 12,
    "hip_l": 23,
    "hip_r": 24,
}
# 핵심 좌표(귀·어깨) visibility 가 이 임계 미만이면 그 프레임 landmarks=None.
_AVATAR_KEY_VIS_KEYS = ("ear_l", "ear_r", "shoulder_l", "shoulder_r")
_AVATAR_MIN_VIS = 0.5


def _build_avatar_landmarks(
    landmarks: list[dict[str, float]] | None,
    is_dummy_mode: bool,
) -> AvatarLandmarks | None:
    # 산식 함수와 별도 패스: 7개만 추출 + 신뢰도 게이트.
    if landmarks is None or is_dummy_mode or len(landmarks) < 25:
        return None
    extracted: dict[str, AvatarPoint] = {}
    for name, idx in _AVATAR_IDX.items():
        lm = landmarks[idx]
        extracted[name] = AvatarPoint(
            x=float(lm["x"]),
            y=float(lm["y"]),
            visibility=float(lm["visibility"]),
        )
    # 귀·어깨 평균 vis 가 임계 미만이면 None (튀는 아바타 방지).
    key_vis = sum(extracted[k].visibility for k in _AVATAR_KEY_VIS_KEYS) / len(_AVATAR_KEY_VIS_KEYS)
    if key_vis < _AVATAR_MIN_VIS:
        return None
    return AvatarLandmarks(**extracted)


class ConnectionManager:
    def __init__(self) -> None:
        # 다중 클라이언트 연결 상태와 스트리밍 작업을 관리한다.
        self.active_connections: set[WebSocket] = set()
        self._stream_task: asyncio.Task[None] | None = None
        self._serial_reader = FSRSerialReader()
        self._mediapipe_service = MediaPipeService()
        # (timestamp, cop_x, cop_y) 시계열. analyze_fft 는 (x,y) 만 받으므로 호출부에서 분리.
        self._cop_history: list[tuple[datetime, float, float]] = []
        self._session_started_at: datetime | None = None
        # MQTT 경로로 수신된 최신 압력 프레임 버퍼.
        # _build_posture_frame 이 신선한 MQTT 데이터를 우선 소비하고,
        # 없으면 serial_reader.read_frame() 로 fallback.
        self._mqtt_pressure_map: np.ndarray | None = None
        self._mqtt_received_at: datetime | None = None
        # MediaPipe tracking-lost 시 직전 유효 SkeletonData 를 hold 한다.
        # 유효 = landmarks 가 None 이 아니고 25개 이상.
        self._last_skeleton: SkeletonData | None = None
        # 거북목 캘리브레이션·점수화 (rest 가 collector 를 트리거하고 store 가 영속).
        self._calibration_store = CalibrationStore()
        self._calibration_collector = CalibrationCollector()
        self._forward_head = ForwardHeadProcessor(alpha=0.3, sustain_seconds=1.5)
        # 종합 진단: 신규 지표 EMA + 명명 상태 (risk 미반영, 상태/시계열 전용).
        self._posture_state = PostureStateProcessor(alpha=0.3)
        # 시연 시나리오: 활성 시 압력맵만 시나리오화 (자세 점수는 미주입).
        self._demo = DemoScenario()
        # 시계열 적재 (읽어서 기록만, fire-and-forget). 실시간 경로 무영향.
        self._posture_logger = PostureLogger()

    async def connect(self, websocket: WebSocket) -> None:
        # 신규 클라이언트 연결 수락 후 파이프라인을 시작한다.
        await websocket.accept()
        self.active_connections.add(websocket)
        if self._stream_task is None or self._stream_task.done():
            await self._startup_pipeline()
            self._stream_task = asyncio.create_task(self._stream_loop())

    async def disconnect(self, websocket: WebSocket) -> None:
        # 연결 해제 시 클라이언트 제거 후 필요하면 전체 리소스를 정리한다.
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        if not self.active_connections:
            await self._shutdown_pipeline()

    def submit_mqtt_frame(self, frame: np.ndarray, received_at: datetime) -> None:
        # MQTT 경로로 들어온 압력 프레임을 stream loop 가 소비할 수 있도록 버퍼에 저장한다.
        # 단순 필드 쓰기이므로 동기 메서드로 노출 (asyncio 단일 스레드 모델하에서 race 없음).
        self._mqtt_pressure_map = frame
        self._mqtt_received_at = received_at

    def _consume_fresh_mqtt_frame(self, now: datetime) -> np.ndarray | None:
        # 신선한 MQTT 프레임이 있으면 반환하고 버퍼를 비운다 (재사용 방지).
        if self._mqtt_pressure_map is None or self._mqtt_received_at is None:
            return None
        age_seconds = (now - self._mqtt_received_at).total_seconds()
        if age_seconds > MQTT_FRESHNESS_SECONDS:
            return None
        frame = self._mqtt_pressure_map
        self._mqtt_pressure_map = None
        self._mqtt_received_at = None
        return frame

    async def broadcast(self, payload: dict) -> None:
        # 연결된 모든 클라이언트에게 현재 자세 프레임을 전송한다.
        stale_connections: list[WebSocket] = []
        for connection in self.active_connections:
            try:
                await connection.send_json(payload)
            except Exception:
                stale_connections.append(connection)

        for stale in stale_connections:
            self.active_connections.discard(stale)

        if not self.active_connections:
            await self._shutdown_pipeline()

    async def _startup_pipeline(self) -> None:
        # 센서/카메라 파이프라인 초기화
        # MediaPipe/시리얼 미준비 상태에서도 더미 모드로 stream loop 가 동작해야 한다
        self._cop_history.clear()
        self._last_skeleton = None
        self._forward_head.reset()
        self._posture_state.reset()
        self._session_started_at = datetime.now(timezone.utc)
        try:
            await self._serial_reader.connect()
        except Exception as exc:
            logger.warning("시리얼 리더 연결 실패 - 더미 모드로 계속 진행: %s", exc)
        # MediaPipe init 은 background 로 진행한다.
        # cv2.VideoCapture 가 Windows 카메라 probe 로 수초간 블로킹할 수 있어
        # 동기 await 하면 _stream_task 생성이 지연되고 첫 broadcast 가 늦어진다.
        # 서비스는 _cap/_pose 가 None 이면 더미 랜드마크를 반환하도록 구현돼 있다.
        asyncio.create_task(self._init_mediapipe_async())
        # 시계열 적재 세션 시작 (DB 미가용이어도 내부에서 흡수 -> 더미모드 유지).
        await self._posture_logger.start_session()

    async def _init_mediapipe_async(self) -> None:
        # MediaPipe 시작 시도. 실패 시 더미 모드 강제 활성화.
        try:
            await self._mediapipe_service.start()
        except Exception as exc:
            logger.warning("MediaPipe 시작 실패 - 더미 모드로 계속 진행: %s", exc)
            # get_landmarks 가 더미 랜드마크를 반환하도록 보장
            self._mediapipe_service.is_dummy_mode = True

    async def _shutdown_pipeline(self) -> None:
        # 스트리밍 중지 및 하드웨어 리소스 정리
        if self._stream_task is not None and not self._stream_task.done():
            self._stream_task.cancel()
            try:
                await self._stream_task
            except asyncio.CancelledError:
                pass
        self._stream_task = None
        await self._serial_reader.disconnect()
        await self._mediapipe_service.stop()
        await self._posture_logger.end_session()
        self._cop_history.clear()
        self._session_started_at = None

    async def _stream_loop(self) -> None:
        # 20Hz 주기로 프레임을 생성하고 모든 클라이언트에 브로드캐스트한다.
        interval: float = 1.0 / float(settings.sample_rate)
        while self.active_connections:
            try:
                frame = await self._build_posture_frame()
                await self.broadcast(frame.model_dump(mode="json"))
                # 읽어서 기록만 (fire-and-forget, 다운샘플). 블로킹/회귀 0.
                self._posture_logger.maybe_log(frame, self._demo.active)
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                raise
            except Exception:
                # 루프 오류 시에도 서비스가 중단되지 않도록 다음 주기를 계속 시도한다.
                await asyncio.sleep(interval)

    async def _build_posture_frame(self) -> PostureFrame:
        # 압력 프레임 수집 우선순위:
        #  1) 시연 모드 활성 -> 시나리오 압력맵 (압력만 시나리오화, 자세는 실제 유지)
        #  2) 신선한 MQTT 프레임
        #  3) 시리얼(또는 더미) fallback
        timestamp = datetime.now(timezone.utc)
        demo_frame = self._demo.next_frame()
        if demo_frame is not None:
            pressure_map = demo_frame
        else:
            mqtt_frame = self._consume_fresh_mqtt_frame(timestamp)
            if mqtt_frame is not None:
                pressure_map = mqtt_frame
            else:
                pressure_map = await self._serial_reader.read_frame()

        pressure = PressureData(pressure_map=pressure_map.astype(float).tolist(), timestamp=timestamp)
        cop: CoPData = calculate_cop(pressure_map)

        self._cop_history.append((timestamp, cop.cop_x, cop.cop_y))
        # 타임스탬프 기준 30초 윈도로 prune (+ 폭주 방어 하드 캡).
        cutoff = timestamp - timedelta(seconds=FFT_WINDOW_SECONDS)
        self._cop_history = [s for s in self._cop_history if s[0] >= cutoff]
        if len(self._cop_history) > _COP_HISTORY_HARD_CAP:
            self._cop_history = self._cop_history[-_COP_HISTORY_HARD_CAP:]

        # 유효 sample_rate = (샘플수-1) / 실측 윈도시간. 가변 fps 에서도 주파수축 정확.
        if len(self._cop_history) >= 2:
            span = (self._cop_history[-1][0] - self._cop_history[0][0]).total_seconds()
            effective_sr = (len(self._cop_history) - 1) / span if span > 0 else float(settings.sample_rate)
        else:
            effective_sr = float(settings.sample_rate)

        fft: FFTData = analyze_fft(
            [(x, y) for _ts, x, y in self._cop_history], sample_rate=effective_sr
        )

        landmarks = await self._mediapipe_service.get_landmarks()
        avg_vis = 0.0
        avatar_landmarks: AvatarLandmarks | None = None
        if landmarks is None:
            # tracking lost: 직전 유효값 hold + flag. 첫 프레임이면 0 + flag.
            # avatar landmarks 는 hold 하지 않음 (정지화면 = silent failure).
            if self._last_skeleton is not None:
                skeleton = self._last_skeleton.model_copy(
                    update={"tracking_ok": False, "landmarks": None}
                )
            else:
                skeleton = SkeletonData(tracking_ok=False)
        else:
            skeleton = calculate_skeleton_metrics(landmarks)
            # tracking_ok 는 schemas default True 로 자동 채워짐.
            if len(landmarks) >= 25:
                self._last_skeleton = skeleton
                # 핵심 landmark (nose, 양 ear, 양 shoulder) 평균 visibility - 캘리브 품질 가드용.
                avg_vis = sum(float(landmarks[i]["visibility"]) for i in (0, 7, 8, 11, 12)) / 5.0
            # 사이드뷰 아바타 좌표 추출 (산식 함수와 별도 패스).
            avatar_landmarks = _build_avatar_landmarks(
                landmarks, self._mediapipe_service.is_dummy_mode
            )

        # 캘리브레이션 수집 중이면 frame 을 collector 에 흘려보낸다.
        # tracking_ok=False (lost) frame 은 dummy=True 로 표시해 거부시킨다.
        if self._calibration_collector.is_collecting():
            is_dummy = self._mediapipe_service.is_dummy_mode or not skeleton.tracking_ok
            self._calibration_collector.add_frame(
                z2=skeleton.z2,
                avg_vis=avg_vis,
                dummy=is_dummy,
                sh_slope=skeleton.shoulder_slope,
            )

        # 거북목 점수: 캘리브레이션 살아있을 때만 진짜 계산. 아니면 0 + 플래그.
        if self._calibration_store.calibrated:
            delta_ema, neck_score, is_turtle = self._forward_head.update(
                z2=skeleton.z2,
                z2_good=self._calibration_store.z2_good,  # type: ignore[arg-type]
                z2_bad=self._calibration_store.z2_bad,  # type: ignore[arg-type]
                now=timestamp,
            )
            skeleton = skeleton.model_copy(update={
                "forward_head_delta": delta_ema,
                "neck_score": neck_score,
                "is_turtle_neck": is_turtle,
                "calibrated": True,
                "calib_status": self._calibration_collector.status_label
                if self._calibration_collector.is_collecting() else "ok",
                "landmarks": avatar_landmarks,
            })
        else:
            skeleton = skeleton.model_copy(update={
                "forward_head_delta": 0.0,
                "neck_score": 0.0,
                "is_turtle_neck": False,
                "calibrated": False,
                "calib_status": self._calibration_collector.status_label
                if self._calibration_collector.is_collecting() else "uncalibrated",
                "landmarks": avatar_landmarks,
            })

        # 종합 진단: 신규 지표 EMA 스무딩 + 명명 상태 (risk 점수에는 미반영).
        if skeleton.tracking_ok:
            head_s, shoulder_s, state = self._posture_state.update(
                skeleton.head_tilt, skeleton.shoulder_asym, skeleton.is_turtle_neck
            )
            skeleton = skeleton.model_copy(update={
                "head_tilt": head_s,
                "shoulder_asym": shoulder_s,
                "posture_state": state,
            })
        else:
            # 추적 끊김: 신규 지표는 stale -> 상태는 unknown (양호 위장 금지).
            skeleton = skeleton.model_copy(update={"posture_state": "unknown"})

        if self._session_started_at is None:
            duration_seconds = 0.0
        else:
            duration_seconds = (timestamp - self._session_started_at).total_seconds()

        risk: RiskData = calculate_risk_level(cop=cop, fft=fft, skeleton=skeleton, duration_seconds=duration_seconds)

        return PostureFrame(
            pressure=pressure,
            cop=cop,
            fft=fft,
            skeleton=skeleton,
            risk=risk,
            timestamp=timestamp,
            demo=DemoState(**self._demo.status()),
        )


manager = ConnectionManager()


@router.websocket("/posture")
async def posture_socket(websocket: WebSocket) -> None:
    await manager.connect(websocket)
    try:
        # 클라이언트 연결 유지용 수신 루프
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        await manager.disconnect(websocket)
