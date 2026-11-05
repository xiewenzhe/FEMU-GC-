"""评分边界、历史帧独立重算和实验接线检查；离线结果不冒称真实收益。"""
from pathlib import Path
from fractions import Fraction
import json
import shlex
import subprocess
import tempfile
import unittest
from unit_cost_source import direct,wrapper
from matched_observation_source import direct as common
from greedy_admission_archive import archive_command,extract
from run_calibration_host import business_modes
from analyze_all_blocks_dbbench import verify_unit_cost

ROOT=Path(__file__).resolve().parents[1]
DONOR=Path('/home/data/xwz/rrzc-baselines/joint-controlled-fragments-repeat-v2-20261028-results')
PAIR=Path('/home/data/xwz/rrzc-baselines/joint-controlled-model-repaired-v4-20261028-results')


class UnitCostTest(unittest.TestCase):
    def test_cpp_properties_and_historical_frames(self):
        with tempfile.TemporaryDirectory() as name:
            binary=Path(name)/'test'
            subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-pthread',
                '-I'+str(ROOT/'model'),str(ROOT/'tests/test_unit_cost_selector.cc'),'-o',str(binary)],check=True)
            subprocess.run([str(binary)],check=True)
            if not PAIR.exists():return
            source=Path(name)/'reader.cc'
            source.write_text('#include "rrzc_unit_cost_selector.h"\n#include <iostream>\n'
                'int main(){unsigned n;while(std::cin>>n){std::vector<rrzc_deadline::Input> xs;'
                'for(unsigned i=0;i<n;++i){rrzc_deadline::Input x{};int safe,known;'
                'std::cin>>x.row.candidate.zone>>x.row.candidate.valid>>x.row.candidate.capacity'
                '>>x.row.cost.credit>>safe>>known;x.row.safe=safe;x.row.cost.risk_known=known;'
                'x.row.cost.reclaimed=x.row.candidate.capacity-x.row.candidate.valid;xs.push_back(x);}'
                'auto d=rrzc_unit_cost::Select(xs);std::cout<<d.found<<" "<<d.zone<<"\\n";}}')
            subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-pthread',
                '-I'+str(ROOT/'model'),str(source),'-o',str(binary)],check=True)
            data=json.loads((PAIR/'受控配对分析/完整选区与指标.json').read_text())
            frames={}
            for tag,arm in data.items():
                for r in arm['候选明细']:
                    if r['阶段']=='业务' and r['参与成本比较']:
                        frames.setdefault((tag,r['轮次']),[]).append(r)
            inputs=[];expected=[]
            for rows in frames.values():
                inputs.append(str(len(rows)))
                for r in rows:
                    inputs.append(' '.join(map(str,[r['Zone'],r['有效字节'],134217728,
                        r['RR预计复制字节'],int(r['安全可选']),int(r['风险可知'])])))
                safe=[r for r in rows if r['安全可选']]
                key=lambda r:(Fraction(int(r['有效字节'])-(int(r['RR预计复制字节']) if r['风险可知']=='1' else 0),
                    134217728-int(r['有效字节'])),int(r['有效字节']),r['Zone'])
                expected.append((1,min(safe,key=key)['Zone']) if safe else (0,0))
            reply=subprocess.check_output([str(binary)],input='\n'.join(inputs)+'\n',text=True)
            self.assertEqual([tuple(map(int,l.split())) for l in reply.splitlines()],expected)

    def test_isolated_hook_keeps_old_selection_and_safety(self):
        native=DONOR/'joint-controlled-fragments-repeat-v2-20261028/共同观测-原始选区.inc'
        if not native.exists():self.skipTest('没有实际部署源文件')
        old=common(native.read_text());new=direct(old)
        self.assertIn('unit_cost ? rrzc_deadline::Decision{}',new)
        self.assertEqual(old.count('row.safe=source && destination;'),new.count('row.safe=source && destination;'))
        self.assertEqual(old.count('rrzc_deadline::Select('),new.count('rrzc_deadline::Select('))
        self.assertIn('if(greedy_execute)decision=cal_greedy;',new)
        with self.assertRaises(ValueError):direct(new)

    def test_real_wrapper_references_and_baseline_stage_order(self):
        source=PAIR/'源码/run_calibrated_guest.sh'
        if not source.exists():self.skipTest('没有实际包装器')
        changed=wrapper(source.read_text())
        subprocess.run(['bash','-n'],input=changed,text=True,check=True)
        self.assertIn('initial_reference=$target/p1',changed)
        self.assertNotIn('"$target/g1" matched',changed)
        self.assertEqual(business_modes(True,True),(('p1','greedy'),('p2','greedy'),('g1','greedy'),('g2','blocks')))

    def test_real_archive_for_both_greedy_prechecks(self):
        for tag in ['p1','p2']:
            with self.subTest(tag=tag),tempfile.TemporaryDirectory() as name:
                root=Path(name);guest=root/'guest';(guest/tag).mkdir(parents=True)
                (guest/tag/'run.log').write_text('测试证据')
                (guest/tag/'aux').symlink_to('/root/不可读目录')
                _,command=archive_command('/home/femu/joint-controlled-test',tag)
                subprocess.run(command.replace('/home/femu/joint-controlled-test',shlex.quote(str(guest))),shell=True,check=True)
                dest=root/'out';dest.mkdir();extract(guest/(tag+'-admission.tar.gz'),dest,tag)
                self.assertEqual((dest/tag/'run.log').read_text(),'测试证据')

    def test_log_checker_rejects_fabricated_credit_or_selection(self):
        frame=dict(时序成本={1:dict(安全='1',有效='10',容量='128',RR复制='4',风险可知='1')},
            单位成本={1:dict(有效='10',回收='118',RR抵扣='4',净复制='6')},计划={},
            单位建议=dict(存在='1',Zone='1'),决策=dict(实际存在='1',实际Zone='1'))
        verify_unit_cost(frame,'blocks')
        frame['单位成本'][1]['净复制']='5'
        with self.assertRaises(ValueError):verify_unit_cost(frame,'blocks')


if __name__=='__main__':unittest.main()
