"""Bound replay checkpoints and keep one copy of closed experiment evidence."""
import hashlib
import fcntl
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import tarfile

from ycsb_af import digest, require


def atomic_json(path, value):
    path = Path(path)
    partial = path.with_name(path.name + '.storage-partial')
    require(not partial.exists(), '保存策略临时文件已存在')
    with partial.open('x') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n');stream.flush();os.fsync(stream.fileno())
    partial.replace(path)


def closed_archive(output):
    output = Path(output)
    state = json.loads((output/'状态.json').read_text())
    stage = json.loads((output/'阶段归档.json').read_text())
    require(stage['完整'] and not stage['客机仍存活'] and state['当前客机PID'] is None,
            '结果尚未完整归档退出')
    archive = Path(state['镜像处理']['archive'])
    require(archive.parent == output and digest(archive) == state['镜像处理']['archive_sha256'],
            '保存策略要求当前归档哈希核验通过')
    return state, archive


def replay_source(output):
    visited = set()
    while True:
        output = Path(output).resolve()
        require(output not in visited, '固定输入来源形成循环')
        visited.add(output)
        state, archive = closed_archive(output)
        reference = state['镜像处理'].get('archive_input_reference')
        if not reference:
            manifest = output/state['标签']/'逻辑输入/清单.json'
            require(manifest.is_file() and json.loads(manifest.read_text())['逐区'], '来源缺少固定输入清单')
            expected_name = state['标签']+'/逻辑输入/清单.json'
            expected = {str(row['Zone'])+'.bin.gz': row['压缩SHA256']
                        for row in json.loads(manifest.read_text())['逐区']}
            actual = {};manifest_found = False
            with tarfile.open(archive, 'r|gz') as tar:
                for member in tar:
                    if member.name == expected_name:
                        require(member.isfile() and hashlib.sha256(tar.extractfile(member).read()).hexdigest()
                                == digest(manifest), '来源输入清单与归档不一致')
                        manifest_found = True
                    elif member.name.startswith(state['标签']+'/逻辑输入/') and member.name.endswith('.bin.gz'):
                        name = Path(member.name).name
                        require(member.isfile() and name not in actual, '来源输入重复或不是普通文件')
                        h = hashlib.sha256()
                        with tar.extractfile(member) as stream:
                            for block in iter(lambda:stream.read(1<<20), b''):h.update(block)
                        actual[name] = h.hexdigest()
            require(manifest_found and actual == expected, '来源归档缺少固定输入字节或内容不同')
            return dict(output=str(output), archive=str(archive),
                        sha256=state['镜像处理']['archive_sha256'], label=state['标签'])
        require(digest(Path(reference['archive'])) == reference['sha256'], '固定输入引用归档哈希变化')
        output = Path(reference['output'])


def checkpoint_dir(base):
    return Path(base)/'.result-storage/replay-checkpoints'


def check_maintenance(base):
    path = Path(base)/'.result-storage/maintenance.lock'
    if path.exists():
        with path.open('rb') as lock:
            try:fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:raise ValueError('实验结果正在整理；完成后再启动，避免磁盘干扰计时')
            finally:fcntl.flock(lock, fcntl.LOCK_UN)


