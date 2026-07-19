from __future__ import annotations

from typing import Any

import numpy as np

from app.models.schemas import CoPData, FFTData, RiskData, SkeletonData


def calculate_cop(pressure_map: np.ndarray) -> CoPData:
    """압력 맵(2D)에서 CoP(x, y)와 총 압력을 계산한다."""
    # 입력 배열 차원을 검증해 계산 안정성을 보장한다.
    if pressure_map.ndim != 2:
        raise ValueError("pressure_map must be a 2D array.")

    total_pressure: float = float(np.sum(pressure_map))
    if total_pressure <= 0.0:
        return CoPData(cop_x=0.0, cop_y=0.0, total_pressure=0.0)

    y_indices, x_indices = np.indices(pressure_map.shape)
    cop_x: float = float(np.sum(pressure_map * x_indices) / total_pressure)
    cop_y: float = float(np.sum(pressure_map * y_indices) / total_pressure)
    return CoPData(cop_x=cop_x, cop_y=cop_y, total_pressure=total_pressure)


def analyze_fft(cop_history: list[tuple[float, float]], sample_rate: float = 20.0) -> FFTData:
    """CoP 이력의 주파수 성분을 분석해 피로 점수를 계산한다.

    sample_rate 는 '실제 유효 샘플레이트'(Hz)를 받는다. 가변 fps 환경에서
    호출부가 윈도의 실측 샘플수/실측시간으로 계산해 넘긴다 -> 주파수축 왜곡 방지.
    대역 분리·fatigue 산식 자체는 무변경.
    """
    # x, y를 분리한 뒤 크기 신호로 변환하여 단일 FFT로 분석한다.
    if len(cop_history) < 2 or sample_rate <= 0:
        return FFTData(dc_energy=0.0, low_energy=0.0, high_energy=0.0, fatigue_score=0.0)

    values: np.ndarray = np.asarray(cop_history, dtype=float)
    magnitude_signal: np.ndarray = np.sqrt(np.square(values[:, 0]) + np.square(values[:, 1]))

    fft_values: np.ndarray = np.fft.rfft(magnitude_signal)
    freqs: np.ndarray = np.fft.rfftfreq(magnitude_signal.size, d=1.0 / float(sample_rate))
    power: np.ndarray = np.square(np.abs(fft_values))

    dc_energy: float = float(power[0]) if power.size > 0 else 0.0
    low_energy: float = float(np.sum(power[(freqs >= 0.1) & (freqs <= 1.0)]))
    high_energy: float = float(np.sum(power[freqs > 1.0]))

    # DC(정적 하중 = 체중 평균^2)는 동적 피로와 무관하므로 분모에서 제외.
    # fatigue = 동적 sway 에너지(low+high) 중 고주파(1Hz+) 비중.
    ac_energy: float = low_energy + high_energy
    fatigue_score: float = float(high_energy / ac_energy) if ac_energy > 1e-9 else 0.0
    fatigue_score = float(np.clip(fatigue_score, 0.0, 1.0))

    return FFTData(
        dc_energy=dc_energy,
        low_energy=low_energy,
        high_energy=high_energy,
        fatigue_score=fatigue_score,
    )


# 신규 지표용 핵심 landmark visibility 게이트 (기존 silent-failure 가드 패턴과 일관).
_LATERAL_VIS_MIN = 0.5


def _vis_pair(
    landmarks: list[dict[str, Any]], idx: tuple[int, int]
) -> tuple[tuple[float, float], tuple[float, float]] | None:
    # 두 landmark 의 visibility 가 모두 임계 이상일 때만 (x,y) 쌍 반환. 아니면 None.
    if max(idx) >= len(landmarks):
        return None
    a, b = landmarks[idx[0]], landmarks[idx[1]]
    if float(a["visibility"]) < _LATERAL_VIS_MIN or float(b["visibility"]) < _LATERAL_VIS_MIN:
        return None
    return (float(a["x"]), float(a["y"])), (float(b["x"]), float(b["y"]))


