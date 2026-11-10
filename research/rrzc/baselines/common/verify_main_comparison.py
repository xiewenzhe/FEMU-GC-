"""Read-only verification of the user-selected main arms; literal stays a recorded failure."""
import hashlib
import json
from pathlib import Path
import tarfile

from run_comparison_host import IMAGE_DIR, closed_archive, digest, atomic_json, admit_build
from comparison_source import BASE
from analyze_comparison import analyze_arm, compare
from cache_config_guard import check as cache_check
from comparison_failure import failure_receipt

WORK = BASE / 'workspaces/comparison-20261109-v1'
PLAN = WORK / '主对照口径-20261110.json'
EVIDENCE = WORK / '主对照五臂完整输入核验.json'
ORIGINAL = IMAGE_DIR / 'joint-related-compare-full-v2-20261109-results'
OAZA = IMAGE_DIR / 'joint-related-compare-bootstrap-oaza-v2-20261109-results'
COLD = IMAGE_DIR / 'joint-related-compare-bootstrap-coldmap-v1-20261110-results'
SURVIVAL = IMAGE_DIR / 'joint-related-coldmap-survival-v2-20261110-results'
LITERAL = IMAGE_DIR / 'joint-related-oaza-literal-v3-20261110-results'


def read(path): return json.loads(path.read_text())


def read_plan(path=PLAN):
    plan = read(path)
    assert plan['oaza_primary_variant'] == 'adapted'
    assert plan['oaza_literal_role'] == 'ambiguity_and_failure_analysis'
    assert plan['main_arms'] == ['g2', 'caza', 'oaza-adapted', 'cold-printed', 'cold-survival']
    assert plan['retained_failure_arm'] == 'oaza-literal'
    assert plan['preserve_original_sources_and_archives'] and not plan['rerun_successful_experiments']
    assert not plan['formal_joint_model_comparison'] and not plan['statistical_performance_claim']
    return plan


def archive_files(parent):
    state, archive = closed_archive(parent)
    assert read(parent / '阶段归档.json')['业务成功']
    lease = read(parent / '镜像生命周期.json')
    assert lease['status'] == 'deleted' and not Path(lease['image']).exists()
    assert not (parent / 'payload.tar.gz').exists()
    folder = parent / state['标签']; files = {}
    with tarfile.open(archive, 'r|gz') as tar:
        prefix = state['标签'] + '/'
        for member in tar:
            if not member.name.startswith(prefix) or not member.isfile(): continue
            name = member.name[len(prefix):]
            assert name and not Path(name).is_absolute() and '..' not in Path(name).parts
            local = folder / name
            assert local.is_file() and not local.is_symlink() and name not in files
            h = hashlib.sha256()
            with tar.extractfile(member) as stream:
                for block in iter(lambda: stream.read(1 << 20), b''): h.update(block)
            assert h.hexdigest() == digest(local), str(local)
            files[name] = h.hexdigest()
    return folder, dict(path=str(archive), sha256=digest(archive), files=files,
                        result_directory=str(parent), guest_exited=True, image_reclaimed=True)


