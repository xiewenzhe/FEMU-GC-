"""第三阶段入口接线测试；模拟命令，不访问设备，不代表设备验收。"""
import json
from pathlib import Path
import tarfile
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import run_placement_device_guest as guest
import run_placement_integration_host as host
from placement_device_source import extend
from ycsb_af import digest
from analyze_placement_integration import analyze as audit
from analyze_placement_trace import analyze as original_audit


class IntegrationTest(unittest.TestCase):
    def test_device_commands_and_modes_only_touch_verified_blank_device(self):
        class Scheduler:
            selected=False
            def read_text(self):return '[mq-deadline] none' if self.selected else '[none] mq-deadline'
            def write_text(self,value):
                assert value=='mq-deadline\n';self.selected=True
        all_env=[]
        for mode in ('G0','G1','G2'):
            scheduler=Scheduler();calls=[]
            def path(value):return scheduler if str(value)=='/sys/block/nvme0n1/queue/scheduler' else Path(value)
            def query(argv,**kwargs):
                if argv[0]=='blockdev':return '8589934592\n'
                if 'report-zones' in argv:
                    return json.dumps(dict(zone_list=[dict(state='EMPTY',slba=i*262144,wp=i*262144) for i in range(64)]))
                if 'id-ns' in argv:return json.dumps(dict(mar=11,mor=7))
                raise AssertionError(argv)
            def run(argv,**kwargs):
                calls.append(argv);all_env.append((mode,kwargs['env']))
                return SimpleNamespace(returncode=0)
            with tempfile.TemporaryDirectory() as name,patch.object(guest,'Path',path),\
                patch.object(guest.os,'geteuid',return_value=0),\
                patch.object(guest.subprocess,'check_output',side_effect=query),\
                patch.object(guest.subprocess,'run',side_effect=run):
                guest.exercise(Path(name),mode)
                self.assertTrue(scheduler.selected)
                self.assertIn(['nvme','zns','reset-zone','/dev/nvme0n1','-a'],calls)
                self.assertEqual(sum(x[1]=='mkfs' for x in calls if len(x)>1),2)
                self.assertNotIn('--enable_gc',calls[-2])
        for mode,env in all_env:
            self.assertEqual(env['ZENFS_RRZC_PLACEMENT_MODE'],mode)
            self.assertFalse(any(k in env for k in host.__dict__.get('OLD_KEYS',('ZENFS_RRZC_POOL_K','ZENFS_RRZC_POOL_FLUSH_W'))))

    def test_wrong_capacity_prevents_any_mutating_command(self):
        with tempfile.TemporaryDirectory() as name,patch.object(guest.os,'geteuid',return_value=0),\
            patch.object(guest.subprocess,'check_output',return_value='17179869184'),\
            patch.object(guest.subprocess,'run') as run:
            with self.assertRaisesRegex(AssertionError,'非8GiB'):guest.exercise(Path(name),'G0')
            run.assert_not_called()

    def test_reuse_preserves_executable_bits_in_payload_and_hash(self):
        with tempfile.TemporaryDirectory() as name:
            base=Path(name);previous=base/'previous-results';previous.mkdir()
            old=previous/'previous';old.mkdir();output=base/'new-results';output.mkdir()
            archive=previous/'archive.tar';archive.write_bytes('本地模拟归档'.encode())
            proof=dict(通过=True,完整五单元编译=True)
            for file,key in [('db_bench','程序SHA256'),('placement_probe','设备小测SHA256')]:
                p=old/file;p.write_bytes(b'\x7fELF'+file.encode());p.chmod(0o755);proof[key]=digest(p)
            (old/'集成构建核验.json').write_text(json.dumps(proof))
            with patch.object(host,'IMAGE_DIR',base),patch.object(host,'closed_archive',return_value=({'标签':'previous'},archive)):
                payload=host.prepare_reuse(output,previous)
            with tarfile.open(payload) as tar:
                for file,key in [('db_bench','程序SHA256'),('placement_probe','设备小测SHA256')]:
                    self.assertEqual(digest(output/'源码'/file),proof[key]);self.assertTrue(tar.getmember(file).mode&0o111)

    def test_probe_keeps_original_rr_safety_checks_and_adds_remount(self):
        source=(host.ROOT/'tests/rr_path_device.cc').read_text();new=extend(source)
        self.assertEqual(new.count('int main('),1);self.assertEqual(new.count('int Exercise('),1)
        for line in source.splitlines():
            if line!='int main(int argc,char** argv) {':self.assertIn(line,new)
        self.assertIn('重挂载内容错误',new);self.assertIn('删除后仍存在文件映射',new)

    def test_real_empty_file_early_retirement_is_verified_not_ignored(self):
        for size in (0,512):
            items=[(20,0,3,[]),(16,1,2,[]),(2,1,2,[4,3,0,0,0,1,size]),(4,1,2,[]),(19,1,2,[])]
            with tempfile.TemporaryDirectory() as name:
                path=Path(name)/'empty.bin'
                with path.open('wb') as f:
                    f.write(b'RRZCCP01'+struct.pack('<3Q',len(items),0,1))
                    for seq,(kind,device,owner,args) in enumerate(items,1):
                        row=[seq,seq,1,kind,device,owner,0,*args];row += [0]*(16-len(row))
                        f.write(struct.pack('<16Q',*row))
                if size:
                    with self.assertRaisesRegex(ValueError,'零长退役文件'):audit(path)
                else:
                    self.assertEqual(len(audit(path)['空文件延后登记退役']),1)
                    with self.assertRaisesRegex(ValueError,'退役缺少对象'):original_audit(path)


if __name__=='__main__':unittest.main()
