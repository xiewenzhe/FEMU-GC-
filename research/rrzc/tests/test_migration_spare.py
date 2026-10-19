"""提取本轮真实资源函数，使用既有内存Zone替身注入失败；不冒充设备实验。"""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]


class MigrationSpare(unittest.TestCase):
    def test_real_functions_ownership_and_failures(self):
        source = (ROOT / 'build-femu/rrzc/migration-spare-work/fs/zbd_zenfs.cc').read_text()
        fixture = (ROOT / 'research/rrzc/tests/migration_reserve_test.cc').read_text().split('int main() {', 1)[0]
        fixture = fixture.replace('static IOStatus NoSpace',
            'static IOStatus Corruption(const char*) { return {false}; }\n  static IOStatus NoSpace')
        fixture = fixture.replace('Zone* migration_reserve_zone_ = nullptr;',
            'Zone* migration_reserve_zone_ = nullptr;\n'
            '  bool migration_spare_enabled_ = false;\n'
            '  Zone* migration_spare_zone_ = nullptr;')
        methods = []
        for name in ('EnsureMigrationReserve', 'PrepareReadReclaimSource', 'ReleaseMigrateZone', 'TakeMigrateZone'):
            match = re.search(r'^IOStatus ZonedBlockDevice::' + name + r'\([\s\S]*?^}', source, re.M)
            self.assertIsNotNone(match)
            methods.append(match[0])
        body = r'''
int main() {
  // 默认关闭时仍只保留一个区，Finish失败释放临时替补。
  {
    ZonedBlockDevice d; Zone a,b; d.io_zones={&a,&b}; reserve(d);
    assert(d.migration_reserve_zone_==&a && !b.busy && !d.migration_spare_zone_);
    used(a,256); a.fail_finish=true; Zone* out=nullptr;
    assert(!d.TakeMigrateZone(&out,Env::WLTH_MEDIUM,512).ok());
    assert(!out && !d.migrating_ && a.busy && !b.busy);
  }
  // 开启时失败仍保留两区独占权；可重试，不凭空释放空替补给普通写入。
  {
    ZonedBlockDevice d; Zone a,b; d.io_zones={&a,&b}; d.migration_spare_enabled_=true; reserve(d);
    assert(a.busy && b.busy && d.migration_spare_zone_==&b);
    used(a,256); a.fail_finish=true; Zone* out=nullptr;
    assert(!d.TakeMigrateZone(&out,Env::WLTH_MEDIUM,512).ok());
    assert(!out && !d.migrating_ && a.busy && b.busy && d.migration_spare_zone_==&b);
    a.fail_finish=false;
    assert(take(d)==&b && d.migration_reserve_zone_==&b && !d.migration_spare_zone_);
    assert(!a.busy && a.used_capacity_==1024 && a.resets==0 && d.active_tokens==0);
    assert(d.ReleaseMigrateZone(&b).ok() && b.busy);
  }
  // 当前写满后提升空替补，不退还不存在的普通活跃令牌，并补上第三个空区。
  {
    ZonedBlockDevice d; Zone a,b,c; d.io_zones={&a,&b,&c}; d.migration_spare_enabled_=true; reserve(d);
    assert(take(d)==&a); used(a,0);
    assert(d.ReleaseMigrateZone(&a).ok());
    assert(!a.busy && b.busy && c.busy && d.migration_reserve_zone_==&b && d.migration_spare_zone_==&c);
    assert(d.active_tokens==0);
  }
  // 当前迁移区本身成为RR源时，不把它当目标；下一空替补接任。
  {
    ZonedBlockDevice d; Zone a,b; d.io_zones={&a,&b}; d.migration_spare_enabled_=true; reserve(d);
    used(a,2048); bool prepared=false; uint64_t sacrificed=0;
    assert(d.PrepareReadReclaimSource(&a,1024,a.wp_,&prepared,&sacrificed).ok());
    assert(prepared && sacrificed==2048 && !a.busy && a.used_capacity_==1024 && a.resets==0);
    assert(d.migration_reserve_zone_==&b && b.busy && !d.migration_spare_zone_ && d.active_tokens==0);
  }
  // 非空或容量不足的替补属于约定破坏，必须拒绝且不Finish当前区。
  for (bool nonempty:{false,true}) {
    ZonedBlockDevice d; Zone a,b; d.io_zones={&a,&b}; d.migration_spare_enabled_=true; reserve(d);
    used(a,256); if(nonempty) used(b,1024); else b.capacity_=256;
    Zone* out=nullptr;
    assert(!d.TakeMigrateZone(&out,Env::WLTH_MEDIUM,512).ok());
    assert(!out && a.finishes==0 && a.busy && b.busy && !d.migrating_);
  }
  std::cout << "真实资源函数的默认、失败归属、满区接续、RR源切换和异常替补检查通过\n";
}
'''
        with tempfile.TemporaryDirectory(prefix='rrzc-spare-unit-') as directory:
            path = Path(directory)
            (path / 'migration-under-test.inc').write_text('\n\n'.join(methods))
            (path / 'test.cc').write_text(fixture + body)
            compile_result = subprocess.run(['g++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                '-pthread', str(path / 'test.cc'), '-o', str(path / 'test')], capture_output=True, text=True)
            self.assertEqual(compile_result.returncode, 0, compile_result.stderr)
            run = subprocess.run([str(path / 'test')], capture_output=True, text=True, timeout=10)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertIn('检查通过', run.stdout)


if __name__ == '__main__':
    unittest.main()
