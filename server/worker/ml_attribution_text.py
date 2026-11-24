"""ML 기여도(`methodResults[ml].attribution`, `baseline-shapley-v1`) 설명 문장. 저장값만 쓰는 순수 함수.

문장 틀·반올림·방향 낱말·이름표·조사 규칙은 ML 기여도 계약(Detector 기여도 변경에서 contracts/detection-decision.md에
더하는 화면·설명 문장 절)을 따른다.
값을 다시 계산하지 않는다 — 점수·φ·비율은 저장값을 반올림해서 보이기만 한다.
"""
from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Mapping

INVALID = "explanation_attribution_invalid"
VERSION = "baseline-shapley-v1"

# 변수 이름표(계약 최종 표). 표에 없는 변수는 변수 이름 그대로.
NAMES = {"total_count": "처리량(TOTAL_COUNT)", "success_count": "SUCCESS_COUNT", "error_count": "ERROR_COUNT",
         "retry_count": "RETRY_COUNT", "error_ratio": "오류 비율 값", "elapsed_ms": "처리 시간"}
# 건수 계열은 단위 미확정: 값이 함께 나오는 곳에서만 `(단위 미확인)`. 처리 시간은 ms.
UNCONFIRMED = frozenset({"total_count", "success_count", "error_count", "retry_count"})
UNITS = {"elapsed_ms": "ms"}
UNCONFIRMED_MARK = "(단위 미확인)"
USUAL = "평소 값과 다름"
WORDS = {"higher_is_anomaly": ("올린", "낮아져"), "lower_is_anomaly": ("내린", "높아져")}
LIMITATION = "기여도는 모델이 점수를 매긴 방식에 대한 분석이며 실제 장애 원인이 아닙니다. 원인은 원천 시스템 운영 로그로 확인하세요."
NO_TOP = "원점수를 이상 쪽으로 움직인 변수를 찾지 못했습니다."
NO_SHARE = "학습 데이터 중앙값과 원점수 차이가 거의 없어 비율은 계산하지 않았습니다."
BASELINE_WARNING = "학습 데이터 중앙값에서도 원점수가 판정선을 넘어 비율을 해석할 때 주의가 필요합니다."

# 숫자 끝 글자 읽는 소리의 받침: 0 영·1 일·2 이·3 삼·4 사·5 오·6 육·7 칠·8 팔·9 구 → (받침 있음, ㄹ 받침)
_DIGIT = {"0": (True, False), "1": (True, True), "2": (False, False), "3": (True, False), "4": (False, False),
          "5": (False, False), "6": (True, False), "7": (True, True), "8": (True, True), "9": (False, False)}


def _strip_parens(text: str) -> str:
    """끝의 괄호 덧말(`처리량(TOTAL_COUNT)`·`(단위 미확인)`)을 떼어 조사가 붙을 낱말만 남긴다."""
    text = text.rstrip()
    while text.endswith(")"):
        depth = 0
        for index in range(len(text) - 1, -1, -1):
            depth += {")": 1, "(": -1}.get(text[index], 0)
            if depth == 0:
                break
        if depth != 0 or index == 0:
            return text
        text = text[:index].rstrip()
    return text


def _number_sound(token: str) -> tuple[bool, bool]:
    """숫자 끝 소리. 소수는 끝 숫자를 한 자씩 읽고, 정수는 끝 0의 개수로 자릿값(십·백·천·만·억·조)을 읽는다."""
    if "." in token:
        return _DIGIT[token[-1]]
    digits = token.lstrip("0") or "0"
    if digits == "0":
        return _DIGIT["0"]
    zeros = len(digits) - len(digits.rstrip("0"))
    if zeros == 0:
        return _DIGIT[digits[-1]]
    return zeros < 12, False  # 십(ㅂ)·백(ㄱ)·천·만(ㄴ)·억(ㄱ) 받침, 조는 없음


def _final_sound(word: str) -> tuple[bool, bool]:
    """(받침 있음, ㄹ 받침). 한글은 글자 받침, 숫자는 읽는 소리, 영문은 끝 글자(L·M·N·NG만 받침)로 본다."""
    word = _strip_parens(word)
    if not word:
        return False, False
    last = word[-1]
    if "가" <= last <= "힣":
        final = (ord(last) - 0xAC00) % 28
        return final != 0, final == 8
    if last.isdigit():
        start = len(word)
        while start > 0 and (word[start - 1].isdigit() or word[start - 1] == "."):
            start -= 1
        return _number_sound(word[start:])
    upper = word.upper()
    if upper.endswith("NG"):
        return True, False
    if upper[-1] in "LMN":
        return True, upper[-1] == "L"
    return False, False


def josa(word: str, pair: str) -> str:
    """`은/는`·`이/가`·`을/를`·`으로/로` 중 앞 낱말 받침에 맞는 조사(받침 없거나 ㄹ이면 `로`)."""
    has, rieul = _final_sound(word)
    if pair == "으로/로":
        return "으로" if has and not rieul else "로"
    first, second = pair.split("/")
    return first if has else second


