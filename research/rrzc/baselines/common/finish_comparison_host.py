"""Detached read-only supervision through final archive verification and reporting."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

from run_comparison_host import IMAGE_DIR, detach_supervisor, atomic_json, digest
from comparison_source import BASE
from verify_comparison_host import verify
from summarize_comparison import summarize


def supervisor_alive(state):
    pid = state.get('supervisor_pid')
    if not pid: return False
    proc = Path('/proc') / str(pid)
    try:
        if (proc / 'stat').read_text().split(') ', 1)[1][0] == 'Z': return False
        return b'run_comparison_host.py' in (proc / 'cmdline').read_bytes()
    except (FileNotFoundError, ProcessLookupError): return False


def execute(root, output, timeout):
    receipt = output / 'completion-state.json'
    def record(**values):
        data = json.loads(receipt.read_text()) if receipt.exists() else {}
        data.update(values, pid=os.getpid(), checked_utc=datetime.now(timezone.utc).isoformat())
        atomic_json(receipt, data)
    record(stage='waiting', experiment=str(root), purpose='verify and summarize after all guests exit')
    deadline = time.monotonic() + timeout
    while True:
        state = json.loads((root / 'comparison-state.json').read_text())
        alive = supervisor_alive(state)
        record(controller_stage=state['stage'], completed=state.get('completed', []),
               current=state.get('current'), controller_pid=state.get('supervisor_pid'))
        if state['stage'] == 'complete_with_failures' and not alive:
            record(stage='completed_with_failed_arms', failed_arms=state.get('failed_arms', []),
                   results_claimed=False, full_workload_verified=False,
                   note='independent arms attempted; failures retained, no all-arm success or aggregate performance report')
            return
        if state['stage'] == 'failed':
            record(stage='stopped_at_controller_failure', error=state.get('error'),
                   results_claimed=False, note='retain all failed evidence; no final result synthesized')
            return
        if state['stage'] == 'complete' and not alive: break
        if not alive:
            record(stage='stopped_at_missing_controller', results_claimed=False)
            return
        if time.monotonic() >= deadline:
            record(stage='supervision_deadline', results_claimed=False,
                   note='experiment controller retains its own timeouts and guest cleanup')
            return
        time.sleep(20)
    try:
        evidence = output / '完整六臂最终核验.json'
        dependencies = json.loads((output / 'dependency-sha256.json').read_text())
        assert all(digest(Path(path)) == sha for path, sha in dependencies.items()), 'verification source changed while waiting'
        record(stage='verifying_archives')
        verify(root, evidence)
        record(stage='writing_verified_report')
        summary = output / '完整六臂结果'
        summarize(root, evidence, summary)
        proof = json.loads(evidence.read_text())
        assert proof['passed'] and proof['full_workload_verified']
        catalog = BASE / 'catalog.json'; current = json.loads(catalog.read_text())
        current.update(shared_workload_full_verified=True,
                       shared_workload_full_evidence=str(evidence.relative_to(BASE)),
                       shared_workload_full_scope='six single-run full-size 12-window arms; G2 placement with public Greedy reference; policy-dependent post-load state; no statistical claim',
                       all_methods_complete=False)
        atomic_json(catalog, current)
        record(stage='complete', evidence=str(evidence), report=str(summary),
               results_claimed=True, full_workload_verified=True, statistical_claim=False)
    except BaseException as error:
        record(stage='final_verification_failed', error=repr(error), results_claimed=False)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path); parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--detach', action='store_true'); parser.add_argument('--timeout', type=int, default=14400)
    args = parser.parse_args(); root = args.root.resolve(); output = args.output.resolve()
    assert root.parent == IMAGE_DIR and root.name.startswith('joint-related-compare-full-')
    assert output.is_relative_to(BASE / 'workspaces') and not output.exists()
    output.mkdir(parents=True)
    (output / 'supervision-source.py').write_bytes(Path(__file__).read_bytes())
    dependencies = [Path(__file__), Path(__file__).with_name('run_comparison_host.py'),
                    Path(__file__).with_name('verify_comparison_host.py'),
                    Path(__file__).with_name('summarize_comparison.py'),
                    Path(__file__).with_name('analyze_comparison.py'),
                    BASE.parent / 'analyze_end_to_end.py', BASE.parent / 'analyze_placement_integration.py']
    atomic_json(output / 'dependency-sha256.json', {str(p): digest(p) for p in dependencies})
    if args.detach and not detach_supervisor(output): return
    execute(root, output, args.timeout)


if __name__ == '__main__': main()
