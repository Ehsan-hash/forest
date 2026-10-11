"""Opt-in live policy delivery check through the signed-in local Codex CLI.

This is an operational acceptance script, not a scientific experiment or a
pytest test. It makes one real request per registered role. The input numbers
and manuscript passage are explicitly labeled synthetic behavior fixtures;
they must never be treated as study evidence. Results default to /tmp.

Run from the repository with:
    python tests/live_research_policy.py --workers 4 --timeout 180

Requires a signed-in Codex CLI that supports gpt-6-luna. It uses no external
model API, disables native tools in the CLI transport, and records subscription
usage as unpriced rather than assigning a fabricated monetary cost.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import traceback

from research.agents.policy import (
    ANTI_DEFENSIVE_RESEARCH,
    RESEARCH_FREEDOM,
    RESEARCH_POLICY_MARKER,
    ROLES,
    ensure_research_messages,
)
from research.agents.provider import ModelClient
from research.agents.codex_transport import request_payload


SYNTHETIC_FIXTURE = {
    'label': 'SYNTHETIC BEHAVIOR-VALIDATION FIXTURE; NOT A REAL EXPERIMENT OR STUDY RESULT',
    'paired_baseline': [0.70, 0.71, 0.69],
    'paired_candidate': [0.71, 0.70, 0.72],
    'paired_differences': [0.01, -0.01, 0.03],
    'n': 3,
    'conditions': 'Same hypothetical test split and training budget; seed-paired examples only.',
}
REVISION_FIXTURE = (
    'Our method may potentially improve accuracy, although it is difficult to know whether the effect matters. '
    'Unfortunately, we first tried several variants before reaching this result. '
    'The candidate scored 0.71, compared with 0.70 for the baseline, on the same split.'
)
EXPECTED_ROLES = (
    'Orchestrator', 'Researcher', 'Theorist', 'Engineer', 'Experimenter', 'Analyst',
    'Reviewer', 'Writer', 'Figure Designer', 'Literature Scout', 'Benchmark Curator',
    'Experiment Designer', 'Baseline Reproducer', 'Visual Selector', 'Layout Reviewer',
    'Submission Reviewer', 'Evidence Verifier', 'Open Source Research Advisor',
    'Research Direction Reviewer',
)


def _task(role: str) -> str:
    revision = ''
    if role == 'Writer':
        revision = (
            '\nAlso inspect this short synthetic manuscript fixture (not a real result): '
            + json.dumps(REVISION_FIXTURE)
            + '. Add an `edits` array to your JSON. Each edit must contain exact original span, integer start offset, '
            'smallest replacement, and reason; preserve the measured '
            'comparison, do not invent a stronger result, and leave sound text unchanged.'
        )
    return (
        'This is an agent-policy behavior validation request. The following values are a SYNTHETIC fixture, not '
        'observed research evidence and not a claim about any FOREST paper. As the registered ' + role + ' role, make '
        'a compact decision from these supplied values and propose a genuinely informative next test. Treat n=3 as '
        'small and noisy. Return one JSON object with keys best_estimate, probability_range, confidence, why, against, '
        'decisive_unknown, cheapest_experiment. Give specific evidence and avoid generic hedging. probability_range '
        'must be a meaningful subjective range (for example a percent interval) with one sentence of grounds. '
        'Do not claim an experiment was run or use the fixture as scientific evidence. The fixture is: '
        + json.dumps(SYNTHETIC_FIXTURE, sort_keys=True)
        + revision
        + '\nOutput valid JSON only.'
    )


def _check_response(role: str, response: str) -> dict:
    value = json.loads(response)
    required = ('best_estimate', 'probability_range', 'why', 'against',
                'decisive_unknown', 'cheapest_experiment')
    missing = [key for key in required if not isinstance(value.get(key), str) or len(value[key].strip()) < 12]
    if not isinstance(value.get('confidence'), str) or not value['confidence'].strip():
        missing.append('confidence')
    if missing:
        raise ValueError('response lacks substantive fields: ' + ', '.join(missing))
    normalized = value['confidence'].strip().lower()
    if not any(level in normalized for level in ('high', 'medium', 'low')):
        raise ValueError('confidence must name high, medium, or low')
    if not re.search(r'\d', value['probability_range']):
        raise ValueError('probability_range must contain an explicit numeric range')
    if role == 'Writer':
        edits = value.get('edits')
        if not isinstance(edits, list) or not edits:
            raise ValueError('Writer response must include a minimal exact-span edits list')
        for edit in edits:
            if not isinstance(edit, dict) or not all(isinstance(edit.get(key), str) for key in ('replacement', 'reason')):
                raise ValueError('Writer edit must include original, replacement, and reason strings')
            original = edit.get('original_span', edit.get('original'))
            offset = edit.get('start_offset', edit.get('start'))
            if not isinstance(original, str) or not original.strip() or not edit['reason'].strip():
                raise ValueError('Writer edit has an empty source span or reason')
            if not isinstance(offset, int) or offset < 0:
                raise ValueError('Writer edit start offset must be a nonnegative integer')
            if REVISION_FIXTURE[offset:offset + len(original)] != original:
                raise ValueError('Writer edit must identify an exact span in the supplied text')
            if original == REVISION_FIXTURE:
                raise ValueError('Writer must not rewrite the whole supplied paragraph')
    return value


def _run_role(role: str, output_dir: Path, timeout: int, feedback=None) -> dict:
    start = datetime.now(timezone.utc).isoformat()
    started = time.monotonic()
    events = []
    event_lock = threading.Lock()

    def observe(event):
        with event_lock:
            events.append(dict(event))
        return 'local-live-policy-check'

    provider = {
        'id': 'local-live-policy-check',
        'name': 'Signed-in local Codex CLI acceptance check',
        'kind': 'codex_cli',
        'base_url': 'http://127.0.0.1',
        'model': 'gpt-6-luna',
        'allow_paid': False,
        'config': {
            'timeout': timeout,
            'reasoning_effort': 'high',
            'max_output_tokens': 900,
            'output_stage': 'analysis',
            'allow_paid': False,
        },
    }
    config = {'role': role, 'output_stage': 'analysis', 'allow_paid': False,
              'reasoning_effort': 'high', 'max_output_tokens': 900}
    task = _task(role)
    if feedback:
        task += '\nPRIOR VALIDATION FEEDBACK (repair the response, preserve the original fixture): ' + feedback
    messages = ensure_research_messages([{'role': 'user', 'content': task}], config, 'analysis')
    effective = messages[0]['content']
    for required in (RESEARCH_POLICY_MARKER, RESEARCH_FREEDOM, ANTI_DEFENSIVE_RESEARCH):
        if required not in effective:
            raise AssertionError(f'{role} request is missing shared policy delivery')
    client = ModelClient(provider, allow_paid=False, request_guard=observe)
    record = {
        'role': role,
        'provider': {'kind': 'codex_cli', 'model': 'gpt-6-luna', 'reasoning_effort': 'high',
                     'allow_paid': False, 'external_model_api': False},
        'started_at': start,
        'request': request_payload(messages, json_mode=True),
        'policy_delivery': {'marker': RESEARCH_POLICY_MARKER,
                            'shared_freedom_present': RESEARCH_FREEDOM in effective,
                            'anti_defensive_present': ANTI_DEFENSIVE_RESEARCH in effective,
                            'writing_contract_present': ('Avoid a single semantic unit forming a paragraph alone.' in effective)},
        'synthetic_fixture_disclaimer': SYNTHETIC_FIXTURE['label'],
    }
    try:
        result = client.complete(messages, json_mode=True, max_output_tokens=900,
                                 output_stage='analysis', recover_truncation=False)
        record.update(response_text=result['text'], model=result.get('model'),
                      usage=result.get('usage'), elapsed_seconds=result.get('elapsed'),
                      execution_events=events)
        parsed = _check_response(role, result['text'])
        if role == 'Writer' and not record['policy_delivery']['writing_contract_present']:
            raise AssertionError('Writer request is missing the complete project writing instruction')
        record.update(status='completed', response=parsed)
    except Exception as exc:  # preserve the actual failure; do not create a fallback response
        record.update(status='failed', error_type=type(exc).__name__, error=str(exc),
                      traceback=traceback.format_exc(), execution_events=events,
                      elapsed_seconds=time.monotonic() - started)
    record['finished_at'] = datetime.now(timezone.utc).isoformat()
    path = output_dir / (role.lower().replace(' ', '_') + '.json')
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, path)
    return {'role': role, 'status': record['status'], 'path': str(path),
            'elapsed_seconds': record.get('elapsed_seconds'), 'usage': record.get('usage'),
            'error': record.get('error')}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='Output directory; defaults to a new directory in /tmp.')
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--timeout', type=int, default=180)
    parser.add_argument('--revalidate-existing', type=Path,
                        help='Recheck saved actual response files without making model requests.')
    parser.add_argument('--roles', nargs='+', choices=EXPECTED_ROLES,
                        help='Run selected registered roles; default is the complete roster.')
    parser.add_argument('--feedback', help='Actual prior validation failure to diagnose in a selected-role retry.')
    args = parser.parse_args(argv)
    if args.revalidate_existing:
        root = args.revalidate_existing
        records = []
        for role in EXPECTED_ROLES:
            path = root / (role.lower().replace(' ', '_') + '.json')
            if not path.is_file():
                records.append({'role': role, 'status': 'missing_record'})
                continue
            stored = json.loads(path.read_text(encoding='utf-8'))
            try:
                checked = _check_response(role, stored['response_text'])
                status = 'passed'
                error = None
            except Exception as exc:
                checked = None
                status = 'failed'
                error = type(exc).__name__ + ': ' + str(exc)
            records.append({'role': role, 'status': status, 'error': error,
                            'actual_model': stored.get('model'), 'usage': stored.get('usage'),
                            'policy_delivery': stored.get('policy_delivery'),
                            'writer_edit_count': len(checked.get('edits', [])) if checked and role == 'Writer' else None})
        result = {'status': 'completed' if len(records) == len(EXPECTED_ROLES) and all(r['status'] == 'passed' for r in records) else 'failed',
                  'actual_model_requests_replayed': False, 'model_requests_made': 0,
                  'input_directory': str(root), 'records': records,
                  'scope': 'Revalidation of saved real local CLI outputs; no new model requests and no scientific experiment evidence.'}
        destination = root / 'policy_revalidation.json'
        destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'status': result['status'], 'passed': sum(r['status'] == 'passed' for r in records),
                          'requested': len(records), 'result': str(destination)}, ensure_ascii=False))
        return 0 if result['status'] == 'completed' else 1
    if args.workers < 1 or args.workers > 4:
        parser.error('--workers must be between 1 and 4')
    if args.timeout < 1 or args.timeout > 180:
        parser.error('--timeout must be between 1 and 180 seconds')
    if len(ROLES) != len(EXPECTED_ROLES) or set(ROLES) != set(EXPECTED_ROLES):
        parser.error('active role registry changed; update EXPECTED_ROLES before validating')
    selected_roles = list(dict.fromkeys(args.roles or EXPECTED_ROLES))
    executable = shutil.which(os.environ.get('FOREST_CODEX_EXECUTABLE', 'codex'))
    if not executable:
        parser.error('Codex CLI is unavailable; install and sign in before running this opt-in check')
    version = subprocess.run([executable, '--version'], capture_output=True, text=True,
                             timeout=10, check=False).stdout.strip()
    output_dir = args.output or Path(tempfile.mkdtemp(prefix='forest-live-policy-', dir='/tmp'))
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / 'run.json').write_text(json.dumps({
        'kind': 'live_policy_delivery_acceptance',
        'scientific_experiment': False,
        'fixture_status': SYNTHETIC_FIXTURE['label'],
        'model': 'gpt-6-luna', 'reasoning_effort': 'high', 'allow_paid': False,
        'external_model_api': False, 'codex_executable': executable, 'codex_version': version,
        'workers': args.workers, 'per_request_timeout_seconds': args.timeout,
        'roles': selected_roles, 'validation_feedback': args.feedback,
        'started_at': datetime.now(timezone.utc).isoformat(),
    }, ensure_ascii=False, indent=2), encoding='utf-8')

    results = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_run_role, role, output_dir, args.timeout, args.feedback): role for role in selected_roles}
        for future in as_completed(futures):
            role = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {'role': role, 'status': 'failed_before_request',
                          'error_type': type(exc).__name__, 'error': str(exc)}
            results.append(result)
            with (output_dir / 'execution.jsonl').open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(result, ensure_ascii=False) + '\n')
            print(json.dumps(result, ensure_ascii=False), flush=True)

    summary = {'status': 'completed' if all(item['status'] == 'completed' for item in results) else 'failed',
               'actual_requests': len(results), 'completed': sum(item['status'] == 'completed' for item in results),
               'failed': [item for item in results if item['status'] != 'completed'],
               'results': results, 'finished_at': datetime.now(timezone.utc).isoformat(),
               'evidence_scope': 'Live model response behavior and policy delivery only; synthetic prompt inputs are not scientific evidence.'}
    (output_dir / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'output_dir': str(output_dir), 'status': summary['status'],
                      'completed': summary['completed'], 'requested': summary['actual_requests']}, ensure_ascii=False))
    return 0 if summary['status'] == 'completed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
