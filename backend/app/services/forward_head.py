"""거북목 동적 점수화. 캘리브레이션이 살아있을 때만 작동."""

from __future__ import annotations

from datetime import datetime


class ForwardHeadProcessor:
    """delta_raw = max(0, z2_good - z2) 의 EMA + 지속 게이트 + 정규화 점수."""

    def __init__(
        self,
        alpha: float = 0.3,
        sustain_seconds: float = 1.5,
        deadzone_frac: float = 0.05,
    ) -> None:
        self.alpha = alpha
        self.sustain_seconds = sustain_seconds
        # 데드존: span 대비 이 비율(≈neck_score 1점) 미만의 delta_ema 는 '거북목 아님'.
        # delta_ema 는 음 아닌 delta_raw 의 EMA 라 한 번 양수가 되면 부동소수점상 정확히
        # 0 으로 안 돌아와, 데드존이 없으면 over_onset 이 수 분간 래치된다.
        self.deadzone_frac = deadzone_frac
        self.delta_ema = 0.0
        self._over_onset_since: datetime | None = None

    def reset(self) -> None:
        self.delta_ema = 0.0
        self._over_onset_since = None

    def update(
        self,
        z2: float,
        z2_good: float,
        z2_bad: float,
        now: datetime,
    ) -> tuple[float, float, bool]:
        """매 frame 호출. (delta_ema, neck_score[0~20], is_turtle_neck) 반환."""
        delta_raw = max(0.0, z2_good - z2)
        self.delta_ema = self.alpha * delta_raw + (1.0 - self.alpha) * self.delta_ema

        # 데드존: delta_ema 가 span 의 일정 비율(≈neck_score 1점)을 넘어야 onset.
        # 이 아래로 내려가면 over_onset=False -> _over_onset_since 즉시 리셋 -> sustained 해제
        # (래치 잔존 0). neck_score 도 같은 span 으로 산출돼 점수·상태가 항상 정합.
        span = max(z2_good - z2_bad, 1e-6)
        eps = span * self.deadzone_frac
        over_onset = self.delta_ema > eps
        if over_onset:
            if self._over_onset_since is None:
                self._over_onset_since = now
            sustained = (now - self._over_onset_since).total_seconds() >= self.sustain_seconds
        else:
            self._over_onset_since = None
            sustained = False

        if sustained:
            neck_score = min(max(self.delta_ema / span, 0.0), 1.0) * 20.0
            return self.delta_ema, neck_score, True
        return self.delta_ema, 0.0, False
