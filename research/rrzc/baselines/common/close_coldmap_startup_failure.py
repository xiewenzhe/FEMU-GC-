"""Archive the old pre-deployment failure, then reclaim its registered stopped leaf."""
import hashlib
import json
from pathlib import Path
import tarfile

from run_coldmap_survival_host import PREVIOUS
from run_comparison_host import IMAGE_DIR, atomic_json, digest
from compact_closed_requests import closed_no_guest, stream_hash
from result_storage import maintenance_guard
from image_lifecycle import ImageLease, file_identity


def main():
    root = PREVIOUS / 'cold-survival'
    archive = root / '启动失败宿主证据-20261110.tar.gz'
    receipt = root / '启动失败收尾核验-20261110.json'
    assert not archive.exists() and not receipt.exists()
    with maintenance_guard(IMAGE_DIR):
        closed_no_guest()
        state = json.loads((root / '状态.json').read_text())
        stage = json.loads((root / '阶段归档.json').read_text())
        old = json.loads((root / '镜像生命周期.json').read_text())
        assert state['当前客机PID'] is None and not stage['客机仍存活'] and not stage['业务成功']
        assert stage['原始错误'] == "RuntimeError('认证失败，不重试密码')"
        assert not (root / state['标签']).exists(), 'guest payload was deployed; use business evidence recovery'
        assert old['kind'] == 'temporary' and old['status'] == 'retained-incomplete'
        assert not Path('/proc', str(old['supervisor_pid'])).exists(), 'old supervisor still exists'
        image = Path(old['image'])
        assert image.parent == IMAGE_DIR and file_identity(image) == old['identity']
        original = {p.name: digest(p) for p in root.iterdir() if p.is_file()}
        with tarfile.open(archive, 'x:gz') as tar:
            for name in original: tar.add(root / name, arcname='startup/' + name, recursive=False)
        verified = {}
        with tarfile.open(archive, 'r|gz') as tar:
            for member in tar:
                assert member.isfile() and member.name.startswith('startup/')
                name = member.name[len('startup/'):]
                assert name in original and name not in verified
                with tar.extractfile(member) as stream: verified[name] = stream_hash(stream)
        assert verified == original
        sha = digest(archive)
        result = dict(status='host_evidence_verified', archive=str(archive), archive_sha256=sha,
            files=verified, original_image=old, business_success=False,
            boundary='pre-deployment startup failure only; not a guest/NAND snapshot or successful workload')
        atomic_json(receipt, result)
        repo = Path(__file__).resolve().parents[4]
        lease = ImageLease(IMAGE_DIR, image, old['parent'], root, repo / 'build-femu/qemu-img')
        lease.data = old
        lease.finish(archive, sha, archived=True, guest_exited=True)
        assert lease.data['status'] == 'deleted' and not image.exists()
        state['镜像处理'] = lease.data
        state['启动失败宿主证据'] = str(receipt)
        atomic_json(root / '状态.json', state)
        payload = root / 'payload.tar.gz'
        if payload.exists():
            assert digest(payload) == original['payload.tar.gz']; payload.unlink()
        result.update(status='archived_and_image_reclaimed', lifecycle=lease.data)
        atomic_json(receipt, result)
        print(json.dumps(dict(status=result['status'], image_deleted=str(image)), ensure_ascii=False))


if __name__ == '__main__': main()
