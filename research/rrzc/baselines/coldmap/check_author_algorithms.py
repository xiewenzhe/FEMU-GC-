"""Compile seven unmodified author method bodies; surface behavior differences before porting."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

HERE = Path(__file__).resolve().parent
SOURCE_SHA256 = 'd91d0b233393541147b59d71867ab27941e3a9ca8382d02878d7be7d61c6a70a'
FUNCTIONS = [('void', 'CalculateHorizontalLifetimes'), ('void', 'ReCalculateLifetimes'),
             ('void', 'PredictCompaction'), ('void', 'GetOverlappingFno'), ('void', 'Propagation'),
             ('uint64_t', 'GetMaxLevelScoreLevel'), ('uint64_t', 'GetMaxHorizontalFno')]


def run(upstream, include, output):
    assert not output.exists(), 'refuse overwrite'
    source = upstream / 'rocksdb/plugin/zenfs/fs/fs_zenfs.cc'
    assert hashlib.sha256(source.read_bytes()).hexdigest() == SOURCE_SHA256
    text = source.read_text(); blocks = []; hashes = {}
    for result, name in FUNCTIONS:
        matches = list(re.finditer(r'^' + result + r' ZenFS::' + name + r'\(', text, re.M))
        assert len(matches) == 1, name
        start = matches[0].start(); end = text.index('\n}', start) + 2
        body = text[start:end]; blocks.append(body)
        hashes[name] = hashlib.sha256(body.encode()).hexdigest()
    output.mkdir(parents=True)
    (output / 'author_functions.inc').write_text('\n\n'.join(blocks) + '\n')
    binary = output / 'author-test'
    command = ['g++', '-std=c++17', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
               '-Wno-unused-parameter', '-fsanitize=undefined', '-fno-sanitize-recover=all',
               '-I' + str(include), '-I' + str(HERE), '-I' + str(output),
               str(HERE / 'author_reference_test.cc'), '-o', str(binary)]
    with (output / 'compile.log').open('x') as log:
        subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=120)
    with (output / 'run.log').open('x') as log:
        subprocess.run([str(binary)], stdout=log, stderr=subprocess.STDOUT, check=True, timeout=10)
    timed_out = False
    with (output / 'inconsistent-snapshot.log').open('x') as log:
        try: subprocess.run([str(binary), 'missing-file'], stdout=log, stderr=subprocess.STDOUT, check=True, timeout=1)
        except subprocess.TimeoutExpired: timed_out = True
    assert timed_out, 'original no-progress behavior changed; inspect the source'
    report = dict(reference_behavior_verified=True, author_functions_modified=False,
                  upstream_file_sha256=SOURCE_SHA256, function_sha256=hashes,
                  compiler_command=command, extent_split_changes_zone_average=True,
                  inconsistent_snapshot_nontermination_observed=True,
                  timeout_cleanup='subprocess.run timeout terminated only this known standalone child',
                  full_coldmap_implemented=False, device_verified=False,
                  scope='unmodified author methods with field-only metadata backend; not the current RocksDB snapshot interface')
    (output / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upstream', type=Path, required=True); parser.add_argument('--include', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True); args = parser.parse_args()
    run(args.upstream.resolve(), args.include.resolve(), args.output.resolve())
