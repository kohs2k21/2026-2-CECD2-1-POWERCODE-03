"""공개 ZIP의 제한된 범위에서 허용 필드만 남긴 작은 시험 CSV를 만든다."""
from __future__ import annotations

import csv
import io
import json
import struct
import urllib.error
import urllib.request
import zlib
from pathlib import Path

from .adapter import InputError, NUMERIC, TEXT, PROFILE, URL, project, canonical

FILE_URL = ('https://data.mendeley.com/public-files/datasets/j9xffc2psb/files/'
            '7491fc3c-d470-4377-92e3-1e49fc267a38/file_downloaded')
PREFIX_BYTES = 1024 * 1024
MAX_EXPANDED = 32 * 1024 * 1024


def make_sample(output: Path, *, prefix: Path | None = None, limit=20_000):
    if not 1 <= limit <= 20_000:
        raise InputError('sample_limit_out_of_range')
    if output.exists():
        raise InputError('output_already_exists')
    if prefix:
        if prefix.stat().st_size > PREFIX_BYTES:
            raise InputError('zip_prefix_too_large')
        content = prefix.read_bytes()
    else:
        request = urllib.request.Request(FILE_URL, headers={
            'Range': f'bytes=0-{PREFIX_BYTES - 1}',
            # 공개 파일 서버가 기본 Python 사용자 에이전트를 거절하는 경우에 대비한다.
            'User-Agent': 'Mozilla/5.0 POWERCODE-IoT-SFC/1.0',
        })
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                if response.status != 206 or not response.headers.get('Content-Range', '').startswith('bytes 0-'):
                    raise InputError('server_did_not_honor_range')
                content = response.read(PREFIX_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise InputError(f'download_http_{exc.code}') from None
        if len(content) > PREFIX_BYTES:
            raise InputError('range_too_large')
    if len(content) < 30:
        raise InputError('invalid_zip_prefix')
    header = struct.unpack_from('<IHHHHHIIIHH', content)
    if header[0] != 0x04034B50 or header[3] != 8 or header[2] & 1:
        raise InputError('unsupported_zip_entry')
    name_length, extra_length = header[-2:]
    if content[30:30 + name_length] != b'iot_dataset_complete.csv':
        raise InputError('unexpected_zip_entry')
    start = 30 + name_length + extra_length
    data = zlib.decompressobj(-15).decompress(content[start:], MAX_EXPANDED + 1)
    if len(data) > MAX_EXPANDED:
        raise InputError('expanded_prefix_too_large')
    # 부분 수신한 마지막 줄은 버린다. 중간에 잘린 인용 필드는 strict CSV 판독기가 거절한다.
    text = data.rsplit(b'\n', 1)[0].decode('utf-8-sig')
    reader = csv.DictReader(io.StringIO(text), strict=True)
    rows = []
    try:
        for index, raw in enumerate(reader, 1):
            if None in raw or any(value is None for value in raw.values()):
                raise InputError('incomplete_prefix_row')
            if raw.get('log_type') == 'conn':
                rows.append(project(raw, index))
            if len(rows) >= limit:
                break
    except csv.Error:
        raise InputError('prefix_ends_inside_csv_row') from None
    if len(rows) < limit:
        raise InputError('prefix_has_too_few_conn_rows')
    output.mkdir(parents=True)
    # CSV도 허용 항목만으로 다시 만든다. 원본 ZIP·payload·인증정보는 산출물에 쓰지 않는다.
    fields = ('ts',) + TEXT + NUMERIC + ('attack', 'type_of_attack')
    with (output / 'sample.csv').open('x', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({**row['allowed'], 'attack': {False: '0', True: '1', None: ''}[row['truth']['attack']],
                             'type_of_attack': row['truth']['attack_type'] or ''})
    metadata = {'profile': PROFILE, 'source': URL, 'download_url': FILE_URL, 'rows': len(rows),
                'compressed_prefix_bytes': len(content), 'sampling': 'first-conn-rows-of-zip-prefix',
                'full_archive_integrity_verified': False, 'representative_sample': False}
    (output / 'sample-manifest.json').write_text(canonical(metadata) + '\n', encoding='utf-8')
    return metadata