def _finite(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(INVALID)
    return float(value)


def _decimal(value: Any) -> Decimal:
    """저장값 그대로의 십진수. 숫자는 Python repr(반올림 없음), 판정선·실제값은 저장 문자열."""
    if isinstance(value, str):
        try:
            number = Decimal(value)
        except ArithmeticError:
            raise ValueError(INVALID) from None
        if not number.is_finite():
            raise ValueError(INVALID)
        return number
    return Decimal(repr(_finite(value)))


def _fixed(value: Any, digits: int) -> str:
    return format(_decimal(value).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP), "f")


def _plain(value: Any) -> str:
    """기준값(number) 표기: 반올림 없이 지수 없는 십진수, 정수면 소수점 없이(100.0 → 100)."""
    number = _decimal(value)
    if number == number.to_integral_value():
        return format(number.quantize(Decimal(1)), "f")
    return format(number.normalize(), "f")


def _percent(share: Any) -> str:
    return format((_decimal(share) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP), "f") + "%"


def _name(feature: str) -> str:
    return NAMES.get(feature, feature)


def _actual(feature: str, player: Mapping[str, Any]) -> str:
    value = player["actual"].get(feature)
    if not isinstance(value, str) or not value:
        raise ValueError(INVALID)
    return value + (UNCONFIRMED_MARK if feature in UNCONFIRMED else UNITS.get(feature, ""))


def _baseline(feature: str, player: Mapping[str, Any]) -> str:
    return _plain(player["baseline"].get(feature)) + UNITS.get(feature, "")


def _usual(feature: str, player: Mapping[str, Any]) -> bool:
    return player["scaleIsUnit"].get(feature) is True  # 키 없는 이전 기록은 미적용


class _Player:
    def __init__(self, raw: Any):
        if not isinstance(raw, Mapping) or not isinstance(raw.get("playerId"), str):
            raise ValueError(INVALID)
        features = raw.get("features")
        if (not isinstance(features, list) or not features or any(not isinstance(f, str) for f in features)
                or not all(isinstance(raw.get(key), Mapping) for key in ("actual", "baseline"))):
            raise ValueError(INVALID)
        self.raw = {**raw, "scaleIsUnit": raw.get("scaleIsUnit") if isinstance(raw.get("scaleIsUnit"), Mapping) else {}}
        self.id, self.features = raw["playerId"], features
        self.phi = _finite(raw.get("phi"))
        self.share = None if raw.get("share") is None else _finite(raw["share"])
        self.gap = None if raw.get("thresholdGapShare") is None else _finite(raw["thresholdGapShare"])
        for feature in features:  # 구성원마다 실제값(문자열)·기준값(number)이 있어야 한다
            _actual(feature, raw)
            _baseline(feature, raw)
        self.group = len(features) > 1
        self.name = "·".join(_name(f) for f in features)

    def members(self) -> str:
        """묶음 괄호: `{이름} {실제값} · 중앙값 {기준값}`, scale 1 구성원은 중앙값 자리에 `평소 값과 다름`."""
        parts = []
        for feature in self.features:
            reference = USUAL if _usual(feature, self.raw) else "중앙값 " + _baseline(feature, self.raw)
            parts.append(f"{_name(feature)} {_actual(feature, self.raw)} · {reference}")
        return ", ".join(parts)

    def usual_alone(self) -> bool:
        return not self.group and _usual(self.features[0], self.raw)

    def summary(self) -> str:
        if self.group:
            return f"{self.name}({self.members()})"
        return f"{self.name} {USUAL}" if self.usual_alone() else self.name

    def mention(self) -> str:
        """반사실 문장 속 이름(묶음은 구성원 괄호를 함께)."""
        return f"{self.name}({self.members()})" if self.group else self.name

    def replaced_alone(self) -> str:
        """`{top1}만 학습 데이터 중앙값({baseline})으로 바꾸면` 의 앞부분."""
        if self.group:
            return f"{self.mention()}만 학습 데이터 중앙값으로 바꾸면"
        feature = self.features[0]
        if _usual(feature, self.raw):
            return f"{self.name}만 학습 데이터 중앙값으로 바꾸면"
        mark = UNCONFIRMED_MARK if feature in UNCONFIRMED else ""
        return f"{self.name}{mark}만 학습 데이터 중앙값({_baseline(feature, self.raw)})으로 바꾸면"


def _digits(threshold: str, scores: list[Any]) -> int:
    """소수 셋째 자리, 원점수·보이는 반사실 점수가 판정선과 같은 글자면 여섯째 자리까지 늘린다."""
    for digits in range(3, 6):
        line = _fixed(threshold, digits)
        if all(_fixed(score, digits) != line for score in scores):
            return digits
    return 6


def tolerance(score: float) -> float:
    """음수·동률 판단 허용 오차: max(1e-9, 1e-9·|score|), score = attribution의 원점수(scoreActual). 계약 계산 방법 절과 같은 식."""
    return max(1e-9, 1e-9 * abs(score))


