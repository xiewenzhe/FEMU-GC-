"""持续读执行核验、旧入口隔离和部署资源保护；合成日志不作为实验结果。"""
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from analyze_window_credit import audit_scores
from analyze_joint_opportunity import fields
from replay_persistent_credit import oracle,SECOND
from test_window_credit_host import fixture,log
import run_persistent_credit_host as host
from run_window_credit_host import WindowRun
from run_rr_fair_long_v2_host import CollectedRun


def persistent_fixture():
    lines=fixture();v=8*2**20;cap=128*2**20
    out=oracle((128,v,cap,1000,240,480,7,100*SECOND,1024,3,
        1,128,3,v,0,2*SECOND,0,2*SECOND,1,16,SECOND))
    changed=[]
    for s in lines:
        if '近期ETA对照：' in s:
            r=fields(s,'近期ETA对照：')
            r.update(近期可知=1,近期增量=16,间隔=SECOND,前快照=99*SECOND,前RC=984,
                前会话='1/2',前代际=3,前块=4,前PPA='0/0/0/4',前起点=0,新ETA=1500000000)
            s=log('近期ETA对照',**r)
        for tag in ('窗口收益成本','统一成本'):
            if tag+'：' in s:
                r=fields(s.split('，原因=',1)[0],tag+'：')
                r.update(风险可知=out[0],ETA=out[1],预计留存=out[2],RR抵扣=out[3],净复制=out[4])
                s=log(tag,**r)
        changed.append(s)
    changed.append(log('持续读收益配置',请求=1,启用=1,版本=1,
        短间隔最小=SECOND//2,短间隔最大=2*SECOND,原窗口留存不变=1,先选Greedy=0))
    changed.append(log('持续读收益成本',轮次=1,Zone=128,版本=1,
        平均ETA=out[8],近期ETA=out[9],修正ETA=out[1],近期可知=1,近期增量=16,
        近期间隔=SECOND,原抵扣=out[10],修正抵扣=out[3],原因='合成测试，不是实测'))
    return changed


class PersistentHostTest(unittest.TestCase):
    def test_new_formula_requires_explicit_mode_and_full_evidence(self):
        lines=persistent_fixture()
        result=audit_scores(lines,'window',persistent=True)
        self.assertEqual(result[0]['ETA'],1500000000)
        self.assertGreater(result[0]['RR抵扣'],0)
        with self.assertRaises(ValueError):audit_scores(lines,'window')
        with self.assertRaises(ValueError):audit_scores(fixture(),'window',persistent=True)
        with self.assertRaises(ValueError):audit_scores(lines,'greedy',persistent=True)

    def test_missing_or_tampered_evidence_rejected(self):
        lines=persistent_fixture()
        for marker in ('持续读收益配置：','持续读收益成本：','近期ETA对照：','窗口收益成本：'):
            with self.subTest(marker=marker),self.assertRaises(ValueError):
                audit_scores([s for s in lines if marker not in s],'window',persistent=True)
        for marker,old,new in (
            ('近期ETA对照：','前块=4','前块=5'),('近期ETA对照：','前代际=3','前代际=4'),
            ('持续读收益配置：','启用=1','启用=0'),
            ('持续读收益成本：','近期间隔=1000000000','近期间隔=2000000000'),
            ('持续读收益成本：','修正抵扣=','修正抵扣=9'),
            ('统一成本：','净复制=','净复制=9')):
            broken=[s.replace(old,new) if marker in s else s for s in lines]
            with self.subTest(marker=marker,old=old),self.assertRaises(ValueError):
                audit_scores(broken,'window',persistent=True)

    def test_shell_flags_and_frozen_parent(self):
        for n in ('build_persistent_credit_guest.sh','run_persistent_credit_guest.sh'):
            subprocess.run(['bash','-n',str(ROOT/'tests'/n)],check=True)
        self.assertTrue(all(p.is_file() for p in host.FILES))
        self.assertEqual(host.PARENT.name,'joint-window-credit-v2-20261021-build.qcow2')
        self.assertEqual(host.PersistentRun.finish,CollectedRun.finish)
        self.assertEqual(host.PersistentRun.close_guest,CollectedRun.close_guest)
        self.assertEqual(host.PersistentRun.start,WindowRun.start)
        wrapper=(ROOT/'tests/run_persistent_credit_guest.sh').read_text()
        for value in ('ZENFS_RRZC_PERSISTENT_CREDIT="$persistent"',
            'ZENFS_RRZC_WINDOW_CREDIT="$window"','ZENFS_RRZC_PAIR_GREEDY="$greedy"',
            'ZENFS_RR_FAIR_QUEUE=1','ZENFS_RRZC_RECLAIM_BYTES=0','持续读开关=%s'):
            self.assertIn(value,wrapper)
        self.assertNotIn('GC_START',wrapper)
        self.assertNotIn('ZENFS_RRZC_PERSISTENT_CREDIT',
            (ROOT/'tests/run_window_credit_guest.sh').read_text())

    def test_source_controlled_results_and_original_headers_reaudited(self):
        if not host.MANIFEST.is_file():self.skipTest('实际父实验不可用')
        result=host.checked_source()
        self.assertEqual(len(result),4)
        self.assertTrue(all(r['配对验收通过'] for r in result.values()))

    def test_live_project_vm_still_refuses_overlap(self):
        reply=subprocess.CompletedProcess([],0,'123 xwz 8000000 qemu-system-x86_64 -name FEMU-joint-other','')
        with patch('run_window_credit_host.subprocess.run',return_value=reply),self.assertRaises(RuntimeError):
            host.resources({})

    def test_old_real_pair_still_passes_original_analysis(self):
        from analyze_window_ycsb import compare_pair
        root=Path('/home/data/xwz/rrzc-baselines/joint-window-ycsb-timed-v1-20261021-results')
        if not (root/'short配对核验.json').is_file():self.skipTest('真实旧配对不可用')
        original=json.loads((root/'short配对核验.json').read_text())
        current=compare_pair(root,'short')
        self.assertEqual(current,original)


if __name__=='__main__':unittest.main()
