"""收益预算日志的独立公式与防篡改测试；合成输入不是设备实验。"""
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from benefit_log import BenefitLog,evaluate
from analyze_v1_shadow_stream import MAX,SECOND,EXTRA
from analyze_short_shadow_stream import choose_short


class BenefitLogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory(prefix='rrzc-benefit-log-')
        cls.binary=Path(cls.temp.name)/'replay'
        subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=undefined',
            '-I'+str(ROOT/'model'),str(ROOT/'tests/net_cost_replay.cc'),'-o',str(cls.binary)],
            check=True,capture_output=True)

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def test_independent_python_matches_actual_cpp(self):
        rng=random.Random(20261020);payload=[];values=[];mib=1<<20
        for rid in range(1,151):
            candidates=[];blocks={};forecasts={};estimates={};rows=[]
            for z in range(1,7):
                valid=(8 if z==1 else rng.randrange(8,65))*mib
                rc=0 if z==1 else rng.randrange(3900,4120)
                usable=1 if z==1 else rng.randrange(5)!=0
                known=rng.randrange(5)!=0
                flags=rng.choice((1,3,7,7,7,7)) if z!=1 else 7
                d10=0 if z==1 else min(rc,rng.randrange(0,1000))
                d30=min(rc,3*d10)
                short=MAX if z==1 else rng.randrange(0,13)*SECOND
                if rc>=4096:short=0
                if not usable:short=None
                loss10=rng.randrange(0,8)*mib;loss30=3*loss10
                kept=max(0,valid-loss10) if known else 0
                candidates.append(dict(Zone=str(z),有效字节=str(valid),容量字节=str(256*mib),覆盖资格='1',空目标资格='1'))
                blocks[z]=dict(统计准入=str(int(usable)),当前RC=str(rc),窗口标志=str(flags),
                    十秒真实增量=str(d10),三十秒真实增量=str(d30))
                forecasts[z]=dict(可知=str(int(known)),十秒实际间隔纳秒=str(10*SECOND),三十秒实际间隔纳秒=str(30*SECOND),
                    十秒减少字节=str(loss10),三十秒减少字节=str(loss30),预计保留字节=str(kept))
                estimates[z]=short
                rows.append([z,valid,256*mib,1,1,int(usable),rc,d10,d30,flags,1,0 if z==1 else 96,SECOND,
                    short if short is not None else MAX,int(known),loss10,loss30,10*SECOND,30*SECOND,kept])
            values.append(evaluate(candidates,blocks,forecasts,estimates))
            payload.append(f'{rid} 6');payload.extend(' '.join(map(str,r)) for r in rows)
        run=subprocess.run([str(self.binary),'--benefit-budget'],input='\n'.join(payload)+'\n',
            text=True,capture_output=True,check=True)
        actual=[json.loads(s) for s in run.stdout.splitlines()]
        self.assertEqual(len(actual),len(values))
        for a,b in zip(actual,values):
            self.assertEqual(tuple(a[k] for k in ('GreedyZone','建议Zone','会改选','额外字节','预测纳秒','执行预算字节')),
                tuple(b[k] for k in ('greedy','selected','switch','extra','predicted','budget')))
            self.assertEqual({c['Zone']:{k:v for k,v in c.items() if k not in ('Zone','原因')} for c in a['成本']},b['costs'])

    def fixture(self):
        mib=1<<20
        candidates=[dict(Zone=str(z),有效字节=str(v*mib),容量字节=str(256*mib),覆盖资格='1',空目标资格='1')
            for z,v in ((1,16),(2,40))]
        blocks={z:dict(轮次='1',Zone=str(z),统计准入='1',当前RC=str(0 if z==1 else 4032),窗口标志='7',
            十秒真实增量=str(0 if z==1 else 4032),三十秒真实增量=str(0 if z==1 else 4032)) for z in (1,2)}
        estimates={1:MAX,2:2*SECOND}
        model=BenefitLog(True)
        model.observe('收益预算配置：观察=1，执行=1，默认关闭')
        def observe(marker,row):model.observe(marker+'，'.join(k+'='+str(v) for k,v in row.items()))
        for c in candidates:
            z=int(c['Zone']);observe('V1影子候选：',blocks[z])
            observe('留存收益候选：',dict(轮次=1,Zone=z,可知=1,有效字节=c['有效字节'],十秒减少字节=0,
                三十秒减少字节=0,十秒实际间隔纳秒=10*SECOND,三十秒实际间隔纳秒=30*SECOND,
                预计保留字节=c['有效字节'],额外迁移字节=(z-1)*24*mib,净收益字节=16*mib))
        observe('留存收益建议：',dict(轮次=1,执行=0,原建议Zone=1,新建议Zone=1,会改选=0,额外字节=0,预测纳秒=MAX))
        value=evaluate(candidates,blocks,{z:f for (r,z),f in model.retention.candidates.items()},estimates)
        for z,c in value['costs'].items():observe('收益预算候选：',dict(轮次=1,Zone=z,**c))
        observe('收益预算建议：',dict(轮次=1,执行=1,GreedyZone=1,建议Zone=value['selected'],会改选=int(value['switch']),
            额外字节=value['extra'],预测纳秒=value['predicted'],原预算字节=EXTRA,
            执行预算字节=value['budget'],合格集合额外上界=value['upper']))
        return model,candidates,estimates,choose_short(candidates,estimates)

    def test_accept_and_close(self):
        m,c,e,old=self.fixture();self.assertEqual(m.resolve('1',c,e,old)[1],2)
        self.assertEqual(m.budget_for('1'),24<<20);m.finish(1)

    def test_budget_cost_config_and_missing_rows_fail_closed(self):
        for kind in ('budget','cost','config','missing'):
            m,c,e,old=self.fixture()
            if kind=='budget':m.suggestions['1']['执行预算字节']=str(64<<20)
            if kind=='cost':m.rows['1',2]['折扣抵扣字节']='0'
            if kind=='config':m.config[0]['执行']='0'
            if kind=='missing':m.rows.clear()
            with self.subTest(kind=kind),self.assertRaises(ValueError):
                m.resolve('1',c,e,old);m.finish(1)

    def test_old_parser_never_accepts_dynamic_budget_implicitly(self):
        import test_guarded_analysis
        from guarded_rounds import parse
        lines=test_guarded_analysis.GuardedAnalysis().empty_round()
        lines[2]=lines[2].replace('预算字节='+str(EXTRA),'预算字节='+str(24<<20))
        with self.assertRaises(ValueError):parse(lines,'greedy')
        with self.assertRaises(ValueError):parse(lines,'greedy',budget_for=lambda rid:23<<20)
        self.assertEqual(len(parse(lines,'greedy',budget_for=lambda rid:24<<20)[0]),1)


if __name__=='__main__':unittest.main()
