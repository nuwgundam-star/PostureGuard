"""종합 자세 명명 상태 분류 (신규 지표 EMA 스무딩 + 상태 라벨).

❗원칙:
  - 신규 지표(head_tilt, shoulder_asym)는 절대 0기준·캘리브 없음. risk 배점 미반영.
  - EMA(α=0.3, 기존 forward_head 패턴) 로 떨림 억제.
  - 상태 라벨은 거북목(기존 z2 캘리브 결과 is_turtle_neck) + 신규 2지표 조합으로만 분류.
  - 거북목 z2·risk·캘리브 경로는 건드리지 않는다(읽기만).
"""

from __future__ import annotations

# 상태 분류 임계(deg). 데드존(미세 흔들림 무시) 겸용 — 이하 전부 "양호".
# 데이터 근거(정상착석 실측): head_tilt mean 9.95/sd 4.96/p95 16.1/max 16.6,
#   shoulder_asym mean 3.33/sd 2.95/p95 9.5/max 9.6 -> 정상 p95·max 보다 충분히 위(≈mean+2sd)로 설정.
# (노트북 카메라 오프셋으로 정자세 baseline 이 0이 아님 — 절대 0기준이라 임계가 이를 흡수)
HEAD_TILT_STATE_DEG = 20.0
SHOULDER_ASYM_STATE_DEG = 12.0


class PostureStateProcessor:
    def __init__(self, alpha: float = 0.3) -> None:
        self._alpha = alpha
        self._head_ema: float | None = None
        self._shoulder_ema: float | None = None

    def reset(self) -> None:
        # 스트림 시작 시 누적 초기화.
        self._head_ema = None
        self._shoulder_ema = None

    def update(
        self,
        head_raw: float | None,
        shoulder_raw: float | None,
        is_turtle_neck: bool,
    ) -> tuple[float | None, float | None, str]:
        # 신규 지표 EMA 스무딩 후 명명 상태 분류. 반환: (head_ema, shoulder_ema, posture_state)
        head = self._smooth_head(head_raw)
        shoulder = self._smooth_shoulder(shoulder_raw)
        return head, shoulder, self._classify(head, shoulder, is_turtle_neck)

    def _smooth_head(self, raw: float | None) -> float | None:
        if raw is None:
            return None  # 저신뢰 프레임: 값 위조 금지(silent-failure 가드). 누적은 유지.
        self._head_ema = raw if self._head_ema is None else self._alpha * raw + (1 - self._alpha) * self._head_ema
        return self._head_ema

    def _smooth_shoulder(self, raw: float | None) -> float | None:
        if raw is None:
            return None
        self._shoulder_ema = (
            raw if self._shoulder_ema is None else self._alpha * raw + (1 - self._alpha) * self._shoulder_ema
        )
        return self._shoulder_ema

    def _classify(self, head: float | None, shoulder: float | None, is_turtle: bool) -> str:
        # 우선순위(표기순): 거북목 → 어깨 비대칭 → 머리 기울임.
        flags: list[str] = []
        if is_turtle:
            flags.append("거북목")
        if shoulder is not None and shoulder >= SHOULDER_ASYM_STATE_DEG:
            flags.append("어깨 비대칭")
        if head is not None and head >= HEAD_TILT_STATE_DEG:
            flags.append("머리 기울임")

        # 신뢰 가능한 데이터가 전혀 없으면 unknown (양호로 위장하지 않음).
        if not flags and head is None and shoulder is None and not is_turtle:
            return "unknown"
        if not flags:
            return "양호"
        if len(flags) >= 2:
            return "복합"
        return flags[0]
