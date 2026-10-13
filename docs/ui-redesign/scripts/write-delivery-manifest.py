"""Index the saved delivery evidence and current local source; no execution claims inferred."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = ROOT / 'docs/ui-redesign/evidence'


def git(*args: str) -> str:
    return subprocess.check_output(['git', *args], cwd=ROOT).decode().strip()


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    changed_path = EVIDENCE / 'changed-files.tsv'
    source_path = EVIDENCE / 'source-sha256.tsv'
    manifest_path = EVIDENCE / 'run-manifest.json'
    for path in (changed_path, source_path, manifest_path):
        if not path.exists():
            path.write_text('')
    tracked = git('diff', '--name-only', 'HEAD').splitlines()
    added = git('ls-files', '--others', '--exclude-standard').splitlines()
    paths = sorted(set(tracked + added))
    added_set = set(added)
    rows = [(('ADDED' if path in added_set else 'MODIFIED'), path) for path in paths]
    changed_path.write_text('status\tpath\n' + ''.join(f'{status}\t{path}\n' for status, path in rows))
    source_roots = ('apps/web/', 'services/', 'research/', 'packages/contracts/', 'tests/')
    sources = [path for path in paths if path.startswith(source_roots) and (ROOT / path).is_file()]
    source_path.write_text('sha256\tbytes\tpath\n' + ''.join(
        f'{digest(ROOT / path)}\t{(ROOT / path).stat().st_size}\t{path}\n' for path in sources))
    browser = json.loads((EVIDENCE / 'e2e-delivery.json').read_text())
    units = json.loads((EVIDENCE / 'unit-delivery.json').read_text())
    smoke = json.loads((EVIDENCE / 'analysis-worker-smoke.json').read_text())
    bundle = json.loads((EVIDENCE / 'bundle-comparison.json').read_text())
    assets = ['index-BIrSAtO6.js', 'WorkspacePage-BMJxELRb.js', 'index-CVqPoWfS.css']
    asset_rows = [{'path': f'apps/web/dist/assets/{name}', 'bytes': (ROOT / 'apps/web/dist/assets' / name).stat().st_size,
                   'sha256': digest(ROOT / 'apps/web/dist/assets' / name)} for name in assets]
    manifest = {
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'baseline_commit': 'ff16c3f656f94d053d1bba65c6b3a07a5feaccde',
        'head_at_manifest_generation': git('rev-parse', 'HEAD'), 'branch': git('branch', '--show-current'),
        'source_state': 'Source fingerprint recorded before delivery commit; source-sha256.tsv identifies the included changed source',
        'source_manifest': 'source-sha256.tsv', 'source_manifest_sha256': digest(source_path),
        'changed_files': {'count': len(paths), 'status_counts': dict(Counter(status for status, _ in rows)),
                          'png_count': sum(path.endswith('.png') for path in paths), 'index': 'changed-files.tsv'},
        'production_build': {'status': 'PASS', 'log': 'build-delivery.txt', 'assets': asset_rows,
                             'condition': 'TypeScript + Vite6.4.3; final assets held during all delivery tests'},
        'runtime': {'origin': 'http://127.0.0.1:18340', 'data_directory': '/tmp/forest-redesign-runtime-20261013',
                    'condition': 'Independent test projects, SQLite, files and ordinary worker on macOS'},
        'browser': {'result': 'PASS', **browser['stats'], 'cases': 'browser-results.md',
                    'json': 'e2e-delivery.json', 'log': 'e2e-delivery.txt',
                    'condition': 'Actual API/worker plus explicitly identified authored UI/protocol/transport fixtures; one Chromium project'},
        'units': {'result': 'PASS', 'passed': units['numPassedTests'], 'failed': units['numFailedTests'],
                  'pending': units['numPendingTests'], 'source_files': len(units['testResults']),
                  'reported_duration_ms': 977, 'cases': 'unit-results.md', 'json': 'unit-delivery.json', 'log': 'vitest-delivery.txt'},
        'targeted_backend': {'passed': 35, 'skipped': 2, 'failed': 0, 'duration_seconds': 64.33,
                             'cases': '../backend-analysis-results.md', 'log': 'analysis-integration-tests-final.txt',
                             'skip_reason': 'FOREST_TEST_POSTGRES unset; dedicated PostgreSQL unavailable'},
        'analysis_worker': {'completed_scopes': 3, 'actual_scientific_runs': 2, 'provider_protocol_requests': 3,
                            'evidence': 'analysis-worker-smoke.json', 'log': 'analysis-worker-smoke.txt',
                            'transport': 'Actual API/scheduler/worker/ModelClient HTTP; authored loopback provider model prose',
                            'literature': 'Actual Crossref metadata retrieval, two catalog records per scope',
                            'provider_cleanup': smoke['provider_cleanup'],
                            'post_smoke_layout': 'analysis-visual-layout.json'},
        'baseline_backend': {'passed': 1429, 'skipped': 71, 'failed': 63, 'duration_seconds': 1572.60,
                             'log': 'baseline-pytest.txt', 'classification': '../backend-baseline-failures.json',
                             'status': 'Not a clean full-backend PASS; host prerequisites and repaired skill-catalog assertion are classified'},
        'performance': {'initial_js_reduction_percent': bundle['initial_js_reduction_percent'],
                        'initial_js_gzip_reduction_percent': bundle['initial_js_gzip_reduction_percent'],
                        'evidence': 'bundle-comparison.json', 'FCP_before_after': 'NOT RUN'},
        'visual_review': {'index': '../visual-comparison.md', 'analysis': '../analysis-pixel-review-final.json',
                          'states': ['../state-pixel-review-final.json', '../state-pixel-review-coverage-final.json'],
                          'capture_counts': {'main_route_observations': 156, 'main_additional_panels': 4,
                                             'analysis': 48, 'expanded': 30, 'readability_pages': 36, 'path_dialogs': 12, 'states': 32},
                          'condition': 'Measured browser observations and exact opened-pixel scope remain separately recorded'},
        'NOT_RUN': ['Live AI scientific quality', 'Scientific entailment of full-text citations', 'Live arXiv search',
                    'Linux Bubblewrap Agent/human-decision consumption', 'Dedicated PostgreSQL variants',
                    'Complete screen-reader/native keyboard/WCAG conformance', 'FCP before/after',
                    'Large graph pan/edit frame benchmarks', 'Large PDF buffer startup/memory benchmarks'],
        'acceptance_matrix': '../test-matrix.md',
        'acceptance_counts': {'original_56': {'scoped_PASS': 35, 'PARTIAL': 18, 'MOCK_UI_REAL_NOT_RUN': 2, 'NOT_RUN': 1},
                              'analysis_8': {'PASS': 6, 'PARTIAL': 1, 'NOT_RUN': 1}},
        'final_report': '../final-report.md',
        'interpretation': 'Counts index executed cases only; repeated targets are not additional unique tests. Integration evidence is not scientific validation.',
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps({'changed_files': len(paths), 'source_files': len(sources), 'browser_passed': browser['stats']['expected'],
                      'unit_passed': units['numPassedTests'], 'manifest': str(manifest_path)}))


if __name__ == '__main__':
    main()