@contextmanager
def maintenance_guard(base):
    folder = Path(base)/'.result-storage';folder.mkdir(parents=True,exist_ok=True)
    with (folder/'maintenance.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:yield
        finally:fcntl.flock(lock, fcntl.LOCK_UN)


def check_checkpoint_capacity(base, label=None):
    records = list(checkpoint_dir(base).glob('*.json'))
    existing = label is not None and (checkpoint_dir(base)/(label+'.json')).exists()
    require(existing or len(records) < 2,
            '固定逻辑输入检查点已达2份；先整理已登记来源，禁止继续逐轮复制输入')
    return len(records)


def reserve_checkpoint(base, label, output):
    require(re.fullmatch(r'joint-controlled-[a-z0-9-]+', label), '检查点标签非法')
    folder = checkpoint_dir(base);folder.mkdir(parents=True, exist_ok=True)
    path = folder/(label+'.json')
    with (folder.parent/'checkpoint.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        check_checkpoint_capacity(base, label)
        if path.exists():
            require(json.loads(path.read_text())['output'] == str(output), '检查点来源变化')
        else:
            atomic_json(path, dict(output=str(output), status='reserved',
                                  purpose='共享逻辑输入来源；不保存NAND内存状态'))
    return path


def check_observation(source, ftl_audit):
    state = json.loads((Path(source)/'状态.json').read_text())
    previous = state.get('完整设备命令追踪', bool(state.get('设备命令日志')))
    require(bool(previous) == bool(ftl_audit),
            '来源与本轮完整设备追踪开关不同；须重做同观测配置的来源重复，或显式--ftl-audit匹配来源')


def configure(run, *, input_source=None, ftl_audit=False):
    run.ftl_audit_enabled = bool(ftl_audit)
    run.archive_exclusions = ('固定输入复用.tar',)
    policy = dict(version=1, 逐请求记录=True, 维护账目=True, 完整设备命令追踪=bool(ftl_audit),
                  计时边界='完整设备命令追踪开关变化后需重新核验重复性，不能混用历史计时')
    if input_source is not None:
        check_observation(input_source, ftl_audit)
        ref = replay_source(input_source)
        plan = json.loads((run.output/'源码/固定输入复用清单.json').read_text())
        source = Path(ref['output'])/ref['label']/'逻辑输入/清单.json'
        expected = {'逻辑输入/'+str(row['Zone'])+'.bin.gz': row['压缩SHA256']
                    for row in json.loads(source.read_text())['逐区']}
        actual = {name: sha for name, sha in plan['文件'].items() if name.endswith('.bin.gz')}
        require(actual == expected and plan['来源归档SHA256'] == ref['sha256'],
                '复用输入与共享来源不同，不能省略本轮输入字节')
        source_output = Path(ref['output'])
        marker = reserve_checkpoint(source_output.parent, source_output.name.removesuffix('-results'), source_output)
        atomic_json(marker, dict(output=ref['output'], status='archived', archive=ref['archive'], sha256=ref['sha256']))
        run.archive_input_reference = ref
        run.archive_exclusions += ('逻辑输入/*.bin.gz',)
        policy['固定输入引用'] = ref
        policy['本轮归档含输入字节'] = False
    else:
        policy['本轮归档含输入字节'] = True
    atomic_json(run.output/'结果保存策略.json', policy)
    run.record(结果保存策略=policy)


def journal(output, row):
    with (Path(output)/'结果收尾记录.jsonl').open('a') as stream:
        stream.write(json.dumps(row, ensure_ascii=False)+'\n')
        stream.flush();os.fsync(stream.fileno())


def deduplicate_collected(output, label):
    output = Path(output);reclaimed = 0;count = 0
    for target in sorted((output/'逐臂采证').rglob('*')):
        if target.is_symlink() or not target.is_file():continue
        source = output/label/target.relative_to(output/'逐臂采证')
        if source.is_symlink() or not source.is_file():continue
        a, b = source.stat(), target.stat()
        if (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino) or b.st_nlink != 1:continue
        if (a.st_size, a.st_uid, a.st_gid) != (b.st_size, b.st_uid, b.st_gid):continue
        # Extraction can add group-write permission. Never add permissions to either copy.
        if (a.st_mode & ~0o222) != (b.st_mode & ~0o222) or (a.st_mode | b.st_mode) != b.st_mode:continue
        sha = digest(source)
        if digest(target) != sha:continue
        temp = target.with_name(target.name+'.storage-link')
        require(not temp.exists(), '证据合并临时文件已存在')
        journal(output, dict(action='dedup-prepared', source=str(source), target=str(target), sha256=sha,
                             target_mtime_ns=b.st_mtime_ns, target_mode=b.st_mode&0o777,
                             retained_mode=a.st_mode&0o777))
        os.link(source, temp)
        try:
            require(source.stat().st_ino == a.st_ino and target.stat().st_ino == b.st_ino and
                    digest(temp) == digest(target) == sha, '合并前证据变化')
            temp.replace(target)
        finally:
            if temp.exists():temp.unlink()
        count += 1;reclaimed += b.st_blocks*512
        journal(output, dict(action='deduplicated', target=str(target), sha256=sha))
    return dict(files=count, reclaimed_bytes=reclaimed)


def remove_input_transfer(output):
    output = Path(output);payload = output/'源码/固定输入复用.tar'
    if not payload.exists():return 0
    plan = json.loads((payload.parent/'固定输入复用清单.json').read_text())
    ref = replay_source(Path(plan['来源']))
    require(ref['sha256'] == plan['来源归档SHA256'] and digest(payload) == plan['压缩包SHA256'],
            '输入传输包或其保留来源变化')
    # Transfer manifest is preserved; the retained source contains all logical bytes.
    source = Path(ref['output'])/ref['label']/'逻辑输入/清单.json'
    expected = {'逻辑输入/'+str(row['Zone'])+'.bin.gz': row['压缩SHA256']
                for row in json.loads(source.read_text())['逐区']}
    require({n: h for n, h in plan['文件'].items() if n.endswith('.bin.gz')} == expected,
            '传输包固定输入与保留来源不同')
    before = payload.stat();seen = set()
    with tarfile.open(payload) as tar:
        for member in tar:
            require(member.isfile() and member.name in plan['文件'] and member.name not in seen,
                    '传输包有未登记成员')
            h = hashlib.sha256()
            with tar.extractfile(member) as stream:
                for block in iter(lambda:stream.read(1<<20), b''):h.update(block)
            require(h.hexdigest() == plan['文件'][member.name], '传输包成员变化')
            if member.name.startswith('参考起点/'):
                original = Path(plan['来源'])/'逐臂采证/p1'/Path(member.name).name
                require(digest(original) == h.hexdigest(), '参考起点来源变化')
            elif member.name.startswith('逻辑输入/') and not member.name.endswith('.bin.gz'):
                original = Path(ref['output'])/ref['label']/member.name
                require(digest(original) == h.hexdigest(), '辅助输入来源变化')
            seen.add(member.name)
    require(seen == set(plan['文件']), '传输包成员不完整')
    journal(output, dict(action='transfer-verified', path=str(payload), sha256=plan['压缩包SHA256'], source=ref))
    require(payload.stat().st_ino == before.st_ino and digest(payload) == plan['压缩包SHA256'], '传输包身份变化')
    payload.unlink()
    journal(output, dict(action='transfer-removed', path=str(payload), reclaimed_bytes=before.st_blocks*512))
    return before.st_blocks*512


def share_archived_input(output, input_source):
    """Replace a legacy full copy by a verified key archive plus an input reference."""
    output = Path(output);state, original = closed_archive(output)
    reference = replay_source(input_source)
    if state['镜像处理'].get('archive_input_reference'):
        require(state['镜像处理']['archive_input_reference'] == reference, '归档已引用不同输入来源')
        return dict(reclaimed_bytes=0, reference=reference)
    source = Path(reference['output'])/reference['label']/'逻辑输入/清单.json'
    expected = {str(row['Zone'])+'.bin.gz': row['压缩SHA256']
                for row in json.loads(source.read_text())['逐区']}
    destination = original.with_name(original.name.removesuffix('.tar.gz')+'-key.tar.gz')
    partial = destination.with_name(destination.name+'.partial')
    require(not destination.exists() and not partial.exists(), '禁止覆盖关键归档')
    before = original.stat();dropped = {};kept = {}

    class HashingReader:
        def __init__(self, stream):
            self.stream = stream;self.hash = hashlib.sha256()
        def read(self, size=-1):
            data = self.stream.read(size);self.hash.update(data);return data

    try:
        with tarfile.open(original, 'r|gz') as old, tarfile.open(str(partial), 'w:gz', compresslevel=1) as new:
            for member in old:
                require(not Path(member.name).is_absolute() and '..' not in Path(member.name).parts,
                        '原归档含越界路径')
                if member.isfile():
                    stream = HashingReader(old.extractfile(member))
                    if '/逻辑输入/' in member.name and member.name.endswith('.bin.gz'):
                        name = Path(member.name).name
                        require(name in expected and name not in dropped, '共享输入成员异常')
                        while stream.read(1<<20):pass
                        require(stream.hash.hexdigest() == expected[name], '本轮输入字节与共享来源不同')
                        dropped[name] = stream.hash.hexdigest()
                    else:
                        require(member.name not in kept, '原归档成员重复')
                        new.addfile(member, stream);kept[member.name] = stream.hash.hexdigest()
                else:
                    require(member.isdir() or member.issym() or member.islnk(), '原归档含特殊文件')
                    require(not member.islnk() or not ('/逻辑输入/' in member.linkname and
                            member.linkname.endswith('.bin.gz')), '不能删除仍有硬链接依赖的输入成员')
                    new.addfile(member)
        require(dropped == expected, '原归档输入不完整，不能精简')
        actual = {}
        with tarfile.open(partial, 'r|gz') as check:
            for member in check:
                if member.isfile():
                    stream = HashingReader(check.extractfile(member))
                    while stream.read(1<<20):pass
                    require(member.name not in actual, '关键归档成员重复')
                    actual[member.name] = stream.hash.hexdigest()
        require(actual == kept and digest(original) == state['镜像处理']['archive_sha256'] and
                original.stat().st_ino == before.st_ino, '关键归档或原归档核验失败')
        with partial.open('rb') as stream:os.fsync(stream.fileno())
        partial.replace(destination)
        replacement = dict(original=str(original), original_sha256=state['镜像处理']['archive_sha256'],
                           archive=str(destination), sha256=digest(destination), input_reference=reference,
                           omitted_input_sha256=dropped, retained_file_sha256=kept)
        atomic_json(output/'归档精简.json', replacement)
        image = state['镜像处理']
        image.update(original_archive=str(original), original_archive_sha256=image['archive_sha256'],
                     archive=str(destination), archive_sha256=replacement['sha256'],
                     archive_input_reference=reference, archive_scope='关键证据及共享输入引用')
        atomic_json(output/'状态.json', state)
        closed_archive(output)
        original.unlink()
        replacement = dict(reclaimed_bytes=before.st_blocks*512-destination.stat().st_blocks*512,
                           reference=reference)
        journal(output, dict(action='archive-input-shared', **replacement))
        return replacement
    finally:
        if partial.exists():partial.unlink()


def compact(output, *, input_source=None):
    output = Path(output);state, archive = closed_archive(output)
    result = dict(重复证据=deduplicate_collected(output, state['标签']),
                  输入传输包释放字节=remove_input_transfer(output))
    log = output/'FEMU设备命令.bin'
    if log.exists():
        from compress_archived_ftl_logs import compress
        result['设备追踪无损压缩'] = compress(log, apply=True)
    if input_source is not None:
        result['共享归档输入'] = share_archived_input(output, input_source)
        state, archive = closed_archive(output)
    marker = checkpoint_dir(output.parent)/(output.name.removesuffix('-results')+'.json')
    if marker.exists():
        atomic_json(marker, dict(output=str(output), status='archived', archive=str(archive),
                                 sha256=state['镜像处理']['archive_sha256']))
    atomic_json(output/'结果收尾.json', result)
    return result
