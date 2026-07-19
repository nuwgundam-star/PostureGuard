"""시연용 압력 시나리오 생성기.

❗정직성 원칙: 압력 입력(16x16)만 시나리오화한다.
   스켈레톤/neck_score 등 자세 점수는 절대 주입하지 않는다(자세 위조 금지).
   생성된 압력맵은 실제 산식(calculate_cop/analyze_fft/calculate_risk_level)을
   그대로 통과한다 -> 진짜 산식·진짜 점수.

모드:
  off      : 시연 비활성 (실제 MQTT/serial 사용)
  good     : 중앙 균형 + 저주파 sway + 저노이즈 -> 양호(level 0), fatigue 낮음
  warning  : 한쪽 쏠림 + 고주파 지터(>1Hz) -> 주의(level 1), fatigue 유발
  danger   : 코너 쏠림(최대) + 고주파 지터 -> 압력 기여 ~51
             (level 2 는 발표자 거북목 자세 결합으로만 도달 = 진짜 자세)
  loop     : good <-> warning 자동 순회 (무인 데모.
             danger 는 자세 결합이 필요해 자동 루프에서 제외)
"""

from __future__ import annotations

import math

import numpy as np

from app.core.config import settings

ROWS = 16
COLS = 16

# 자동 루프 한 구간 길이(초). good 8초 -> warning 8초 순회.
_LOOP_SEGMENT_SECONDS = 8

# UI 안내 문구 (정직성: 위험은 자세 결합 필요함을 명시).
_LABELS: dict[str, str] = {
    "off": "",
    "good": "시연: 양호 자세",
    "warning": "시연: 주의 단계 (한쪽 쏠림 + 피로 떨림)",
    "danger": "위험 단계 시연: 거북목 자세를 함께 취하세요 (압력+자세 결합)",
}
_LOOP_LABEL = "자동 데모: 양호↔주의 순회 (위험은 자세 결합 필요)"


def _gauss(cx: float, cy: float, amp: float, sigma: float) -> np.ndarray:
    # (cx, cy) 중심 가우시안 하중 분포. x=열, y=행.
    y_grid, x_grid = np.meshgrid(
        np.arange(ROWS, dtype=np.float64),
        np.arange(COLS, dtype=np.float64),
        indexing="ij",
    )
    return amp * np.exp(-(((x_grid - cx) ** 2) + ((y_grid - cy) ** 2)) / (2.0 * sigma**2))


class DemoScenario:
    """프레임 단위로 호출되어 시연 압력맵을 생성하는 상태 머신."""

    def __init__(self) -> None:
        self._mode: str = "off"  # off|good|warning|danger
        self._loop: bool = False
        self._tick: int = 0  # 프레임 카운터 (sample_rate Hz 기준 = 지터/순회 시간축)

    @property
    def active(self) -> bool:
        # 시연 프레임을 주입 중인지 여부.
        return self._loop or self._mode != "off"

    def set_mode(self, mode: str) -> None:
        # 단발 시나리오 지정 (good|warning|danger). 자동 루프는 해제.
        if mode not in ("good", "warning", "danger"):
            raise ValueError(f"unknown demo mode: {mode}")
        self._mode = mode
        self._loop = False
        self._tick = 0

    def set_loop(self, on: bool) -> None:
        # 자동 루프 토글. 켜면 단발 모드는 해제.
        self._loop = on
        self._mode = "off"
        self._tick = 0

    def off(self) -> None:
        # 시연 종료 -> 실제 데이터 경로로 복귀.
        self._mode = "off"
        self._loop = False
        self._tick = 0

    def _effective_mode(self) -> str:
        # 자동 루프면 현재 구간(good/warning)을, 아니면 지정 모드를 반환.
        if not self._loop:
            return self._mode
        period = max(1, settings.sample_rate * _LOOP_SEGMENT_SECONDS)
        segment = (self._tick // period) % 2
        return "good" if segment == 0 else "warning"

    def next_frame(self) -> np.ndarray | None:
        # 비활성이면 None (호출부가 실제 데이터로 fallback).
        mode = self._effective_mode()
        if mode == "off":
            return None

        t = self._tick
        self._tick += 1

        if mode == "good":
            frame = self._good_frame(t)
        elif mode == "warning":
            frame = self._warning_frame(t)
        else:
            frame = self._danger_frame(t)
        return np.clip(frame, 0.0, None).astype(np.float32)

    def status(self) -> dict:
        # 프론트 배지/안내 문구용 상태.
        effective = self._effective_mode()
        label = _LOOP_LABEL if self._loop else _LABELS.get(self._mode, "")
        return {
            "active": self.active,
            "mode": self._mode,
            "loop": self._loop,
            "effective": effective,
            "label": label,
        }

    # --- 시나리오별 프레임 생성 ---------------------------------------------

    # 주파수 설계 메모(시간기반 FFT 대응):
    #   고주파(피로) 성분 = '매프레임 반전' 항만 사용한다. 이는 항상 Nyquist(=eff_sr/2)
    #   주파수라 실제 fps(5~30) 와 무관하게 항상 >1Hz(고주파 대역)로 잡힌다 -> fps 강건.
    #   틱공간 sin 항은 실제 fps 에 따라 저주파로 접혀 fatigue 를 왜곡하므로 쓰지 않는다.
    #   저주파(건강한 미세움직임) = good 의 느린 sway(주기 80프레임)로만 부여.

    def _good_frame(self, t: int) -> np.ndarray:
        # 중앙 균형 + 느린 sway(저주파) + 저노이즈 -> CoP 중앙 근처, fatigue 낮음.
        sway = 0.6 * math.sin(2.0 * math.pi * t / 80.0)
        base = (
            _gauss(5.5 + sway, 10.0, 110.0, 2.1)
            + _gauss(10.5 + sway, 10.0, 115.0, 2.0)
            + _gauss(8.0, 8.5, 25.0, 3.8)
        )
        noise = np.random.normal(0.0, 0.3, (ROWS, COLS))
        return base + noise

    def _warning_frame(self, t: int) -> np.ndarray:
        # 한쪽 쏠림(중간) + 매프레임 반전 고주파 지터 -> cop 편향 + fatigue 유발 (주의).
        # 반전 진폭을 크게 줘 고주파 에너지가 잔여 저주파를 빠르게 압도하게 한다.
        # cop 쏠림은 danger 보다 약하게 유지해 두 단계를 구분한다.
        jitter = 1.5 if t % 2 == 0 else -1.5
        base = (
            _gauss(11.0 + jitter, 10.5, 160.0, 2.2)
            + _gauss(12.0 + jitter, 11.5, 95.0, 1.8)
        )
        noise = np.random.normal(0.0, 0.5, (ROWS, COLS))
        return base + noise

    def _danger_frame(self, t: int) -> np.ndarray:
        # 코너 쏠림(최대) + 매프레임 반전 고주파 지터 -> 압력 기여 상한(~51).
        jitter = 1.8 if t % 2 == 0 else -1.8
        base = (
            _gauss(14.0 + jitter, 14.2, 240.0, 2.0)
            + _gauss(14.7 + jitter, 14.9, 130.0, 1.3)
        )
        noise = np.random.normal(0.0, 0.8, (ROWS, COLS))
        return base + noise
