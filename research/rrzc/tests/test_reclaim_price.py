"""独立重算空间替代估价与生产接线，旧V1和Greedy不变。"""
from fractions import Fraction
from pathlib import Path
import subprocess,tempfile,unittest,json
from unit_cost_source import direct,frontend
from matched_observation_source import direct as common
from analyze_all_blocks_dbbench import verify_unit_cost

ROOT=Path(__file__).resolve().parents[1]
PAIR=Path('/home/data/xwz/rrzc-baselines/joint-controlled-unit-cost-v1-20261028-results')
DONOR=Path('/home/data/xwz/rrzc-baselines/joint-controlled-fragments-repeat-v2-20261028-results/joint-controlled-fragments-repeat-v2-20261028')


class ReclaimPriceTest(unittest.TestCase):
    def test_cpp_properties(self):
        with tempfile.TemporaryDirectory() as name:
            binary=Path(name)/'test'
            subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-pthread','-I'+str(ROOT/'model'),
                str(ROOT/'tests/test_reclaim_price_selector.cc'),'-o',str(binary)],check=True)
            subprocess.run([str(binary)],check=True)

    def test_v1_hook_is_retained_and_v2_is_explicit(self):
        native=DONOR/'共同观测-原始选区.inc'
        if not native.exists():self.skipTest('实际源文件缺失')
        source=common(native.read_text());old=direct(source);new=direct(source,True)
        self.assertIn('rrzc_unit_cost::Select(inputs)',old)
        self.assertNotIn('RR空间替代成本',old)
        self.assertIn('rrzc_reclaim_price::Select(inputs,need,&space_evidence)',new)
        self.assertIn('if(greedy_execute)decision=cal_greedy;',new)
        self.assertEqual(source.count('row.safe=source && destination;'),new.count('row.safe=source && destination;'))
        self.assertIn('rrzc_reclaim_price_selector.h',frontend('正文',True))

    def test_independent_log_ledger_and_tampering(self):
        frame=dict(时序成本={1:dict(安全='1',有效='20',容量='128',RR复制='0',风险可知='1',RR空间='0'),
            2:dict(安全='1',有效='40',容量='128',RR复制='35',风险可知='1',RR空间='93')},
            单位成本={1:dict(有效='20',回收='108',RR抵扣='0',净复制='20'),
                2:dict(有效='40',回收='88',RR抵扣='33',净复制='7')},计划={},
            需求=dict(可知='1',分配等待='0',缺口字节='10'),单位建议=dict(存在='1',Zone='2'),
            决策=dict(实际存在='1',实际Zone='2'),空间定价={})
        for z,credit,space,debit in [(1,0,0,0),(2,35,93,2)]:
            frame['空间定价'][z]=dict(原RR抵扣=str(credit),预计RR空间=str(space),替代空间=str(min(space,10)),
                空间价格复制='20',空间价格回收='108',空间扣款=str(debit),有效RR抵扣=str(credit-debit),全额空间='0')
        verify_unit_cost(frame,'blocks')
        frame['空间定价'][2]['空间扣款']='0'
        with self.assertRaises(ValueError):verify_unit_cost(frame,'blocks')

    def test_cpp_matches_independent_historical_repricing(self):
        if not PAIR.exists():self.skipTest('没有历史帧')
        data=json.loads((PAIR/'受控配对分析/完整选区与指标.json').read_text())
        inputs=[];expected=[]
        for tag,arm in data.items():
            for decision in arm['逐轮选区']:
                if decision['阶段']!='业务':continue
                n=decision['轮次'];rows=[r for r in arm['候选明细'] if r['阶段']=='业务' and r['轮次']==n and r['安全可选']]
                need=decision['空间需求'];limit=int(need['缺口字节']);full=need['可知']!='1' or int(need['分配等待'])>0
                ref=min(rows,key=lambda r:(Fraction(int(r['有效字节']),int(r['回收字节'])),int(r['有效字节']),r['Zone']))
                price=Fraction(int(ref['有效字节']),int(ref['回收字节']));scored=[]
                inputs.append(f'{len(rows)} {int(need["可知"])} {limit} {need["分配等待"]}')
                # RR空间从该帧原始时序成本读取，不通过有效量反推未记录的概率。
                log=(PAIR/PAIR.name.removesuffix('-results')/tag/'run.zenfs.log').read_text()
                from analyze_all_blocks_dbbench import fields
                space={int(x['Zone'])//(128<<20):int(x['RR空间']) for line in log.splitlines()
                    if '时序成本：' in line and (x:=fields(line,'时序成本：'))['轮次']==str(n)}
                for r in rows:
                    v=int(r['有效字节']);credit=int(r['RR预计复制字节']) if r['风险可知']=='1' else 0
                    replaced=space[r['Zone']] if full else min(space[r['Zone']],limit)
                    raw=price*replaced;debit=min(credit,(raw.numerator+raw.denominator-1)//raw.denominator)
                    scored.append((Fraction(v-credit+debit,int(r['回收字节'])),v,r['Zone']))
                    inputs.append(f'{r["Zone"]} {v} {credit} {space[r["Zone"]]}')
                expected.append(min(scored)[2])
        with tempfile.TemporaryDirectory() as name:
            source=Path(name)/'reader.cc';binary=Path(name)/'test'
            source.write_text('#include "rrzc_reclaim_price_selector.h"\n#include <iostream>\n'
                'int main(){unsigned n;rrzc_space_plan::Need need;while(std::cin>>n>>need.known>>need.bytes>>need.waiters){'
                'std::vector<rrzc_deadline::Input> xs;for(unsigned i=0;i<n;++i){rrzc_deadline::Input x{};'
                'std::cin>>x.row.candidate.zone>>x.row.candidate.valid>>x.row.cost.credit>>x.rr_space;'
                'x.row.candidate.capacity=134217728;x.row.cost.reclaimed=134217728-x.row.candidate.valid;'
                'x.row.cost.risk_known=true;x.row.safe=true;xs.push_back(x);}'
                'std::cout<<rrzc_reclaim_price::Select(xs,need).zone<<"\\n";}}')
            subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-pthread','-I'+str(ROOT/'model'),str(source),'-o',str(binary)],check=True)
            reply=subprocess.check_output([str(binary)],input='\n'.join(inputs)+'\n',text=True)
            self.assertEqual([int(z) for z in reply.splitlines()],expected)


if __name__=='__main__':unittest.main()