def _lateral_tilt_deg(
    landmarks: list[dict[str, Any]],
    primary: tuple[int, int],
    fallback: tuple[int, int] | None,
) -> float | None:
    # 좌우 두 점의 y차로 좌우 기울기 각(deg, 절댓값) 산출. 정면 좌표 기반, 캘리브 없음.
    # 대칭(수평)=0 이 정상. primary 저신뢰 시 fallback 으로 보조. 둘 다 저신뢰면 None.
    pair = _vis_pair(landmarks, primary)
    if pair is None and fallback is not None:
        pair = _vis_pair(landmarks, fallback)
    if pair is None:
        return None
    (lx, ly), (rx, ry) = pair
    dx = rx - lx
    dy = ry - ly
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        return 0.0
    # 수평으로부터의 예각(0~90). |dx|,|dy| 사용 -> 좌우 index 순서/거울상과 무관하게
    # 수평(대칭)=0, 기울수록 상승. (방향 부호는 불필요 — 이탈 '크기'만 본다)
    return float(np.degrees(np.arctan2(abs(dy), abs(dx))))


def calculate_skeleton_metrics(landmarks: list[dict[str, Any]]) -> SkeletonData:
    """33개 랜드마크에서 목 각도, 어깨 기울기, 몸통 기울기를 계산한다."""
    # 필수 랜드마크 부족 시 안전한 기본값을 반환한다.
    if len(landmarks) < 25:
        return SkeletonData(neck_angle=0.0, shoulder_slope=0.0, trunk_tilt=0.0, is_turtle_neck=False)

    left_shoulder: dict[str, Any] = landmarks[11]
    right_shoulder: dict[str, Any] = landmarks[12]
    nose: dict[str, Any] = landmarks[0]
    left_hip: dict[str, Any] = landmarks[23]
    right_hip: dict[str, Any] = landmarks[24]

    shoulder_center: np.ndarray = np.array(
        [
            (float(left_shoulder["x"]) + float(right_shoulder["x"])) / 2.0,
            (float(left_shoulder["y"]) + float(right_shoulder["y"])) / 2.0,
        ],
        dtype=float,
    )
    hip_center: np.ndarray = np.array(
        [
            (float(left_hip["x"]) + float(right_hip["x"])) / 2.0,
            (float(left_hip["y"]) + float(right_hip["y"])) / 2.0,
        ],
        dtype=float,
    )
    nose_point: np.ndarray = np.array([float(nose["x"]), float(nose["y"])], dtype=float)

    neck_vector: np.ndarray = nose_point - shoulder_center
    vertical_vector: np.ndarray = np.array([0.0, -1.0], dtype=float)
    neck_norm: float = float(np.linalg.norm(neck_vector))

    if neck_norm <= 0.0:
        neck_angle: float = 0.0
    else:
        cos_theta: float = float(np.dot(vertical_vector, neck_vector) / neck_norm)
        cos_theta = float(np.clip(cos_theta, -1.0, 1.0))
        neck_angle = float(np.degrees(np.arccos(cos_theta)))

    dx_shoulder: float = float(right_shoulder["x"]) - float(left_shoulder["x"])
    dy_shoulder: float = float(right_shoulder["y"]) - float(left_shoulder["y"])
    shoulder_slope: float = (dy_shoulder / dx_shoulder) if abs(dx_shoulder) > 1e-9 else 0.0

    trunk_vector: np.ndarray = shoulder_center - hip_center
    trunk_norm: float = float(np.linalg.norm(trunk_vector))
    if trunk_norm <= 0.0:
        trunk_tilt: float = 0.0
    else:
        trunk_cos: float = float(np.dot(vertical_vector, trunk_vector) / trunk_norm)
        trunk_cos = float(np.clip(trunk_cos, -1.0, 1.0))
        trunk_tilt = float(np.degrees(np.arccos(trunk_cos)))

    # is_turtle_neck 은 캘리브레이션 + EMA + 지속조건 거친 결과로 websocket 단에서 덮어쓴다.
    # 기하만으로는 거북목 판정 불가(정면 2D 한계). 여기서는 default False.
    is_turtle_neck: bool = False

    # z2 = mean(ear_L.z, ear_R.z) - mean(shoulder_L.z, shoulder_R.z)
    # 거북목 = 머리/귀가 어깨보다 카메라에 더 가까움 => z2 가 더 음수.
    left_ear: dict[str, Any] = landmarks[7]
    right_ear: dict[str, Any] = landmarks[8]
    ear_z: float = (float(left_ear["z"]) + float(right_ear["z"])) / 2.0
    sh_z: float = (float(left_shoulder["z"]) + float(right_shoulder["z"])) / 2.0
    z2: float = ear_z - sh_z

    # 신규(절대 0기준, 캘리브 없음): 머리 좌우기울기 / 어깨 비대칭.
    # head_tilt: 두 귀(7,8) y차 기울기각, 귀 저신뢰 시 눈(1,4) 보조.
    # shoulder_asym: 두 어깨(11,12) y차 기울기각 (shoulder_slope 를 각도로 명시화).
    head_tilt: float | None = _lateral_tilt_deg(landmarks, (7, 8), (1, 4))
    shoulder_asym: float | None = _lateral_tilt_deg(landmarks, (11, 12), None)

    return SkeletonData(
        neck_angle=neck_angle,
        shoulder_slope=shoulder_slope,
        trunk_tilt=trunk_tilt,
        is_turtle_neck=is_turtle_neck,
        z2=z2,
        head_tilt=head_tilt,
        shoulder_asym=shoulder_asym,
    )


