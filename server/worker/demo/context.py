"""Optional T/M metadata capture. Does not guess parents or store message content."""
from itertools import islice
from pathlib import Path
from collector.replay_csv import CsvInput, iter_csv_rows
from collector.storage import ingest_observation


def copy_csv_context(conn, source_id, run_id, transaction_csv, body_csv, limit):
    counts = {'T': 0, 'M': 0}
    for kind, path in (('T', transaction_csv), ('BODY', body_csv)):
        if not path:
            continue
        for row in islice(iter_csv_rows(CsvInput(kind, Path(path))), limit):
            raw = dict(row.allowed)
            raw.pop('response_message', None)
            # No execution FK: these files do not prove a shared execution/revision contract.
            with conn.transaction():
                ingest_observation(conn, source_id=source_id, layer=row.layer,
                    dedup_key=f'demo-context:{run_id}:{row.layer}:{row.row_number}', raw=raw)
            counts[row.layer] += 1
    return counts
