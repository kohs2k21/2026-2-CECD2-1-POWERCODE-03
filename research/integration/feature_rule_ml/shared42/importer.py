"""Explicit metadata projection from current same-schema CSVs, preserving originals."""
from pathlib import Path
import csv

from ..common import InputFailure, digest, new_dir, sha, write
from .artifacts import DEFAULT, profile
from .capture import Capture, FORBIDDEN


def capture_csv(transactions, processes, messages, output, start, end, split,
                data_class, complete_family_capture=False, accept_frozen_reference=False,
                confirm_same_hr_contract=False, held_lineage_excluded=False, release=DEFAULT):
    if not complete_family_capture or not accept_frozen_reference or not held_lineage_excluded:
        raise InputFailure('Explicit completeness, held-lineage exclusion and frozen research reference declarations required')
    if not start < end or (start < '2026-10-03' and end > '2026-09-30'):
        raise InputFailure('Invalid period or held September30–October2 overlap; source not read')
    if split not in ('train','calibration','threshold','test','application') or data_class not in ('approved_real','controlled'):
        raise InputFailure('Explicit supported split and data class required')
    _,p = profile(release)
    root = new_dir(output)
    d = p['definition']
    entries,sources = {},[]
    for short, source in zip('TPM',(transactions,processes,messages)):
        source = Path(source).resolve()
        if any(x in str(source).lower() for x in ('data_1002','data_0930','data_1001','pending_approval')):
            raise InputFailure('Held/unconfirmed source path; original data remain untouched')
        original_sha = sha(source)
        spec = d['schema']['tables'][short]
        fields = [c['name'] for c in spec['columns'] if not c['name'].startswith('__')]
        dest = root/(short+'.csv')
        with source.open(newline='',encoding='utf-8-sig') as src, dest.open('w',newline='') as dst:
            reader=csv.DictReader(src)
            header=reader.fieldnames or []
            if len(header) != len(set(header)) or set(fields)-set(header):
                raise InputFailure('Missing or duplicate physical columns; explicit schema adaptation required')
            extra = set(header)-set(fields)-{'__SOURCE_ROW_ID','__RETRY_LEXICAL'}
            if any(not FORBIDDEN.search(c) for c in extra):
                raise InputFailure('Unknown nonmetadata column; do not silently guess schema: '+','.join(sorted(extra)))
            # Preserve preexisting lexical virtual keys when supplied; otherwise
            # Capture constructs them from the original ordinal and RETRY_COUNT.
            selected = fields+[c for c in ('__SOURCE_ROW_ID','__RETRY_LEXICAL') if c in header]
            writer=csv.DictWriter(dst,fieldnames=selected);writer.writeheader()
            n=0
            for row in reader:
                if None in row or any(v is None for v in row.values()):
                    raise InputFailure('Malformed source CSV record')
                writer.writerow({k:row[k] for k in selected});n+=1
        if sha(source) != original_sha:
            raise InputFailure('Source changed during metadata projection')
        entries[short] = dict(file=dest.name,sha256=sha(dest),row_count=n)
        sources.append(dict(table=short,sha256=original_sha,excluded_columns=sorted(extra),Body_values_copied=False))
    path=root/'capture.json'
    write(path,dict(contract='shared42-capture1.0',definition_sha=p['definition_sha'],schema_sha=digest(d['schema']),admission=dict(approved=True,Body_excluded=True,held_lineage_excluded=True,complete_family_capture=True,frozen_research_reference=True),data_class=data_class,period=dict(start_inclusive=start,end_exclusive=end,timezone='source wall-clock; operational timezone unconfirmed'),split=split,lineage_tags=['explicit_same_schema_metadata_projection'],confirmed_hr_evidence_ids=sorted({e for r in d['rules'] if r['role']=='HR' for e in r['evidence_ids']}) if confirm_same_hr_contract else [],tables=entries,original_source_provenance=sources))
    Capture(path,p).assert_unchanged()
    return dict(capture=str(path),row_counts={k:v['row_count'] for k,v in entries.items()},Body_values_copied=False)
