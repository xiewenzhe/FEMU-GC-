"""Preserve a closed failed workload as a failure, never as a completed arm."""
import hashlib
import gzip
import json
from pathlib import Path
import re
import sys
import tarfile

sys.path[:0] = [str(Path(__file__).resolve().parents[2] / 'tests'),
                str(Path(__file__).resolve().parents[2])]
from result_storage import closed_archive


def failure_receipt(parent, wanted):
    state, archive = closed_archive(parent)
    stage = json.loads((parent / '阶段归档.json').read_text())
    assert stage['业务成功'] is False, 'an audit failure is not a failed device execution'
    lease = json.loads((parent / '镜像生命周期.json').read_text())
    assert lease['status'] == 'deleted' and not Path(lease['image']).exists()
    folder = parent / state['标签']
    assert json.loads((folder / 'comparison_plan.json').read_text()) == wanted
    phase = (folder / 'phase.txt').read_text().strip() if (folder / 'phase.txt').exists() else 'run'
    assert phase in ('run', 'verify', 'reopen'), 'deployment/load failure requires separate investigation'
    assert int((folder / (phase + '.exit')).read_text()) != 0, 'only archived execution failures may continue'
    for earlier in ('load', 'run', 'verify', 'reopen')[:('load', 'run', 'verify', 'reopen').index(phase)]:
        assert int((folder / (earlier + '.exit')).read_text()) == 0, 'earlier phase did not complete'
    assert not (folder / 'comparison_complete.json').exists()
    packed = {}
    manifest = parent / '失败二进制无损压缩.json'
    if manifest.exists():
        for item in json.loads(manifest.read_text())['files']:
            packed[item['name']] = item
    verified = {}
    with tarfile.open(archive, 'r|gz') as tar:
        for member in tar:
            prefix = state['标签'] + '/'
            if not member.name.startswith(prefix) or not member.isfile():
                continue
            name = member.name[len(prefix):]
            assert name and not Path(name).is_absolute() and '..' not in Path(name).parts
            local = folder / name
            assert name not in verified
            compressed = None
            if not local.exists():
                assert name in packed and name.endswith('.bin'), 'missing archived failure file'
                compressed = local.with_suffix('.bin.gz')
                assert compressed.is_file() and not compressed.is_symlink()
                assert hashlib.sha256(compressed.read_bytes()).hexdigest() == packed[name]['compressed_sha256']
            else:
                assert local.is_file() and not local.is_symlink()
            h = hashlib.sha256()
            with tar.extractfile(member) as f:
                for block in iter(lambda: f.read(1 << 20), b''):
                    h.update(block)
            local_hash = hashlib.sha256()
            with (gzip.open(compressed, 'rb') if compressed else local.open('rb')) as f:
                for block in iter(lambda: f.read(1 << 20), b''):
                    local_hash.update(block)
            actual = local_hash.hexdigest()
            assert actual == h.hexdigest(), 'failed evidence differs from archive: ' + name
            if compressed: assert actual == packed[name]['sha256']
            verified[name] = actual
    required = {phase + '.exit', phase + '.log', phase + '.zenfs.log',
                'comparison_plan.json', 'comparison_db_bench'}
    assert required <= verified.keys(), 'missing archived failure evidence'
    log = (folder / (phase + '.log')).read_text(errors='replace')
    native = (folder / (phase + '.zenfs.log')).read_text(errors='replace')
    errors = [dict(line=i + 1, text=line) for i, line in enumerate(log.splitlines())
              if 'what():' in line or 'Received signal' in line or 'open error:' in line]
    rounds = [dict(line=i + 1, text=line) for i, line in enumerate(native.splitlines())
              if 'ZC 统计：' in line or 'ZC 迁移失败：' in line]
    completed_batches = re.findall(r'应用写批次完成：批次=(\d+)',
                                  (folder / 'run.log').read_text(errors='replace'))
    return dict(arm=wanted['arm'], outcome='failed_execution', passed=False,
                archive_integrity_verified=True, archive=str(archive),
                archive_sha256=state['镜像处理']['archive_sha256'], files=verified,
                binary_sha256=verified['comparison_db_bench'],
                failed_phase=phase, phase_exit=int((folder / (phase + '.exit')).read_text()),
                business_exit=int((folder / 'run.exit').read_text()), errors=errors,
                maintenance_log=rounds,
                last_successful_flush_batch=max(map(int, completed_batches), default=0),
                guest_exited=True, image_reclaimed=True, full_workload_verified=False,
                boundary='failed execution has no full four-phase content and maintenance proof; '
                         'individual completed phases may be analyzed separately, never as full acceptance')
