"""Compile the frozen author's actual UpdatePredictCnt() with field-only stubs.

This is a source-understanding test, not a complete ColdMap implementation or
device experiment. It does not execute anything from the upstream build scripts.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess


COMMIT = 'e03c70760371b540964dac8b3a0c2596c06cf418'
FILE_SHA256 = 'fb6a07ccfcf56297c0e109e71b3f457eb22ce73955ef4e431e63d80559337c66'
FUNCTION_SHA256 = 'f59bea249d2a2035d47b46caceccfb0b41565bf3e8d1f7dee1e0e894ec0127ac'

PREAMBLE = r'''
#include <atomic>
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <deque>

// Backend consists only of the fields read/written by the exact upstream method.
class ZonedBlockDevice {
 public:
  uint64_t predict_cnt_ = 0;
  bool adaptive_predict_cnt_ = false;
  std::atomic<uint64_t> current_compaction_count_{0};
  bool is_first_zc_ = true;
  static constexpr size_t MOVING_AVG_WINDOW_K = 3;
  std::deque<uint64_t> compaction_history_;
  void UpdatePredictCnt();
};
'''

CASES = r'''
int main() {
  ZonedBlockDevice fixed;
  fixed.predict_cnt_ = 9;
  fixed.current_compaction_count_ = 7;
  fixed.UpdatePredictCnt();
  assert(fixed.predict_cnt_ == 9 && !fixed.is_first_zc_);
  assert(fixed.current_compaction_count_ == 0 && fixed.compaction_history_.empty());
  fixed.current_compaction_count_ = 100;
  fixed.UpdatePredictCnt();
  assert(fixed.predict_cnt_ == 9 && fixed.current_compaction_count_ == 0);

  ZonedBlockDevice adaptive;
  adaptive.adaptive_predict_cnt_ = true;
  adaptive.current_compaction_count_ = 7;
  adaptive.UpdatePredictCnt();
  assert(adaptive.predict_cnt_ == 4 && !adaptive.is_first_zc_);
  // The first invocation uses an initial value; it does not add that count.
  assert(adaptive.current_compaction_count_ == 0 && adaptive.compaction_history_.empty());
  const uint64_t intervals[] = {6, 11, 2, 0, 0};
  const uint64_t expected[] = {6, 8, 6, 4, 0};
  for (size_t i = 0; i != 5; ++i) {
    adaptive.current_compaction_count_ = intervals[i];
    adaptive.UpdatePredictCnt();
    assert(adaptive.predict_cnt_ == expected[i]);
    assert(adaptive.current_compaction_count_ == 0);
    assert(adaptive.compaction_history_.size() == (i < 3 ? i + 1 : 3));
  }
  assert((adaptive.compaction_history_ == std::deque<uint64_t>{2, 0, 0}));
  // Zero is allowed by the author's implementation; do not silently clamp to 1.
  assert(adaptive.predict_cnt_ == 0);

  ZonedBlockDevice initial;
  initial.adaptive_predict_cnt_ = true;
  initial.predict_cnt_ = 99;
  initial.UpdatePredictCnt();
  assert(initial.predict_cnt_ == 99 && initial.compaction_history_.empty());
  std::puts("actual upstream UpdatePredictCnt: fixed/initial/window/rounding/zero passed");
}
'''


def run(upstream, output):
    upstream = Path(upstream).resolve()
    actual_commit = subprocess.check_output(
        ['git', '-C', str(upstream), 'rev-parse', 'HEAD'], text=True).strip()
    if actual_commit != COMMIT:
        raise ValueError('上游提交身份不符')
    path = upstream / 'rocksdb/plugin/zenfs/fs/zbd_zenfs.cc'
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != FILE_SHA256:
        raise ValueError('上游源文件身份不符')
    text = data.decode()
    start = text.index('void ZonedBlockDevice::UpdatePredictCnt() {')
    end = text.index('\nvoid ZonedBlockDevice::GiveZenFStoLSMTreeHint(', start)
    function = text[start:end]
    if hashlib.sha256(function.encode()).hexdigest() != FUNCTION_SHA256:
        raise ValueError('上游函数范围身份不符')
    output = Path(output).resolve()
    if output.exists():
        raise ValueError('不覆盖已有本地测试')
    if output.is_relative_to(upstream) or upstream.is_relative_to(output):
        raise ValueError('测试输出不能与上游目录重叠')
    output.mkdir(parents=True)
    source = output / 'actual_upstream_window.cc'
    source.write_text(PREAMBLE + function + CASES)
    binary = output / 'actual_upstream_window'
    argv = ['g++', '-std=c++17', '-O1', '-g', '-Wall', '-Wextra', '-Werror',
            '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
            str(source), '-o', str(binary)]
    with (output / 'compile.log').open('x') as log:
        subprocess.run(argv, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=60)
    with (output / 'run.log').open('x') as log:
        subprocess.run([str(binary)], stdout=log, stderr=subprocess.STDOUT,
                       check=True, timeout=20)
    report = dict(passed=True, upstream_commit=COMMIT, upstream_file_sha256=FILE_SHA256,
                  upstream_function_sha256=FUNCTION_SHA256, function_body_modified=False,
                  compiler_command=argv, source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                  compiler=subprocess.check_output(['g++', '--version'], text=True).splitlines()[0],
                  backend='仅字段替身；实际编译作者原函数', sanitizer='ASan+UBSan',
                  full_coldmap_implemented=False, device_test=False, performance_test=False)
    with (output / 'result.json').open('x') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upstream', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    arguments = parser.parse_args()
    run(arguments.upstream, arguments.output)
