"""Summarize retained Playwright JSON without changing execution classifications."""
import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('json_path', type=Path)
parser.add_argument('--main-asset', required=True)
parser.add_argument('--output', type=Path, default=Path('docs/ui-redesign/evidence/browser-results.md'))
args = parser.parse_args()
result = json.loads(args.json_path.read_text())
stats = result['stats']
rows = []
outcomes = []

def visit(suites):
    for suite in suites:
        for spec in suite.get('specs', []):
            for case in spec.get('tests', []):
                attempts = case.get('results', [])
                last = attempts[-1] if attempts else {}
                classification = case.get('status', last.get('status', 'unknown'))
                state = 'PASS' if classification == 'expected' and last.get('status') == 'passed' else classification.upper()
                outcomes.append(state)
                duration = sum(attempt.get('duration', 0) for attempt in attempts) / 1000
                filename = spec.get('file', suite.get('file', ''))
                title = spec['title'].replace('|', '\\|').replace('\n', ' ')
                source = f"[{filename}:{spec['line']}](../../../apps/web/tests/{filename}#L{spec['line']})"
                rows.append(f'| {title} | {state} | {duration:.2f}s | {source} |')
        visit(suite.get('suites', []))

visit(result['suites'])
lines = [
    '# Consolidated browser results', '',
    f"Stable production assets `{args.main_asset}`, Chromium, isolated API/worker at `http://127.0.0.1:18340`. Start {stats['startTime']}. **{outcomes.count('PASS')} passed; {stats['skipped']} skipped, {stats['unexpected']} unexpected, {stats['flaky']} flaky.** Duration {stats['duration'] / 1000:.2f} seconds.", '',
    'Every row records actual Playwright interaction in Chromium. The [acceptance matrix](../test-matrix.md) distinguishes actual API/worker execution, controlled transport faults and authored response fixtures. A browser PASS does not make a fixture into scientific execution or validate AI judgment quality.', '',
    'The [stdout](e2e-delivery.txt) and [complete JSON results](e2e-delivery.json) retain exact names, durations, results and attachment paths. Earlier diagnostics and the passing build before the final dependency/PDF corrections remain separate evidence.', '',
    '| Test name | Result | Browser duration | Source / assertions |',
    '| --- | --- | --- | --- |', *rows, '',
]
args.output.write_text('\n'.join(lines))
print(json.dumps({'rows': len(rows), 'stats': stats, 'output': str(args.output)}))
