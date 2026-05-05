"""telemetry — append-only JSONL logger for harness invocations.

Every harness (brain-distill, brain-distill-review, lucid, neumann, fusion, ...)
emits one JSON record per invocation. Records are the substrate the harness-audit
skill reads to score failure clusters and propose patches.

Usage:

    from telemetry import invocation
    with invocation('brain-distill') as t:
        t.input(items=len(new_items), concepts=len(catalog))
        # ... do work ...
        t.output(touched_concepts=len(touches), entries_written=N)
        # outcome defaults to 'success'; use t.outcome('failure', error='...') on errors

The context manager auto-records ts, uuid, latency_ms, outcome (success/failure
on exception), and writes the record on exit.
"""
import json, os, time, uuid
from datetime import datetime, timezone
from pathlib import Path
from contextlib import contextmanager

TELEMETRY_DIR = Path(os.environ.get('COYWOLF_TELEMETRY_DIR', str(Path.home() / '.coywolf/telemetry')))


class _Invocation:
    def __init__(self, harness):
        self.harness = harness
        self.invocation_id = str(uuid.uuid4())
        self._t0 = time.monotonic()
        self._input = {}
        self._output = {}
        self._outcome = 'success'
        self._error = None
        self._skill_invocations = []
        self._extra = {}

    def input(self, **kwargs):
        self._input.update(kwargs)

    def output(self, **kwargs):
        self._output.update(kwargs)

    def outcome(self, label, error=None):
        if label not in ('success', 'failure', 'partial', 'no-op'):
            raise ValueError(f'invalid outcome: {label}')
        self._outcome = label
        if error is not None:
            self._error = str(error)

    def skill(self, name, outcome, latency_ms, error=None):
        rec = {'name': name, 'outcome': outcome, 'latency_ms': latency_ms}
        if error:
            rec['error'] = str(error)
        self._skill_invocations.append(rec)

    def extra(self, **kwargs):
        self._extra.update(kwargs)

    def _record(self, exc_type, exc_val):
        latency_ms = int((time.monotonic() - self._t0) * 1000)
        if exc_type is not None and self._outcome == 'success':
            self._outcome = 'failure'
            self._error = f'{exc_type.__name__}: {exc_val}'
        rec = {
            'ts': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'harness': self.harness,
            'invocation_id': self.invocation_id,
            'outcome': self._outcome,
            'latency_ms': latency_ms,
            'input': self._input,
            'output': self._output,
            'error': self._error,
            'skill_invocations': self._skill_invocations,
            **self._extra,
        }
        TELEMETRY_DIR.mkdir(parents=True, exist_ok=True)
        path = TELEMETRY_DIR / f'{self.harness}.jsonl'
        with open(path, 'a') as f:
            f.write(json.dumps(rec) + '\n')


@contextmanager
def invocation(harness):
    """Context manager that records a single harness invocation."""
    t = _Invocation(harness)
    try:
        yield t
    except Exception as e:
        t._record(type(e), e)
        raise
    else:
        t._record(None, None)


def query(harness, since=None, outcome=None):
    """Iterate records for a harness, optionally filtered."""
    path = TELEMETRY_DIR / f'{harness}.jsonl'
    if not path.exists():
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if since and rec.get('ts', '') < since:
                continue
            if outcome and rec.get('outcome') != outcome:
                continue
            yield rec