def verify():
    assert not EVIDENCE.exists(), 'refuse overwrite'
    plan = read_plan()
    original_build = IMAGE_DIR / 'joint-related-compare-build-v2-20261109-results'
    builds = dict(caza=admit_build(original_build, 'caza')[1],
                  oaza_gc=admit_build(OAZA / 'build', 'oaza_gc')[1],
                  coldmap=admit_build(COLD / 'build', 'coldmap')[1])
    cases = [
        ('g2', 'caza', None, ORIGINAL / 'g2', 'caza-v6-20261107'),
        ('caza', 'caza', 'native', ORIGINAL / 'caza', 'caza-v6-20261107'),
        ('oaza-adapted', 'oaza_gc', 'adapted', OAZA / 'oaza-adapted', 'oaza-gc-v9-20261109'),
        ('cold-printed', 'coldmap', 'printed', COLD / 'cold-printed', 'coldmap-v6-20261109'),
        ('cold-survival', 'coldmap', 'artifact-survival', SURVIVAL / 'cold-survival', 'coldmap-v6-20261109')]
    assert [row[0] for row in cases] == plan['main_arms']
    reports = {}; archives = {}; folders = {}; comparisons = {}
    for arm, method, variant, parent, tree in cases:
        print('verifying ' + arm, flush=True)
        folder, archive = archive_files(parent); folders[arm] = folder
        assert read(folder / 'comparison_plan.json') == dict(arm=arm, method=method, variant=variant, windows=12)
        assert digest(folder / 'comparison_db_bench') == builds[method]['binary_sha256']
        identity = read(folder / 'overlay_identity.json')
        assert identity['source_sha256'] == read(BASE / 'workspaces' / tree / 'overlay_identity.json')['source_sha256']
        report = analyze_arm(folder, 12)
        assert json.loads(json.dumps(report)) == read(parent / 'comparison-audit.json')
        assert report['arm'] == arm and report['coldmap_deferred'] == report['oaza_deferred'] == 0
        safety = {}
        for phase in ('load', 'run', 'verify', 'reopen'):
            assert (folder / (phase + '.exit')).read_text().strip() == '0'
            env = read(folder / (phase + '.command.json'))['env']
            if method == 'oaza_gc': assert env['OAZA_GC_VARIANT'] == 'adapted'
            if method == 'coldmap': assert env['COLDMAP_FACTOR_DIRECTION'] == variant
            if arm == 'g2': assert env['ZENFS_RRZC_PLACEMENT_MODE'] == 'G2'
            else: assert env['ZENFS_RELATED_WORK'] == method
            snapshot = read(folder / (phase + '-end.json'))
            assert snapshot['strict']['历史最高RC'] <= 1024 and all(b['RC'] <= 1024 for b in snapshot['blocks'])
            assert report['trace_receipts'][phase]['原生映射核验次数'] > 0
            safety[phase] = dict(highest_rc=snapshot['strict']['历史最高RC'],
                cache=cache_check((folder / (phase + '.rocksdb.log')).read_text(), 1))
        reports[arm] = report
        archives[arm] = dict(**archive, source_revision=tree, binary_sha256=builds[method]['binary_sha256'], safety=safety)
        if arm != 'g2': comparisons[arm] = compare(folders['g2'], folder, reports['g2'], report)
    failure = failure_receipt(LITERAL / 'oaza-literal',
        dict(arm='oaza-literal', method='oaza_gc', variant='literal', windows=12))
    assert not failure['passed'] and not failure['full_workload_verified']
    assert failure == read(LITERAL / 'literal-failure-audit.json')
    frozen = {}
    for tree in ('caza-v6-20261107', 'oaza-gc-v7-20261109', 'coldmap-v5-20261109',
                 'oaza-gc-v9-20261109', 'coldmap-v6-20261109'):
        source = BASE / 'workspaces' / tree; identity = read(source / 'overlay_identity.json')
        assert all(digest(source / 'src/rocksdb' / n) == sha for n, sha in identity['source_sha256'].items())
        frozen[tree] = len(identity['source_sha256'])
    repo = BASE.parents[2]; four = repo / 'research/rrzc/results/placement-ablation-20261105/阶段四'
    four_hashes = read(four / '交付文件SHA256.json')
    assert all(digest(four / n) == sha for n, sha in four_hashes.items())
    five = read(BASE / 'workspaces/caza-v6-20261107/本轮设备核验.json')['stage_five_current_hashes']
    assert all(digest(repo / n) == sha for n, sha in five.items())
    result = dict(passed=True, selection=plan, selection_sha256=digest(PLAN), reports=reports,
        archives=archives, cross_arm_input=comparisons, builds=builds,
        literal_failure_reference=dict(path=str(LITERAL / 'literal-failure-audit.json'),
            sha256=digest(LITERAL / 'literal-failure-audit.json'), last_successful_flush_batch=failure['last_successful_flush_batch'],
            passed=False, role=plan['oaza_literal_role']), frozen_source_files=frozen,
        stage_four_unchanged_files=len(four_hashes), stage_five_unchanged_files=len(five),
        selected_main_full_input_verified=True, all_methods_complete=False,
        formal_joint_model_comparison=False, statistical_performance_claim=False,
        boundaries=['one full input per arm; explicit recovery/batch adaptations',
            'G2 placement with public valid-byte Greedy GC; full Credit/Score model not enabled',
            'OAZA GC 15%, others 20%; no same-trigger score isolation',
            'empty-device semantics equal; post-load layout and RC may differ',
            'CRC32C is not bytewise latest-version evidence; no atomic NAND checkpoint',
            'ColdMap nontrivial simulated compaction absent in these full inputs',
            'native GC/RR reset totals count source Zones empty at return, not Reset commands'])
    atomic_json(EVIDENCE, result); print(EVIDENCE, flush=True)


if __name__ == '__main__': verify()
