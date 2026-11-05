"""隔离部署、真实执行核验和失败收尾的单元测试；合成输入不是设备结果。"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from analyze_window_credit import audit_scores, exact_history
from replay_window_credit import retention, oracle, SECOND, MAX
import run_window_credit_host as host


def point(t,v):
    return dict(阶段='采样',轮次='0',会话='1/2',Zone='128',时点纳秒=str(t*SECOND),有效字节=str(v),
        可知满区='1',设备代际='3',主机代际='3',主机序号='6',迁移实例='1',迁移创建纳秒='1',
        迁移前纳秒=str(t*SECOND-1),迁移后纳秒=str(t*SECOND+1),迁移可信='1',
        迁移序号='0/0',迁移活动='0/0',迁移末次结束='0/0')


def log(tag,**kw):return tag+'：'+'，'.join(f'{k}={v}' for k,v in kw.items())


def fixture():
    """明确标记合成：只验证数据关联和拒绝边界，不生成实验结论。"""
    v=8*2**20;cap=128*2**20
    old=[point(98,v),point(99,v)];now=point(100,v);now.update(阶段='选区',轮次='1')
    lines=[log('窗口收益配置',请求=1,启用=1,版本=1,最大支撑窗口=10*SECOND,先选Greedy=0,原候选资格不变=1)]
    for n,p in enumerate(old,1):
        lines.append(log('留存原始采样',**p))
        lines.append(log('窗口收益历史发布',发布序号=n,可用=1,迁移后纳秒=p['迁移后纳秒'],会话='1/2'))
    lines += [log('窗口收益历史冻结',轮次=1,发布序号=2),log('留存原始采样',**now),
        log('稳定收益配对模式',轮次=1,Greedy=0,稳定模型=0,共用安全复核=1),
        log('窗口收益模式',轮次=1,执行=1,版本=1,时间尺度=10*SECOND,先选Greedy=0,历史序号=2),
        log('V1影子候选',轮次=1,Zone=128,原候选资格=1,统计准入=1,设备会话='1/2',设备代际=3,
            块编号=4,PPA='0/0/0/4',快照纳秒=100*SECOND,代际起点纳秒=0,当前RC=1000,
            十秒真实增量=240,三十秒真实增量=480,窗口标志=7),
        log('近期ETA对照',轮次=1,Zone=128,会话='1/2',代际=3,块=4,PPA='0/0/0/4',
            快照=100*SECOND,起点=0,RC=1000,十秒增量=240,阈值=1024,原ETA=SECOND,
            原抵扣=0,近期可知=0,新ETA=MAX,新抵扣=0)]
    f=retention([{128:p} for p in old],now)
    result=oracle((128,v,cap,1000,240,480,7,100*SECOND,1024,3,*f))
    risk,eta,retained,credit,net,reclaimed,span,window=result
    lines += [log('窗口收益成本',轮次=1,Zone=128,风险可知=risk,ETA=eta,预计留存=retained,RR抵扣=credit,
        净复制=net,读间隔=span,读增量=240,支撑窗口=window,留存可知=f[0],留存Zone=f[1],留存代际=f[2],
        留存有效=f[3],短减少=f[4],短间隔=f[5],长减少=f[6],长间隔=f[7],原因='合成测试，不是实测'),
        log('统一成本',轮次=1,Zone=128,有效=v,回收=reclaimed,风险可知=risk,ETA=eta,
            预计留存=retained,RR抵扣=credit,净复制=net,安全=1,留存可知=f[0]),
        log('统一决策',轮次=1,实际存在=1,实际Zone=128)]
    return lines


class WindowHostTest(unittest.TestCase):
    def test_shell_syntax_and_explicit_flags(self):
        for n in ('build_window_credit_guest.sh','run_window_credit_guest.sh'):
            subprocess.run(['bash','-n',str(ROOT/'tests'/n)],check=True)
        wrapper=(ROOT/'tests/run_window_credit_guest.sh').read_text()
        for value in ('ZENFS_RRZC_WINDOW_CREDIT="$window"','ZENFS_RRZC_PAIR_GREEDY="$greedy"',
            'ZENFS_RRZC_STABLE_CREDIT=0','ZENFS_RR_FAIR_QUEUE=1','ZENFS_RRZC_RECLAIM_BYTES=0',
            'ZENFS_RR_ENABLE=0','ZENFS_RR_ENABLE=1'):
            self.assertIn(value,wrapper)
        self.assertNotIn('reset-zone',wrapper);self.assertNotIn('GC_START',wrapper)
        self.assertEqual(host.PARENT.name,'joint-rr-fair-ycsb-v1-20261021-build.qcow2')
        self.assertIn('joint_opportunity_smoke.cc',[p.name for p in host.FILES])

    def test_utf8_binary_guard_not_ascii_strings(self):
        script=(ROOT/'tests/build_window_credit_guest.sh').read_text()
        guard=script.split('python3 - "$target" <<\'PY\'\n',1)[1].split('\nPY\n',1)[0]
        self.assertNotIn('strings "$target',script)
        with tempfile.TemporaryDirectory() as tmp:
            binary=Path(tmp)/'joint_opportunity_smoke'
            # 合成二进制样本仅用于字节核验，不冒充编译/运行结果。
            binary.write_bytes(b'ZENFS_RRZC_WINDOW_CREDIT\0'+'窗口收益模式：'.encode())
            subprocess.run([sys.executable,'-',tmp],input=guard,text=True,check=True,capture_output=True)
            self.assertIn('实际执行仍需运行日志核验',(Path(tmp)/'执行分支.log').read_text())
            binary.write_bytes(b'ZENFS_RRZC_WINDOW_CREDIT\0')
            result=subprocess.run([sys.executable,'-',tmp],input=guard,text=True,capture_output=True)
            self.assertNotEqual(result.returncode,0)

    def test_actual_failed_build_binary_has_utf8_marker(self):
        binary=Path('/home/data/xwz/rrzc-baselines/joint-window-credit-v1-20261021-results/build/joint-window-credit-v1-20261021/joint_opportunity_smoke')
        if not binary.is_file():self.skipTest('本轮失败归档不可用，不伪造编译证据')
        raw=binary.read_bytes()
        self.assertIn(b'ZENFS_RRZC_WINDOW_CREDIT',raw)
        self.assertIn('窗口收益模式：'.encode(),raw)
        # 复现默认strings不能可靠保留中文标记的原始失败。
        strings=subprocess.check_output(['strings',str(binary)])
        self.assertNotIn('窗口收益模式：'.encode(),strings)

    def test_scores_from_exact_sequence(self):
        result=audit_scores(fixture(),'window')
        self.assertEqual(len(result),1);self.assertGreater(result[0]['RR抵扣'],0)

    def test_missing_mode_or_old_log_must_not_pass(self):
        with self.assertRaises(ValueError):audit_scores([],'window')
        lines=fixture()
        for token in ('窗口收益配置：','窗口收益模式：','窗口收益成本：','窗口收益历史发布：'):
            with self.subTest(token=token),self.assertRaises(ValueError):
                audit_scores([s for s in lines if token not in s],'window')
        with self.assertRaises(ValueError):audit_scores(lines,'greedy')

    def test_reject_tampered_identity_or_retention_or_score(self):
        for marker,old,new in [('近期ETA对照：','块=4','块=5'),
            ('窗口收益成本：','短间隔=2000000000','短间隔=3000000000'),
            ('统一成本：','留存可知=1','留存可知=0'),
            ('窗口收益历史冻结：','发布序号=2','发布序号=3'),
            ('窗口收益模式：','历史序号=2','历史序号=1')]:
            lines=[s.replace(old,new) if marker in s else s for s in fixture()]
            with self.subTest(marker=marker),self.assertRaises(ValueError):audit_scores(lines,'window')

    def test_freeze_can_log_before_publication_message(self):
        lines=fixture();publish=next(s for s in lines if '窗口收益历史发布：发布序号=2' in s)
        lines.remove(publish)
        position=next(n for n,s in enumerate(lines) if '窗口收益历史冻结：' in s)
        lines.insert(position+1,publish)
        self.assertGreater(audit_scores(lines,'window')[0]['RR抵扣'],0)

    def test_failed_publication_clears_history(self):
        lines=fixture()
        lines=[s.replace('可用=1','可用=0') if '窗口收益历史发布：发布序号=2' in s else s for s in lines]
        history,current,_=exact_history(lines)
        self.assertEqual(history[1],[])
        with self.assertRaises(ValueError):audit_scores(lines,'window')

    def test_diagnostic_only_requires_explicit_ineligibility_and_correct_values(self):
        lines=fixture()
        extra=[s.replace('Zone=128','Zone=256') for s in lines if
            any(t+'：' in s for t in ('V1影子候选','近期ETA对照','窗口收益成本','留存原始采样'))]
        extra=[s.replace('原候选资格=1','原候选资格=0') for s in extra]
        extra=[s+'，有效字节='+str(8*2**20) if 'V1影子候选：' in s else s for s in extra]
        # 原始帧中的两个Zone同一发布批次，实际合格区仍只有一个。
        self.assertEqual(len(audit_scores(lines+extra,'window')),1)
        with self.assertRaises(ValueError):
            audit_scores(lines+[s.replace('原候选资格=0','原候选资格=1') for s in extra],'window')
        with self.assertRaises(ValueError):
            audit_scores(lines+[s for s in extra if 'V1影子候选：' not in s],'window')
        broken=[s.replace('净复制=','净复制=9') if '窗口收益成本：' in s else s for s in extra]
        with self.assertRaises(ValueError):audit_scores(lines+broken,'window')

    def test_missing_eligible_cost_is_not_treated_as_diagnostic_only(self):
        lines=[s for s in fixture() if '统一成本：' not in s]
        with self.assertRaises(ValueError):audit_scores(lines,'window')

    def test_real_ycsb_candidate_sets_are_reaudited(self):
        path=Path('/home/data/xwz/rrzc-baselines/joint-window-ycsb-v2-20261021-results/short-window/short-window/zenfs.run.log')
        if not path.is_file():self.skipTest('真实YCSB归档不可用，不伪造通过')
        rows=audit_scores(path.read_text().splitlines(),'window')
        self.assertEqual(len(rows),42)
        self.assertEqual(sum(r['RR抵扣']>0 for r in rows),2)

    def test_live_project_vm_refuses_overlap(self):
        reply=subprocess.CompletedProcess([],0,'123 xwz 8000000 qemu-system-x86_64 -name FEMU-joint-other','')
        with patch.object(host.subprocess,'run',return_value=reply),self.assertRaises(RuntimeError):
            host.resources({})

    def test_archive_verifies_before_shutdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            out=Path(tmp);local=out/'remote';local.mkdir();(local/'x').write_bytes(b'abc')
            run=object.__new__(host.WindowRun)
            with patch.object(run,'ssh',return_value=host.digest(local/'x')+'  /guest/remote/x\n'),\
                 patch.object(host.JointRun,'collect'):
                run.collect(out,'/guest/remote')
            self.assertTrue(json.loads((out/'remote.归档核验.json').read_text())['核验通过'])
            (local/'x').write_bytes(b'changed')
            with patch.object(run,'ssh',return_value='0'*64+'  /guest/remote/x\n'),\
                 patch.object(host.JointRun,'collect'),self.assertRaises(RuntimeError):run.collect(out,'/guest/remote')


if __name__=='__main__':unittest.main()
