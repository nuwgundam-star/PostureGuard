from datetime import datetime

from pydantic import BaseModel, Field


class PressureData(BaseModel):
    # 16x16 압력 맵(행렬 형태)
    pressure_map: list[list[float]] = Field(
        ...,
        min_length=16,
        max_length=16,
        description="16x16 pressure map",
    )
    # 프레임 수집 시각
    timestamp: datetime


class CoPData(BaseModel):
    # 압력 중심 좌표(x, y)와 총 압력
    cop_x: float
    cop_y: float
    total_pressure: float


class FFTData(BaseModel):
    # 주파수 대역별 에너지 및 피로 점수
    dc_energy: float
    low_energy: float
    high_energy: float
    fatigue_score: float = Field(..., ge=0.0, le=1.0)


class AvatarPoint(BaseModel):
    # mediapipe 정규화 좌표 [0,1] + visibility [0,1]. z 좌표는 사이드뷰 미러에 불필요.
    x: float
    y: float
    visibility: float


class AvatarLandmarks(BaseModel):
    # 사이드뷰 디지털 트윈에 필요한 7개 좌표만. 전체 33개 전송 금지.
    nose: AvatarPoint        # mediapipe idx 0
    ear_l: AvatarPoint       # idx 7
    ear_r: AvatarPoint       # idx 8
    shoulder_l: AvatarPoint  # idx 11
    shoulder_r: AvatarPoint  # idx 12
    hip_l: AvatarPoint       # idx 23
    hip_r: AvatarPoint       # idx 24


class SkeletonData(BaseModel):
    # 스켈레톤 기반 자세 지표
    # neck_angle: deprecated. 정면 2D 산식이라 거북목 무반응. 정보용으로만 유지.
    # default 부여: 추적 실패 첫 프레임 SkeletonData(tracking_ok=False) 생성 시 크래시 방지.
    neck_angle: float = 0.0
    shoulder_slope: float = 0.0
    trunk_tilt: float = 0.0
    is_turtle_neck: bool = False
    # MediaPipe Pose 추적 성공 여부. False=lost(직전 유효값 hold 또는 0)
    tracking_ok: bool = True
    # 귀-어깨 평균 z 차이 (mediapipe 상대깊이). 음수일수록 머리가 카메라에 가까움.
    z2: float = 0.0
    # forward_head_delta (= EMA(max(0, z2_good - z2))) - 거북목일수록 양수.
    forward_head_delta: float = 0.0
    # 캘리브레이션 기반 거북목 점수 (0~20). risk 의 neck 20점 입력.
    neck_score: float = 0.0
    # 캘리브레이션 완료 여부. False 면 neck_score 강제 0 (silent-failure 가드).
    calibrated: bool = False
    # 캘리브레이션 상태 메시지 ("ok" / "uncalibrated" / "calibrating_good" / ...).
    calib_status: str = "uncalibrated"
    # 사이드뷰 아바타용 7개 좌표. tracking_ok=False/dummy/저신뢰 시 None (silent-failure 가드).
    landmarks: AvatarLandmarks | None = None
    # 신규 종합 진단 지표 (절대 0기준, 캘리브 없음, risk 배점 미반영 — 상태문장·시계열 전용).
    head_tilt: float | None = None       # 머리 좌우기울기(deg). 저신뢰 시 None.
    shoulder_asym: float | None = None   # 어깨 비대칭(deg). 저신뢰 시 None.
    posture_state: str = "unknown"       # 명명 상태: 양호/거북목/어깨 비대칭/머리 기울임/복합/unknown


class RiskData(BaseModel):
    # 위험도 레벨(0=Notice, 1=Warning, 2=Danger) 및 점수
    level: int = Field(..., ge=0, le=2)
    score: float = Field(..., ge=0.0, le=100.0)
    reasons: list[str] = Field(default_factory=list)


class DemoState(BaseModel):
    # 시연 모드 상태 (정직성: 배지/안내 문구용). 압력만 시나리오화하며 자세 점수는 미주입.
    active: bool = False
    mode: str = "off"        # off|good|warning|danger (사용자가 선택한 모드)
    loop: bool = False       # 자동 루프(양호↔주의) 동작 여부
    effective: str = "off"   # loop 시 현재 구간 (good/warning)
    label: str = ""          # UI 안내 문구


class PostureFrame(BaseModel):
    # 통합 자세 프레임
    pressure: PressureData
    cop: CoPData
    fft: FFTData
    skeleton: SkeletonData
    risk: RiskData
    timestamp: datetime
    # 시연 모드 상태 (비활성 시 active=False). 프론트 배지/안내용.
    demo: DemoState = Field(default_factory=DemoState)


class CalibrationRequest(BaseModel):
    # 사용자별 캘리브레이션 요청 정보
    user_id: str
    duration_seconds: int = Field(..., gt=0)
