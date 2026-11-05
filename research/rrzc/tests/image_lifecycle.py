"""Bound new experiment images; reclaim only owned, archived, stopped leaves.

Legacy images without a registry entry are never automatically deleted.
The registry lives next to the images; permanent evidence lives in the result directory.
"""
import fcntl
import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path
import shutil
import stat
import subprocess
import time

MAX_CHECKPOINTS = 2
MAX_TEMPORARY = 4


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def write_json(path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    temporary.replace(path)


def info(qemu_img, image, *, shared=False):
    args = [str(qemu_img), 'info', '--output=json']
    if shared:
        args.append('-U')
    return json.loads(subprocess.check_output(
        args + [str(image)], text=True, stderr=subprocess.PIPE))


def file_identity(path):
    s = path.lstat()
    if not stat.S_ISREG(s.st_mode) or s.st_uid != os.getuid() or s.st_nlink != 1:
        raise RuntimeError('镜像不是本账号的独立普通文件：' + str(path))
    return [s.st_dev, s.st_ino]


def systemd_user_pam(proc):
    """Recognize systemd's protected PAM session, not an experiment process."""
    try:
        if (proc / 'comm').read_text().strip() != '(sd-pam)':
            return False
        if (proc / 'cmdline').read_bytes() != b'(sd-pam)\0':
            return False
        fields = dict(line.split(':', 1) for line in (proc / 'status').read_text().splitlines()
                      if ':' in line)
        parent = proc.parent / fields['PPid'].strip()
        return ((parent / 'comm').read_text().strip() == 'systemd' and
                (parent / 'cmdline').read_bytes() == b'/lib/systemd/systemd\0--user\0')
    except (OSError, KeyError, ValueError):
        return False


def in_use(image):
    """Check visible same-account open file descriptors, refusing uncertain access."""
    expected = image.stat()
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            for fd in (proc / 'fd').iterdir():
                try:
                    s = fd.stat()
                    if (s.st_dev, s.st_ino) == (expected.st_dev, expected.st_ino):
                        return True
                except FileNotFoundError:
                    pass
        except FileNotFoundError:
            continue
        except PermissionError as error:
            if systemd_user_pam(proc):
                continue
            raise RuntimeError('无法核对本账号进程占用，保留镜像') from error
    return False


@contextmanager
def exclusive_image_lease(image):
    """Hold a kernel write lease until the archived temporary image is removed."""
    fd = os.open(image, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        opened = os.fstat(fd)
        current = image.lstat()
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
            raise RuntimeError('镜像身份变化，拒绝自动删除')
        try:
            fcntl.fcntl(fd, fcntl.F_SETLEASE, fcntl.F_WRLCK)
        except OSError as error:
            raise RuntimeError('无法取得镜像独占租约，可能仍被进程占用；保留镜像') from error
        try:
            yield
        finally:
            fcntl.fcntl(fd, fcntl.F_SETLEASE, fcntl.F_UNLCK)
    finally:
        os.close(fd)


class ImageLease:
    def __init__(self, base, image, parent, output, qemu_img, *, checkpoint=False):
        self.base = Path(base).resolve()
        self.image = Path(image).absolute()
        self.parent = Path(parent).absolute()
        self.output = Path(output)
        self.qemu_img = Path(qemu_img)
        self.registry = self.base / '.image-lifecycle'
        self.entry = self.registry / (self.image.name + '.json')
        self.checkpoint = checkpoint
        self.data = None

    def reserve(self):
        if self.image.parent != self.base or self.image.suffix != '.qcow2':
            raise RuntimeError('镜像生命周期目标不在指定目录')
        if self.image.exists() or self.image.is_symlink() or self.entry.exists():
            raise RuntimeError('镜像或生命周期登记已存在，拒绝覆盖')
        if self.registry.is_symlink():
            raise RuntimeError('镜像登记目录不能是符号链接')
        self.registry.mkdir(exist_ok=True)
        with (self.registry / 'lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            records = [json.loads(p.read_text()) for p in self.registry.glob('*.json')]
            live = [r for r in records if r['status'] not in ('deleted', 'not-created')]
            for record in live:
                if Path(record['image']).resolve() == self.parent.resolve() and record['kind'] == 'temporary':
                    raise RuntimeError('临时实验镜像不能作为后续父镜像；请使用明确保留的构建环境')
            kind = 'checkpoint' if self.checkpoint else 'temporary'
            limit = MAX_CHECKPOINTS if self.checkpoint else MAX_TEMPORARY
            if sum(r['kind'] == kind for r in live) >= limit:
                raise RuntimeError(f'{kind}保留数已达{limit}；先归档清理或整理核心环境，不继续新建镜像')
            # Recheck under the shared lock before reserving the name.
            if self.entry.exists() or self.image.exists() or self.image.is_symlink():
                raise RuntimeError('镜像名称已被其他监督使用')
            self.data = dict(image=str(self.image), parent=str(self.parent), output=str(self.output),
                             kind=kind, status='reserved', supervisor_pid=os.getpid(),
                             created_utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), identity=None)
            source = self.output / '镜像管理源码.py'
            shutil.copyfile(__file__, source)
            self.data['lifecycle_source_sha256'] = digest(source)
            self.save()

    def save(self):
        write_json(self.entry, self.data)
        write_json(self.output / '镜像生命周期.json', self.data)

    def created(self, image, parent):
        if Path(image).absolute() != self.image or Path(parent).absolute() != self.parent:
            raise RuntimeError('实际镜像路径与生命周期登记不符')
        metadata = info(self.qemu_img, self.image)
        backing = metadata.get('full-backing-filename')
        if not backing or Path(backing).resolve() != self.parent.resolve():
            raise RuntimeError('实际父镜像与生命周期登记不符')
        self.data.update(identity=file_identity(self.image), status='created')
        self.save()

    def cancel_uncreated(self):
        if self.data and not self.image.exists() and not self.image.is_symlink():
            self.data['status'] = 'not-created'
            self.save()

    def finish(self, archive, expected_hash, *, archived, guest_exited):
        if not self.data or self.data['status'] == 'not-created':
            return
        if not archived or not guest_exited or archive is None or not expected_hash:
            self.data.update(status='retained-incomplete', reason='归档不完整或未确认客机退出')
            self.save()
            return
        archive = Path(archive)
        if digest(archive) != expected_hash:
            raise RuntimeError('清理前归档哈希不符，保留镜像')
        if self.data['identity'] != file_identity(self.image):
            raise RuntimeError('镜像身份变化，拒绝自动删除')
        self.data.update(archive=str(archive), archive_sha256=expected_hash)
        if self.checkpoint:
            if in_use(self.image):
                raise RuntimeError('镜像仍被进程打开，拒绝自动清理')
            self.data.update(status='checkpoint', reason='明确保留的构建环境，不自动删除')
            self.save()
            return
        with exclusive_image_lease(self.image):
            with (self.registry / 'lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                # Include symlinks into HDD archives; preserve parents of every existing image.
                for candidate in self.base.rglob('*.qcow2'):
                    if candidate == self.image:
                        continue
                    metadata = info(self.qemu_img, candidate, shared=True)
                    backing = metadata.get('full-backing-filename')
                    if backing and Path(backing).resolve() == self.image.resolve():
                        self.data.update(status='retained-dependent', reason='现有子镜像仍依赖该文件')
                        self.save()
                        raise RuntimeError('临时镜像出现子依赖，保留并停止后续新建：' + str(candidate))
                if self.data['identity'] != file_identity(self.image):
                    raise RuntimeError('删除前镜像身份变化')
                self.data.update(status='deleting', allocated_bytes=self.image.stat().st_blocks * 512)
                self.save()
                self.image.unlink()
                self.data.update(status='deleted', reason='本轮临时镜像归档校验、正常退出及依赖检查通过')
                self.save()
