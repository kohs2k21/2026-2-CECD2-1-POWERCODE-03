from decimal import Decimal, InvalidOperation
from datetime import datetime
import math

def _type(column):
    return {'string': 'category', 'datetime': 'timestamp'}.get(column.get('logical_type'), column.get('logical_type', 'unknown'))

def _missing(value):
    return value is None or value == ''

def _number(value):
    if _missing(value) or type(value) is bool:
        return None
    try:
        number = Decimal(str(value))
        return number if number.is_finite() and math.isfinite(float(number)) else None
    except (InvalidOperation, ValueError, TypeError, OverflowError):
        return None

def _timestamp(value):
    if _missing(value):
        return None
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (ValueError, TypeError):
        return None

def _json_number(number):
    if number is None:
        return None
    if isinstance(number, Decimal):
        return int(number) if number == number.to_integral_value() else float(number)
    return number

def _summary(values):
    valid = [v for v in values if v is not None]
    booleans = bool(valid) and all((type(v) is bool for v in valid))
    numeric = bool(valid) and all((isinstance(v, (Decimal, int, float)) and type(v) is not bool for v in valid))
    numbers = sorted((Decimal(str(v)) for v in valid)) if numeric else []

    def percentile(p):
        if not numbers:
            return None
        position = Decimal(len(numbers) - 1) * Decimal(p)
        lower = int(position)
        upper = min(lower + 1, len(numbers) - 1)
        return numbers[lower] + (numbers[upper] - numbers[lower]) * (position - lower)
    true_count = sum((v is True for v in valid)) if booleans else None
    denominator = len(valid) if booleans else None
    ratio = Decimal(true_count) / Decimal(denominator) if booleans and denominator else None
    (p95, p99) = (percentile('0.95'), percentile('0.99'))
    result = {'row_count': len(values), 'valid_count': len(valid), 'missing_count': len(values) - len(valid), 'true_count': true_count, 'false_count': len(valid) - true_count if booleans else None, 'ratio': _json_number(ratio), 'numerator': true_count, 'denominator': denominator, 'min': _json_number(numbers[0]) if numbers else None, 'max': _json_number(numbers[-1]) if numbers else None, 'p95': _json_number(p95), 'p99': _json_number(p99), 'variation': 'unknown' if not valid else 'constant' if len(set(valid)) == 1 else 'variable', 'quantile_method': 'linear_(n-1)*p', 'exact_decimal': {'ratio': str(ratio) if ratio is not None else None, 'p95': str(p95) if p95 is not None else None, 'p99': str(p99) if p99 is not None else None}}
    return result
