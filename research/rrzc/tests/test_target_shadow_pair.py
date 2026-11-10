"""用已归档设备事件检验关联方法；不生成业务收益或模拟新实验。"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from analyze_target_shadow_pair import link
from run_target_shadow_pair_host import BUILD_LABEL, PAIR_LABEL, pair, wrapper


class TargetPairTest(unittest.TestCase):
    def test_common_wrapper_only_enables_observation(self):
        source=pair.common.OLD/'源码/run_pool_business_guest.sh'
        original=pair.wrapper(source.read_text());new=wrapper(source.read_text())
        self.assertEqual(new.replace(' ZENFS_RRZC_TARGET_SHADOW=1',''),original)
        self.assertEqual(new.count('ZENFS_RRZC_TARGET_SHADOW=1'),1)
        pair.check_label(PAIR_LABEL)
        with tempfile.NamedTemporaryFile('w',suffix='.sh') as stream:
            stream.write(new);stream.flush()
            subprocess.run(['bash','-n',stream.name],check=True)

    def test_real_device_times_and_generation_link(self):
        build=pair.common.IMAGE_DIR/(BUILD_LABEL+'-results')
        proof=json.loads((build/'W历史设备补充核验.json').read_text())
        local=build/BUILD_LABEL
        attempts=[a for a in proof['组成核验']['协同尝试'] if not a['新文件'] and a['标签']==2]
        self.assertEqual(len(attempts),3)
        report=dict(组成=proof['组成核验'],旧G2影子=dict(逐次=[dict(尝试事件=attempts[1]['事件'],拒绝协同=True)]))
        with tempfile.TemporaryDirectory(prefix='rrzc-dual-pair-') as tmp:
            root=Path(tmp);(root/'组成.bin').symlink_to(local/'历史组成.bin')
            text=''.join((local/'历史准入功能'/name).read_text() for name in ('history.zenfs.log','recovered.zenfs.log'))
            (root/'run.zenfs.log').write_text(text)
            result=link(root,report)
            self.assertEqual(result['观察数'],4)
            self.assertEqual(result['历史拒绝数'],1)
            self.assertEqual(result['完整观察数'],0)
            gate=next(r for r in result['逐次'] if r['本次历史拒绝'])
            self.assertFalse(gate['实际入池'])
            self.assertTrue(gate['普通建议与实际同身份'])
            # 只修改临时日志的区间，必须拒绝把无关尝试关联成成功采证。
            (root/'run.zenfs.log').write_text(text.replace('路径=新写','路径=必要迁移',1))
            with self.assertRaises(ValueError):link(root,report)


if __name__=='__main__':unittest.main()
