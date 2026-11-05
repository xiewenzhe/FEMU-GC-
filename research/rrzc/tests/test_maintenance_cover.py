"""合成边界测试及独立算术校验；不是设备实验或实际收益。"""
import itertools
from pathlib import Path
import tempfile
import unittest
from replay_maintenance_cover import compile_reader,evaluate,digest,snapshots,BASE


def candidate(zone,valid,credit=0,capacity=128,known=1,safe=1):
    return dict(Zone=zone,有效=valid,容量=capacity,RR复制=credit,安全=safe,风险可知=known)


def record(rows,free=20,non_free=980,waiters=0,level=20):
    return dict(候选=rows,需求=dict(空闲=free,非空闲=non_free,分配等待=waiters,目标百分比=level))


class CoverTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory();cls.binary=Path(cls.temp.name)/'reader'
        compile_reader(cls.binary)

    @classmethod
    def tearDownClass(cls):cls.temp.cleanup()

    def run_records(self,*records):return [r['新建议'] for r in evaluate(self.binary,records)]

    def test_integer_exit_boundary(self):
        a,b=self.run_records(record([],free=200,non_free=800),record([],free=209,non_free=791))
        self.assertEqual((a['退出目标'],a['退出缺口']),(210,10))
        self.assertEqual(b['退出缺口'],1)

    def test_archive_digest_works_on_current_python(self):
        p=Path(self.temp.name)/'hash-input';p.write_bytes(b'abc')
        self.assertEqual(digest(p),'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')

    def test_one_zone_can_replace_cold_maintenance(self):
        d,=self.run_records(record([candidate(1,2,2),candidate(2,1)],free=200,non_free=800))
        self.assertEqual(d['Zone'],1)
        p={p['首区']:p for p in d['计划']}
        self.assertEqual(p[1]['当前计划复制'],2)
        self.assertEqual(p[1]['计划外当前有效量'],1)
        self.assertEqual(p[2]['评分总复制'],3)

    def test_both_needed_never_claims_rr_credit_twice(self):
        d,=self.run_records(record([candidate(1,32,31),candidate(2,24)],free=10,non_free=990))
        self.assertEqual(d['Zone'],2)
        self.assertTrue(all(p['覆盖'] and p['当前计划复制']==56 and
                            not p['计划外预计RR复制'] for p in d['计划']))

    def test_insufficient_pool_is_not_claimed_sufficient(self):
        d,=self.run_records(record([candidate(1,32,31),candidate(2,24)],free=1,non_free=1999))
        self.assertEqual(d['Zone'],2)
        self.assertTrue(all(not p['覆盖'] for p in d['计划']))

    def test_cold_unknown_and_unsafe(self):
        d,=self.run_records(record([candidate(1,40,40,known=0),candidate(2,5),candidate(3,1,1,safe=0)],
                                  free=200,non_free=800))
        self.assertEqual(d['Zone'],2)
        self.assertTrue(all(3 not in p['计划区'] for p in d['计划']))

    def test_waiters_do_not_borrow_rr_to_prove_allocation(self):
        d,=self.run_records(record([candidate(1,40,40),candidate(2,5)],waiters=1))
        self.assertTrue(d['仅空间进展'])
        self.assertEqual(d['Zone'],2)
        self.assertFalse(d['计划'][0]['覆盖'])

    def test_duplicate_identity_fails_closed_and_invalid_rows_are_excluded(self):
        a,b=self.run_records(record([candidate(1,2),candidate(1,3)]),
            record([candidate(1,2,3),candidate(2,128),candidate(3,1)]))
        self.assertTrue(a['输入非法']);self.assertFalse(a['存在'])
        self.assertEqual(b['Zone'],3)
        d,=self.run_records(record([candidate(1,2,safe=0),candidate(1,2)],free=200,non_free=800))
        self.assertTrue(d['存在']);self.assertEqual(d['Zone'],1)

    def test_real_archived_opportunities_have_different_space_obligations(self):
        sources=[('choice-v3','choice-k1-model-r1'),('credit-v2','retire-k1-greedy-r1'),
                 ('credit-v2','live-k1-greedy-r1')]
        records=[]
        for name,arm in sources:
            tag=f'joint-controlled-colocation-{name}-20261029'
            path=BASE/(tag+'-results')/tag/arm
            if not path.exists():self.skipTest('真实归档未在当前主机保留')
            records.append(snapshots(path)[0])
        high,retire,live=self.run_records(*records)
        self.assertEqual(high['Zone'],7*128*2**20)
        self.assertTrue(all(not p['覆盖'] and p['当前计划复制']==56*2**20 and
                            not p['计划外预计RR复制'] for p in high['计划']))
        for d in (retire,live):
            self.assertEqual(d['Zone'],5*128*2**20)
            p=next(p for p in d['计划'] if p['首区']==d['Zone'])
            self.assertTrue(p['覆盖']);self.assertEqual(p['当前计划复制'],2*2**20)
            self.assertEqual(p['计划外当前有效量'],3*2**19)
        # 两种未来在这里不能被区分；不把归档中后来实际发生的Delete喂给评分。
        self.assertEqual(retire,live)

    def test_permutation_stability(self):
        rows=[candidate(9,20,18),candidate(3,10),candidate(5,10)]
        values=self.run_records(*(record(list(p)) for p in itertools.permutations(rows)))
        self.assertTrue(all(d==values[0] for d in values))

    def test_two_candidate_decisions_match_exhaustive_current_space_sets(self):
        # 两候选时补充启发式应恰等于枚举全部满足目标的集合；独立用Python整数求解。
        records=[];expected=[]
        for a,b,credit,gap in itertools.product((1,25,70),(3,28,80),(0,1),(10,100,180)):
            rows=[candidate(1,a,a*credit),candidate(2,b)]
            records.append(record(rows,free=210-gap,non_free=790+gap))
            plans=[]
            sets=[s for n in (1,2) for s in itertools.combinations(rows,n)
                  if sum(128-x['有效'] for x in s)>=gap]
            if not sets:sets=[tuple(rows)]
            for selected in sets:
                total=sum(x['有效'] for x in selected)+sum(x['RR复制'] for x in rows if x not in selected)
                covered=sum(128-x['有效'] for x in selected)>=gap
                for first in selected:
                    plans.append(((not covered,total,first['有效'],-(128-first['有效']),first['Zone']),first['Zone']))
            expected.append(min(plans)[1])
        self.assertEqual([d['Zone'] for d in self.run_records(*records)],expected)

    def test_wide_space_and_cost_arithmetic(self):
        big=1<<62
        d,=self.run_records(record([candidate(1,big,capacity=big*2),candidate(2,big-1,capacity=big*2)],
                                  free=0,non_free=(1<<64)-1))
        self.assertEqual(d['退出目标'],(((1<<64)-1)*21+99)//100)
        self.assertEqual(d['Zone'],2)
        maximum=(1<<64)-1
        d,=self.run_records(record([candidate(1,maximum-1,capacity=maximum),
                                   candidate(2,maximum-1,capacity=maximum)],free=0,non_free=maximum))
        self.assertTrue(all(p['当前计划复制']==2*(maximum-1) for p in d['计划']))
        self.assertTrue(all(not p['覆盖'] for p in d['计划']))


if __name__=='__main__':unittest.main()
