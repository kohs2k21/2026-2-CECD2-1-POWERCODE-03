"""소프트룰 임계값 문자열 규칙. rule-set.md 저장 검증과 llm-recommendation-job.md `threshold` 절의 Python 구현."""
from __future__ import annotations

import re
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Context, Decimal, InvalidOperation
from typing import Any

SIGNIFICANT_DIGITS_MAX = 15
THRESHOLD_MAX_LENGTH = 40
DSL_NUMBER = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?")
# float 최대값(309자리)도 quantize가 거부하지 않을 정밀도. 기본 문맥 28자리는 1e30에서 실패한다.
_WIDE = Context(prec=800)


class ThresholdSerializationError(ValueError):
    def __init__(self) -> None:
        super().__init__("threshold_serialization_failed")


def _decimal(value: Any) -> Decimal:
    if value is None or isinstance(value, bool):
        raise ThresholdSerializationError()
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise ThresholdSerializationError() from exc
    if not result.is_finite():
        raise ThresholdSerializationError()
    return result


def _plain(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def _significant(value: Decimal, digits: int, rounding: str) -> Decimal:
    if value.is_zero():
        return Decimal(0)
    return value.quantize(Decimal(1).scaleb(value.adjusted() - digits + 1), rounding=rounding, context=_WIDE)


def _places(value: Decimal, digits: int, rounding: str) -> Decimal:
    return value.quantize(Decimal(1).scaleb(-digits), rounding=rounding, context=_WIDE)


def exact_text(value: Any) -> str:
    """원값을 줄이지 않은 고정소수 문자열. `str(3.125e-05)`의 지수 표기를 피한다."""
    return _plain(_decimal(value))


def threshold_valid(text: str, value_type: str = "number") -> bool:
    """저장 검증과 같은 조건: 40자·DSL 정규식·유효숫자 15자리·정수 필드 정수·number 왕복 불변."""
    if len(text) > THRESHOLD_MAX_LENGTH or not DSL_NUMBER.fullmatch(text):
        return False
    if value_type == "integer" and "." in text:
        return False
    digits = text.lstrip("-").replace(".", "").lstrip("0").rstrip("0")
    if len(digits) > SIGNIFICANT_DIGITS_MAX:
        return False
    # 화면·저장은 JSON number로 다루므로 number를 거쳐도 같은 문자열로 돌아와야 한다.
    return exact_text(float(text)) == text


def fixed_threshold(value: Any, value_type: str = "number", display_precision: int = 0,
                    precision_mode: str | None = None) -> str:
    """유효숫자 15자리(ROUND_HALF_EVEN)로 줄인 고정소수 문자열. 실패는 ThresholdSerializationError."""
    source = _decimal(value)
    rounded = _significant(source, SIGNIFICANT_DIGITS_MAX, ROUND_HALF_EVEN)
    if value_type == "integer":
        # 15자리로 줄인 값을 ROUND_HALF_UP으로 정수로 만든다(계약 `threshold` 절).
        rounded = _places(rounded, 0, ROUND_HALF_UP)
    text = _plain(rounded)
    if threshold_valid(text, value_type):
        return text
    # 계약: 40자 초과는 필드 displayPrecision+6 자리로 줄여 한 번 더 시도한다.
    digits = display_precision + 6
    retried = _significant(source, digits, ROUND_HALF_EVEN) if precision_mode == "significant" \
        else _places(source, digits, ROUND_HALF_EVEN)
    if value_type == "integer":
        retried = _places(retried, 0, ROUND_HALF_UP)
    text = _plain(retried)
    # 0이 아닌 값이 재시도 자릿수에서 사라지면 "모든 값이 초과"라는 다른 임계값이 된다.
    if threshold_valid(text, value_type) and (text != "0" or source.is_zero()):
        return text
    raise ThresholdSerializationError()


def display_threshold(value: Any, display_precision: int = 0, precision_mode: str | None = None) -> str:
    """화면 표시 문자열. 계약 예시(7210.5 → 7211)에 맞춰 표시값만 ROUND_HALF_UP을 쓴다."""
    source = _decimal(value)
    rounded = _significant(source, display_precision, ROUND_HALF_UP) if precision_mode == "significant" \
        else _places(source, display_precision, ROUND_HALF_UP)
    text = _plain(rounded)
    return text if len(text) <= THRESHOLD_MAX_LENGTH else _plain(source)
