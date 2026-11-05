"""后续核验的同块、Reset及删失边界测试；合成样本不当作设备结果。"""
import sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from analyze_terminal_followup import analyze

def line(seconds,tag,**values):
    return f'2026/10/23-00:00:{seconds:02d}.000000 1 '+tag+'：'+'，'.join(f'{k}={v}' for k,v in values.items())

def block(seconds,round_id=1,**override):
    values=dict(轮次=round_id,Zone=128,设备会话='1/2',设备代际=3,块编号=4,PPA='0/0/0/4',
        代际起点纳秒=1,快照纳秒=(seconds+1)*10**9,当前RC=1000,统计准入=1,有效字节=20)
    values.update(override);return line(seconds,'V1影子候选',**values)

def sample(*future):
    return '\n'.join([block(0),line(0,'V1影子文件',轮次=1,Zone=128,文件编号=10,区内有效字节=20),
        line(0,'时序读预测',轮次=1,Zone=128,RR复制=10,窗口=10**10,ETA=10**9,近期增量=10,
             近期间隔=2*10**9,概率十亿分数=900000000),
        line(0,'时间一致证据',轮次=1,Zone=128,事件窗口纳秒=10**9,预计存活纳秒=10**10),
        line(0,'时序成本',轮次=1,Zone=128,安全=1),
        line(0,'时序方案决策',轮次=1,实际存在=1,实际Zone=256),*future])

class TerminalFollowupTest(unittest.TestCase):
    def test_same_block_true_increment_and_missed_window(self):
        r=analyze(sample(block(2,2,当前RC=1003)),'space')['正估计'][0]
        self.assertEqual(r['验证']['结论'],'同块跨预测窗口仍未达阈值')
        self.assertEqual(r['后续']['后续同块观察'][0]['相同块累计增量'],3)

    def test_maximum_identity_change_is_not_increment(self):
        r=analyze(sample(block(2,2,块编号=5,当前RC=1007)),'space')['正估计'][0]
        self.assertIn('身份',r['验证']['结论'])
        self.assertIsNone(r['后续']['后续同块观察'][0]['相同块累计增量'])

    def test_reset_stops_at_old_generation(self):
        reset=line(1,'Zone 重置诊断',Zone=128,状态='成功',主机代际='3/4')
        r=analyze(sample(reset,block(2,2,设备代际=4,当前RC=1)),'space')['正估计'][0]
        self.assertEqual(r['后续']['源首次Reset']['后代际'],4)
        self.assertEqual(r['后续']['后续同块观察'],[])
        self.assertIn('删失',r['验证']['结论'])
        with self.assertRaisesRegex(ValueError,'代际不连续'):
            analyze(sample(reset.replace('3/4','2/3')),'space')

    def test_retirement_is_competing_event(self):
        retired=line(1,'V1后续文件退役',文件编号=10,文件名='/10.sst')+'，最后链接已删除，元数据持久化成功'
        r=analyze(sample(retired,block(2,2,当前RC=1003,有效字节=0)),'space')['正估计'][0]
        self.assertIn('文件退役',r['验证']['结论'])
        self.assertEqual(r['后续']['候选文件退役'][0]['原区片段字节'],20)

    def test_copy_needs_commit_and_rr_service_is_not_threshold_time(self):
        rr='2026/10/23-00:00:02.000000 1 RR 开始：Zone 128，达阈值物理块 1，有效数据 20 字节'
        copy=line(3,'V1后续迁移复制',文件编号=10,源Zone=128,目标Zone=256,有效字节=20,复制成功=1)
        commit=line(3,'V1后续映射提交',文件编号=10,成功=1)
        reset=line(4,'Zone 重置诊断',Zone=128,状态='成功',主机代际='3/4')
        with self.assertRaisesRegex(ValueError,'映射提交'):
            analyze(sample(rr,copy,reset),'space')
        r=analyze(sample(rr,copy,commit,reset),'space')['正估计'][0]
        self.assertEqual(r['后续']['源Reset前RR开始'][0]['间隔秒'],2)
        self.assertEqual(r['后续']['源Reset前已提交复制'][0]['维护类型'],'RR')
        self.assertNotIn('实际达阈值上界秒',r['验证'])

    def test_real_five_predictions_and_original_log_unchanged(self):
        base=Path('/home/data/xwz/rrzc-baselines/joint-terminal-ycsb-v1-20261023-results')
        if not base.is_dir():self.skipTest('没有真实实验，不伪造通过')
        for mode,count in (('space',3),('model',2)):
            path=base/('short-'+mode)/('short-'+mode)/'zenfs.run.log';raw=path.read_bytes()
            r=analyze(raw.decode(),mode);self.assertEqual(r['正估计条数'],count)
            self.assertEqual(raw,path.read_bytes())
            if mode=='model':
                target=next(x for x in r['正估计'] if x['轮次']==3)
                self.assertEqual(target['后续']['初始RC'],916)
                self.assertEqual(target['后续']['后续同块观察'][0]['相同块累计增量'],41)
                self.assertEqual(target['后续']['源Reset前RR开始'],[])
                self.assertEqual(sum(x['有效字节'] for x in target['后续']['源Reset前已提交复制']),16592308)

if __name__=='__main__':unittest.main()
