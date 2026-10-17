"""Local monotonic durations and current process RSS samples, never container peaks."""
import os
import platform
from pathlib import Path
from trainer.metrics import measurement

VERSION = 'execution-cost-v1'


def process_rss():
    try:
        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes
            class Counters(ctypes.Structure):
                _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD)] + \
                    [(key, ctypes.c_size_t) for key in ('PeakWorkingSetSize', 'WorkingSetSize', 'QuotaPeakPagedPoolUsage',
                     'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage', 'QuotaNonPagedPoolUsage',
                     'PagefileUsage', 'PeakPagefileUsage')]
            value = Counters(); value.cb = ctypes.sizeof(value)
            ctypes.windll.kernel32.GetCurrentProcess.restype = wintypes.HANDLE
            ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
            ctypes.windll.psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
            if not ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.windll.kernel32.GetCurrentProcess(),
                                                           ctypes.byref(value), value.cb):
                return None
            return value.WorkingSetSize
        fields = Path('/proc/self/statm').read_text().split()
        return int(fields[1]) * os.sysconf('SC_PAGE_SIZE')
    except (OSError, ValueError, AttributeError):
        return None


def percentile(values, quantile):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    return ordered[lower] + (ordered[min(lower + 1, len(ordered) - 1)] - ordered[lower]) * (position - lower)


def conditions(batch_size, *, concurrency=1):
    return {'clock': 'monotonic', 'batchSize': batch_size, 'concurrency': concurrency,
            'concurrencyScope': 'job', 'platform': platform.system(), 'processCpuCount': os.cpu_count(),
            'warmCold': 'mixed_unclassified', 'samplingPeriod': 'per_batch_completion',
            'memoryScope': 'process_current_rss_sample', 'resourceLimits': 'not_observed',
            'containerPeakMeasured': False, 'totalTiming': 'handler_execution_before_atomic_publication'}


def cost_family(durations, *, batch_size, samples=(), queue_wait=None):
    metrics = {key: measurement(durations.get(key), 'ms', reason=None if durations.get(key) is not None else 'not_observed')
               for key in ('totalMs', 'preprocessMs', 'trainMs', 'evaluationMs', 'inferenceMs', 'ruleMs')}
    for key, value in [('itemP50Ms', percentile(samples, .5)), ('itemP95Ms', percentile(samples, .95)),
                       ('queueWaitMs', queue_wait)]:
        metrics[key] = measurement(value, 'ms', reason=None if value is not None else 'not_observed')
    rss = process_rss()
    metrics['processRssBytes'] = measurement(rss, 'bytes', reason=None if rss is not None else 'process_rss_unavailable')
    elapsed = durations.get('totalMs')
    metrics['throughput'] = measurement(batch_size * 1000 / elapsed if elapsed and batch_size else None,
        'rows/s', reason=None if elapsed and batch_size else 'no_timed_rows')
    metrics['esbLatencyMs'] = measurement(None, 'ms', reason='source_clock_unverified')
    metrics['batchP50Ms'] = measurement(elapsed, 'ms', reason=None if elapsed is not None else 'not_observed')
    metrics['batchP95Ms'] = metrics['batchP50Ms'].copy()
    return {'status': 'measured', 'reason': None, 'metricVersion': VERSION, 'metrics': metrics,
            'details': {**conditions(batch_size), 'batchSampleCount': 1, 'itemSampleCount': len(samples),
                        'queueWaitClock': 'database', 'queueWaitBasis': 'enqueue_to_attempt_start_including_retry'}}


def queue_wait_ms(conn, message, attempt_id):
    with conn.cursor() as cur:
        cur.execute('SELECT extract(epoch FROM (started_at - %s::timestamptz))*1000 FROM run_job_attempts WHERE id=%s',
                    (message.created_at, attempt_id))
        return max(0.0, float(cur.fetchone()[0]))