def render(attribution: Mapping[str, Any]) -> dict[str, Any]:
    """`status=succeeded` 기여도 → 문장(summary·notes·counterfactual·limitation)과 문장에 쓴 저장 수치.

    형식이 계약과 다르면 ValueError(explanation_attribution_invalid). 성공으로 바꾸지 않는다.
    """
    try:
        return _render(attribution)
    except (KeyError, TypeError, AttributeError, IndexError):
        raise ValueError(INVALID) from None


def _render(attribution: Mapping[str, Any]) -> dict[str, Any]:
    if (attribution.get("version") != VERSION or attribution.get("status") != "succeeded"
            or attribution.get("direction") not in WORDS):
        raise ValueError(INVALID)
    raise_word, lower_word = WORDS[attribution["direction"]]
    threshold = attribution["threshold"]
    if not isinstance(threshold, str):
        raise ValueError(INVALID)
    score = _finite(attribution["scoreActual"])
    tol = tolerance(score)
    players = [_Player(raw) for raw in attribution["players"]]
    by_id = {player.id: player for player in players}
    top_ids, cf = attribution["topK"], attribution["counterfactual"]
    if (not isinstance(top_ids, list) or len(top_ids) > 3 or any(i not in by_id for i in top_ids)
            or not isinstance(cf, list) or len(cf) != len(top_ids)):
        raise ValueError(INVALID)
    top = [by_id[i] for i in top_ids]
    cleared = attribution.get("clearedAtK")
    if cleared is not None and (isinstance(cleared, bool) or not isinstance(cleared, int) or not 1 <= cleared <= len(cf)):
        raise ValueError(INVALID)
    after = [_finite(item["scoreAfter"]) for item in cf]
    shown = ([after[cleared - 1]] + ([after[0]] if cleared > 1 else [])) if cleared else after[-1:]
    digits = _digits(threshold, [score, *shown])
    value, line = _fixed(score, digits), _fixed(threshold, digits)

    notes: list[str] = []
    if not top:
        summary = NO_TOP
    else:
        head = f"머신러닝 모델 원점수(값 {value}, 판정선 {line})를 이상 쪽으로 {raise_word} 변수는 "
        if len(top) == 1:
            # 양수 1개 + scale 1: `{이름}뿐입니다(평소 값과 다름).`
            summary = (head + top[0].name + f"뿐입니다({USUAL})." if top[0].usual_alone()
                       else head + top[0].summary() + "뿐입니다.")
        elif any(player.share is None for player in top):
            summary = head + ", ".join(player.summary() for player in top) + " 순입니다. " + NO_SHARE
        else:
            summary = head + ", ".join(f"{p.summary()} {_percent(p.share)}" for p in top) + " 순입니다."
    negative = [player for player in players if player.phi < -tol]
    if negative:
        names = ", ".join(player.name for player in negative)  # `·`는 묶음 구성원 구분자
        notes.append(f"{names}{josa(names, '은/는')} 원점수를 정상 쪽으로 움직였습니다. 그래서 다른 변수 비율 합이 100%를 넘을 수 있습니다.")
    for player in players:
        if player.group:
            notes.append(f"{player.name}{josa(player.name, '은/는')} 같은 원천 값을 쓰는 변수라 한 묶음으로 계산했습니다.")
    if attribution.get("baselineWarning") is True:
        notes.append(BASELINE_WARNING)

    counterfactual = None
    if cleared == 1:
        counterfactual = (f"{top[0].replaced_alone()} 원점수가 판정선보다 {lower_word}(값 {_fixed(after[0], digits)}) "
                          "머신러닝 모델 기준으로는 이상으로 보지 않았을 것입니다.")
    elif cleared:
        together = "·".join(player.mention() for player in top[:cleared])
        first = _fixed(after[0], digits)
        counterfactual = (f"{together}{josa(together, '을/를')} 함께 학습 데이터 중앙값으로 바꾸면 원점수가 판정선보다 "
                          f"{lower_word}(값 {_fixed(after[cleared - 1], digits)}) 머신러닝 모델 기준으로는 이상으로 보지 않았을 것입니다. "
                          f"{top[0].name}만 바꾸면 원점수는 {first}{josa(first, '으로/로')} 여전히 판정선 기준 이상 범위에 남습니다.")
    elif cf:
        counterfactual = (f"상위 {len(cf)}개 변수를 학습 데이터 중앙값으로 바꿔도 원점수가 판정선 기준 이상 범위에 남습니다"
                          f"(값 {_fixed(after[-1], digits)}).")

    return {"summary": summary, "notes": notes, "counterfactual": counterfactual, "limitation": LIMITATION,
            "scoreActual": score, "scoreBaseline": _finite(attribution["scoreBaseline"]), "threshold": threshold,
            "direction": attribution["direction"], "clearedAtK": cleared, "counterfactualScores": after,
            "topK": [{"playerId": p.id, "features": list(p.features), "phi": p.phi, "share": p.share,
                      "thresholdGapShare": p.gap} for p in top]}
