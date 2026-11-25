"""IoT-SFC 배포 버전 6의 conn 행을 허용 목록으로 투영한다."""
from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

PROFILE = 'iot-sfc-v6-conn-v1'
URL = 'https://data.mendeley.com/datasets/j9xffc2psb/6'
NUMERIC = ('duration', 'orig_bytes', 'resp_bytes', 'orig_pkts', 'resp_pkts')
TEXT = ('uid', 'proto', 'service', 'conn_state', 'log_type')
FEATURES = tuple('log1p_' + name for name in NUMERIC) + ('log1p_total_bytes', 'log1p_total_pkts')
MISSING = frozenset(('', '-', '(empty)', 'nan', 'NaN', 'null', 'None'))
MAX_ROWS = 100_000
csv.field_size_limit(max(csv.field_size_limit(), 32 * 1024 * 1024))


class InputError(ValueError):
    """원문·접속 정보를 포함하지 않는 입력 오류."""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def number(value, *, integer=False):
    """결측·잘못된 값·음수를 0으로 대체하지 않는다."""
    if value is None or str(value).strip() in MISSING:
        return None
    try:
        parsed = Decimal(str(value))
        if not parsed.is_finite() or parsed < 0 or (integer and parsed != parsed.to_integral_value()):
            return None
        converted = float(parsed)
        return converted if math.isfinite(converted) else None
    except (InvalidOperation, ValueError, OverflowError):
        return None


def project(raw, row_number):
    """BODY·payload·인증값·자유문·주소를 참조하지 않고 허용한 값만 복사한다."""
    # Zeek ts는 Unix 초로 해석한다. 원문은 보존하며 naive 시각에 시간대를 추정하지 않는다.
    try:
        ts = Decimal(str(raw.get('ts', '')))
        if not ts.is_finite():
            raise ValueError()
        instant = datetime.fromtimestamp(float(ts), timezone.utc).isoformat()
    except (InvalidOperation, ValueError, OverflowError, OSError):
        raise InputError('invalid_zeek_timestamp') from None
    allowed = {key: str(raw.get(key) or '').strip() for key in TEXT}
    allowed['ts'] = str(ts)
    flags = []
    for key in NUMERIC:
        value = raw.get(key)
        # 허용한 숫자의 원문은 문자열로 보존한다. 저장 단계에서 정수를 반올림하지 않는다.
        allowed[key] = None if value is None else str(value).strip()
        parsed = number(value, integer=key != 'duration')
        if parsed is None:
            flags.append('unavailable:' + key)
    if not allowed['uid']:
        flags.append('uid_missing')
    label = str(raw.get('attack', '')).strip()
    truth = {'attack': {'0': False, '1': True}.get(label),
             'attack_type': str(raw.get('type_of_attack') or '')[:128] or None}
    if truth['attack'] is None:
        flags.append('ground_truth_unavailable')
    return {'row_number': row_number, 'source_time': instant, 'allowed': allowed,
            'quality_flags': flags, 'truth': truth}


def features(allowed):
    """모델 입력은 고정된 수치 일곱 열뿐이다. ID·시각·정답·자유문을 포함하지 않는다."""
    values = [number(allowed.get(key), integer=key != 'duration') for key in NUMERIC]
    if any(value is None for value in values):
        return None
    totals = [values[1] + values[2], values[3] + values[4]]
    if any(not math.isfinite(value) for value in totals):
        return None
    return [math.log1p(value) for value in values + totals]


def iter_rows(path: Path, limit=20_000):
    if not 1 <= limit <= MAX_ROWS:
        raise InputError('limit_out_of_range')
    with path.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream, strict=True)
        names = reader.fieldnames or []
        if len(names) != len(set(names)) or not set(('ts', 'uid', 'log_type') + NUMERIC) <= set(names):
            raise InputError('unsupported_csv_header')
        selected = 0
        try:
            for row_number, raw in enumerate(reader, 1):
                if None in raw or any(value is None for value in raw.values()):
                    raise InputError('csv_column_count_mismatch')
                if raw['log_type'].strip() != 'conn':
                    continue
                yield project(raw, row_number)
                selected += 1
                if selected >= limit:
                    break
        except csv.Error:
            raise InputError('invalid_csv') from None


def summary(rows):
    ordered = sorted(rows, key=lambda row: Decimal(row['allowed']['ts']))
    return {'rows': len(rows), 'feature_ready': sum(features(row['allowed']) is not None for row in rows),
            'labels': dict(Counter(str(row['truth']['attack']) for row in rows)),
            'source_time_min': ordered[0]['source_time'] if ordered else None,
            'source_time_max': ordered[-1]['source_time'] if ordered else None,
            'profile': PROFILE}
