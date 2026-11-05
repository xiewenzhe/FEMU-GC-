"""不限速配对入口边界；合成数据只作测试，不冒充YCSB设备结果。"""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from analyze_stable_ycsb import sizes,compare_pair,digest
import run_stable_ycsb_host as host


class StableYcsbTest(unittest.TestCase):
    def test_explicit_stages_and_trace_file_budget(self):
        self.assertEqual(sizes('smoke'),(8192,8192,64,2048,10))
        self.assertEqual(sizes('short'),(491520,400000,4096,65536,30))
        self.assertEqual(sizes('long'),(491520,4000000,4096,65536,30))
        self.assertLess((491520+4000000)*37,256<<20)
        with self.assertRaises(ValueError):sizes('其他')

    def test_same_guards_new_symbols_and_no_limiter(self):
        for name in ('build_stable_ycsb_guest.sh','run_stable_ycsb_guest.sh'):
            subprocess.run(['bash','-n',str(ROOT/'tests'/name)],check=True)
        wrapper=(ROOT/'tests/run_stable_ycsb_guest.sh').read_text()
        for flag in ('ZENFS_RRZC_DIRECT=1','ZENFS_RRZC_PAIR_GREEDY="$greedy"',
                     'ZENFS_RRZC_STABLE_CREDIT="$stable"','ZENFS_RRZC_RECLAIM_BYTES=0',
                     'ZENFS_RR_ENABLE=1','ZENFS_RR_ENABLE=0'):
            self.assertIn(flag,wrapper)
        self.assertNotIn('limit.ops=1000',wrapper);self.assertNotIn('sleep 0.',wrapper)
        self.assertLess(wrapper.index("printf 'mq-deadline"),wrapper.index('"$target/sst_device_probe"'))
        build=(ROOT/'tests/build_stable_ycsb_guest.sh').read_text()
        self.assertIn('ZENFS_RRZC_STABLE_CREDIT',build);self.assertIn('ZENFS_RRZC_PAIR_GREEDY',build)
        self.assertIn('stable.build.finished',build)
        self.assertIn('workload-stable-unlimited.properties',[p.name for p in host.FILES])
        self.assertEqual(host.STAGES,('smoke','short','long'))

    def pair(self,tmp):
        # 明确是配对分析器样例；没有调用任何设备。
        sample=dict(请求SHA256='相同请求',最终值SHA256='相同最终值',RR次数=2,ZC次数=3,
                    总迁移字节=100,总成功ZoneReset=6,推导物理Block擦除次数=768)
        for mode in ('greedy','stable'):
            out=Path(tmp)/('short-'+mode);out.mkdir();folder=out/('short-'+mode);folder.mkdir()
            (out/'阶段归档.json').write_text(json.dumps(dict(完整=True)))
            (folder/'software.before.log').write_text('同库: OK\n')
            (folder/'workload.properties').write_text('limit.ops=0\n')
        return sample

    def test_pair_rejects_different_trace_configuration_or_library(self):
        with tempfile.TemporaryDirectory() as tmp:
            a=self.pair(tmp);b=copy.deepcopy(a);b['RR次数']=1
            with patch('analyze_stable_ycsb.analyze_arm',side_effect=[a,b]):
                result=compare_pair(tmp,'short');self.assertTrue(result['配对验收通过'])
                self.assertEqual(result['模型相对Greedy差值']['RR次数'],-1)
            b['请求SHA256']='另一条请求'
            with patch('analyze_stable_ycsb.analyze_arm',side_effect=[a,b]):
                with self.assertRaises(ValueError):compare_pair(tmp,'short')
            b['请求SHA256']=a['请求SHA256']
            folder=Path(tmp)/'short-stable/short-stable'
            for name in ('software.before.log','workload.properties'):
                previous=(folder/name).read_text();(folder/name).write_text('不同条件')
                with patch('analyze_stable_ycsb.analyze_arm',side_effect=[a,b]):
                    with self.assertRaises(ValueError):compare_pair(tmp,'short')
                (folder/name).write_text(previous)

    def test_frozen_dependency_paths_exist_and_core_is_parent(self):
        self.assertTrue(all(p.exists() for p in host.FILES))
        self.assertEqual(host.PARENT.name,'joint-stable-credit-v1-20261021-build.qcow2')
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'data';p.write_bytes(b'abc')
            self.assertEqual(digest(p),'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')


if __name__=='__main__':unittest.main()
