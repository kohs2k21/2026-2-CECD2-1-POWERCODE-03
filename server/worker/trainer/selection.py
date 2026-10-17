"""Training selection is independent of immutable evaluation membership."""
VERSION = 'train-success-only-v1'


def select_training(rows):
    selected, counts = [], {}
    anomaly_count = 0
    for row in rows:
        status = row[1].get('status')
        key = status if isinstance(status, str) and status else '<missing>'
        counts[key] = counts.get(key, 0) + 1
        label = row[3].get('label') if isinstance(row[3], dict) else None
        if status == 'S' and label != 'anomaly':
            selected.append(row)
        elif status == 'S':
            anomaly_count += 1
    return selected, {'policyVersion': VERSION, 'split': 'train', 'populationCount': len(rows),
                      'selectedCount': len(selected), 'excludedCount': len(rows) - len(selected),
                      'excludedExplicitAnomalyCount': anomaly_count, 'statusCounts': counts,
                      'normalGroundTruth': False}