def calculate_risk_level(
    cop: CoPData,
    fft: FFTData,
    skeleton: SkeletonData,
    duration_seconds: float,
) -> RiskData:
    """CoP, FFT, 스켈레톤 지표를 통합해 최종 위험 레벨과 점수를 계산한다."""
    # 지표별 가중합으로 0~100 위험 점수를 산출한다.
    score: float = 0.0
    reasons: list[str] = []

    cop_offset_score: float = float(min(30.0, np.hypot(cop.cop_x - 7.5, cop.cop_y - 7.5) * 3.0))
    score += cop_offset_score
    if cop_offset_score >= 18.0:
        reasons.append("압력 중심 편향이 큼")

    fft_score: float = float(min(30.0, fft.fatigue_score * 30.0))
    score += fft_score
    if fft.fatigue_score >= 0.6:
        reasons.append("고주파 피로 성분 증가")

    # neck 20점 입력: 캘리브레이션 + EMA 거친 neck_score (0~20) 를 그대로 받는다.
    # calibrated=False 면 websocket 단에서 0 으로 강제 -> silent-failure 가드.
    neck_score: float = float(min(20.0, max(0.0, skeleton.neck_score)))
    trunk_score: float = float(min(10.0, max(0.0, skeleton.trunk_tilt - 5.0)))
    shoulder_score: float = float(min(10.0, abs(skeleton.shoulder_slope) * 50.0))
    score += neck_score + trunk_score + shoulder_score

    if skeleton.is_turtle_neck:
        reasons.append("거북목 가능성")
    if abs(skeleton.shoulder_slope) > 0.08:
        reasons.append("어깨 수평 불균형")
    if skeleton.trunk_tilt > 12.0:
        reasons.append("몸통 기울어짐")

    if duration_seconds >= 600.0:
        score += 5.0
        reasons.append("장시간 연속 착석")

    score = float(np.clip(score, 0.0, 100.0))
    if score < 40.0:
        level: int = 0
    elif score < 70.0:
        level = 1
    else:
        level = 2

    return RiskData(level=level, score=score, reasons=reasons)
