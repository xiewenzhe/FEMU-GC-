"""Prepare independent related-work source trees; never build or start a guest.

The initial copies are deliberately NOT claimed to implement the papers. Source
preparation and paper/port acceptance are different milestones.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import stat
import subprocess


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SOURCE = ROOT / 'research/rrzc/results/placement-ablation-20261105/阶段二'
METHODS = ('caza', 'oaza_gc', 'coldmap')
SCHEMA = 'rrzc-related-work-isolation-v1'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def regular(path):
    path = Path(path)
    require(stat.S_ISREG(path.lstat().st_mode), '不是独立普通文件：' + str(path))
    require(path.stat().st_nlink == 1, '拒绝硬链接源：' + str(path))
    return path


def safe_child(root, name):
    relative = PurePosixPath(name)
    require(name and not relative.is_absolute() and '\\' not in name and
            all(x not in ('', '.', '..') for x in name.split('/')),
            '清单路径非法：' + name)
    target = Path(root).joinpath(*relative.parts)
    # resolve() alone would conceal symlinks that point back inside the tree.
    current = Path(root)
    require(not current.is_symlink(), '根目录不能是符号链接')
    for part in relative.parts:
        current = current / part
        require(not current.is_symlink(), '清单路径包含符号链接：' + str(current))
    return target


def write_json(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def check_files(root, expected):
    require(isinstance(expected, dict) and bool(expected), '文件清单为空或格式错误')
    for name, value in expected.items():
        path = regular(safe_child(root, name))
        require(digest(path) == value, '文件身份不符：' + str(path))


def copy_files(source, target, expected):
    target.mkdir(parents=True, exist_ok=False)
    for name, value in expected.items():
        original = regular(safe_child(source, name))
        require(digest(original) == value, '复制前源身份不符：' + str(original))
        dest = safe_child(target, name)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with original.open('rb') as inp, dest.open('xb') as out:
            while chunk := inp.read(1 << 20):
                out.write(chunk)
        require(digest(dest) == value, '副本身份不符：' + str(dest))
        require(dest.stat().st_ino != original.stat().st_ino or
                dest.stat().st_dev != original.stat().st_dev, '副本共享源 inode')


def source_identity(source):
    source = Path(source).resolve()
    identity_file = regular(source / '副本身份.json')
    delivery_file = regular(source / '交付文件SHA256.json')
    identity = json.loads(identity_file.read_text())
    delivery = json.loads(delivery_file.read_text())
    check_files(source, delivery)
    files = identity['新源码']
    require(all('/' not in name and name.endswith(('.cc', '.h', '.inc')) for name in files),
            '冻结ZenFS源码清单包含非源码或子路径')
    check_files(source / '新版源码', files)
    program = regular(Path(identity['原程序']))
    require(digest(program) == identity['原程序SHA256'], '原可运行程序身份不符')
    return dict(path=str(source), identity_sha256=digest(identity_file),
                delivery_sha256=digest(delivery_file), files=files,
                original_program=str(program), original_program_sha256=digest(program),
                original_library_sha256=identity['原库SHA256'])


def protected_identity():
    """Include existing untracked research code, not just the current Git HEAD."""
    folders = ('hw/femu', 'research/rrzc/model', 'research/rrzc/tests')
    suffixes = {'.c', '.cc', '.h', '.inc', '.py', '.sh', '.patch'}
    paths = [ROOT / 'AGENTS.md']
    for folder in folders:
        paths.extend(p for p in (ROOT / folder).rglob('*')
                     if p.suffix in suffixes and p.is_file() and
                     '__pycache__' not in p.parts)
    paths.extend((ROOT / 'research/rrzc').glob('*.py'))
    paths.extend((ROOT / 'research/rrzc').glob('*.patch'))
    paths.extend((ROOT / 'research/rrzc').glob('*.md'))
    return {str(p.relative_to(ROOT)): digest(regular(p)) for p in sorted(set(paths))}


def protected_changes(before):
    changes = []
    for name, expected in before.items():
        try:
            actual = digest(regular(safe_child(ROOT, name)))
        except (OSError, ValueError) as error:
            changes.append(dict(path=name, expected=expected, actual=None, error=str(error)))
            continue
        if actual != expected:
            changes.append(dict(path=name, expected=expected, actual=actual))
    return changes


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args])


def pin_upstream(repo, commit):
    repo = Path(repo).resolve()
    require(len(commit) == 40 and all(c in '0123456789abcdef' for c in commit),
            '必须提供完整Git提交身份')
    require(git(repo, 'rev-parse', 'HEAD').decode().strip() == commit, '上游HEAD未固定')
    require(not git(repo, 'status', '--porcelain', '--untracked-files=all'), '上游工作树有修改')
    tree = git(repo, 'ls-tree', '-rz', commit)
    files = {}
    for record in tree.split(b'\0'):
        if not record:
            continue
        metadata, raw_name = record.split(b'\t', 1)
        mode, kind, blob = metadata.split()
        require(kind == b'blob' and mode in (b'100644', b'100755'), '上游含链接或子模块')
        name = raw_name.decode()
        path = regular(safe_child(repo, name))
        data = path.read_bytes()
        expected_blob = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
        require(expected_blob == blob.decode(), '上游工作树与Git对象不符：' + name)
        files[name] = hashlib.sha256(data).hexdigest()
    require('rocksdb/plugin/zenfs/fs/fs_zenfs.cc' in files, '上游缺少ZenFS实现')
    return dict(path=str(repo), commit=commit, files=files,
                role='仅供阅读、对照及提取；未运行上游脚本、未作为共同软件版本')


def prepare(output, source=DEFAULT_SOURCE, upstream=None, commit=None):
    output = Path(output).absolute()
    require(not output.exists() and not output.is_symlink(), '不覆盖已有工作目录')
    # Preparation must never place output inside any source/protected tree.
    source = Path(source).resolve()
    destination = output.resolve()
    require(not destination.is_relative_to(source) and not source.is_relative_to(destination),
            '输出与冻结来源重叠')
    for folder in ('hw/femu', 'research/rrzc/model', 'research/rrzc/tests'):
        require(not destination.is_relative_to((ROOT / folder).resolve()), '输出进入已有源码目录')
    require(not destination.is_relative_to((ROOT / 'research/rrzc/results').resolve()),
            '工作副本不能写入既有实验归档目录')
    if upstream is not None:
        require(not destination.is_relative_to(Path(upstream).resolve()) and
                not Path(upstream).resolve().is_relative_to(destination), '输出与上游材料重叠')
    require((upstream is None) == (commit is None), '上游目录与提交必须同时提供')
    before = protected_identity()
    origin = source_identity(source)
    upstream_record = pin_upstream(upstream, commit) if upstream else None
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / '准备开始.json', dict(schema=SCHEMA, complete=False))
    write_json(output / '原机制保护清单.json', before)
    write_json(output / '共同来源.json', origin)
    if upstream_record:
        write_json(output / 'ColdMap上游身份.json', upstream_record)
    for method in METHODS:
        folder = output / method
        folder.mkdir()
        copy_files(source / '新版源码', folder / 'src/plugin/zenfs/fs', origin['files'])
        (folder / 'build').mkdir()
        (folder / 'patches').mkdir()
        write_json(folder / '方法状态.json', dict(
            method=method, source_state='冻结起始副本，尚未接入相关工作算法',
            implementation_complete=False, full_build_passed=False,
            device_verified=False, performance_verified=False,
            runnable=False, source_sha256=origin['files']))
    check_files(ROOT, before)
    # Also recheck the frozen source and old binary after the complete copy.
    require(source_identity(source) == origin, '准备期间共同来源变化')
    report = dict(schema=SCHEMA, complete=True, methods=list(METHODS),
                  independent_source_trees=True, protected_source_unchanged=True,
                  original_program_unchanged=True, build_executed=False,
                  guest_started=False, algorithms_implemented=False)
    write_json(output / '准备完成.json', report)
    require(verify(output)['passed'], '完成核验期间原工作区发生变化；保留清单与现场')
    return report


def verify(output):
    output = Path(output).resolve()
    report = json.loads(regular(output / '准备完成.json').read_text())
    require(report['schema'] == SCHEMA and report['complete'], '准备未完成')
    origin = json.loads(regular(output / '共同来源.json').read_text())
    require(source_identity(origin['path']) == origin, '冻结来源或原程序发生变化')
    before = json.loads(regular(output / '原机制保护清单.json').read_text())
    changes = protected_changes(before)
    method_dirs = []
    for method in METHODS:
        folder = safe_child(output, method)
        state = json.loads(regular(folder / '方法状态.json').read_text())
        require(state['method'] == method and not state['runnable'] and
                not state['implementation_complete'], '初始副本状态被改写')
        require(state['source_sha256'] == origin['files'], '副本清单与共同来源不同')
        fs = safe_child(folder, 'src/plugin/zenfs/fs')
        check_files(fs, origin['files'])
        actual = {str(p.relative_to(fs)) for p in fs.rglob('*') if p.is_file() or p.is_symlink()}
        require(actual == set(origin['files']), '副本内混入未登记文件')
        for name in origin['files']:
            src = fs / name
            for previous in method_dirs:
                other = previous / name
                require((src.stat().st_dev, src.stat().st_ino) !=
                        (other.stat().st_dev, other.stat().st_ino), '方法间共享文件inode')
        method_dirs.append(fs)
        build = safe_child(folder, 'build')
        require(build.is_dir(), '缺少独立构建目录')
        require(not any(build.iterdir()), '初始副本混入未核验构建产物')
    upstream_file = output / 'ColdMap上游身份.json'
    if upstream_file.exists():
        upstream = json.loads(regular(upstream_file).read_text())
        require(pin_upstream(upstream['path'], upstream['commit']) == upstream,
                '上游材料身份改变')
    return dict(passed=not changes, source_trees_valid=True,
                protected_source_unchanged=not changes, protected_changes=changes,
                methods=list(METHODS), algorithms_implemented=False,
                full_build_passed=False, guest_started=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    create = sub.add_parser('prepare')
    create.add_argument('--output', type=Path, required=True)
    create.add_argument('--source', type=Path, default=DEFAULT_SOURCE)
    create.add_argument('--upstream', type=Path)
    create.add_argument('--commit')
    check = sub.add_parser('verify')
    check.add_argument('output', type=Path)
    args = parser.parse_args()
    if args.command == 'prepare':
        result = prepare(args.output, args.source, args.upstream, args.commit)
    else:
        result = verify(args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get('passed') is False:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
