"""共区不等于入池；测试分片迁移、Reset及观察删失，夹具不作为实验结果。"""
import unittest
from analyze_flush_cohorts import Replay


class CohortTest(unittest.TestCase):
    def test_w_only_not_counted_until_hot_arrives(self):
        r=Replay({'h'},{'w'},{'h':{(7,0)}})
        r.birth('w',{(8,0)},1);self.assertEqual(r.first('w'),[])
        r.rr(7,1,2,[{'文件名':'h'}])
        r.move({'文件名':'h','源Zone':'7','目标Zone':'8'},3)
        self.assertEqual(r.first('w')[0]['首次原因'],'H迁入已有W区')
        self.assertIsNone(r.first('w')[0]['结局'])

    def test_retired_before_first_rr_remains_pending_until_rr(self):
        r=Replay({'h'},{'w'},{'h':{(7,0)}})
        r.birth('w',{(7,0)},1);r.retire('w')
        self.assertIsNone(r.first('w')[0]['结局'])
        r.rr(7,1,4,[{'文件名':'h'}])
        self.assertEqual(r.first('w')[0]['结局'],'RR前逻辑退役')

    def test_multiple_fragments_keep_old_zone_until_last_commit(self):
        r=Replay({'h'},{'w'},{'h':{(7,0)}})
        r.birth('w',{(7,0)},1)
        r.rr(7,1,2,[{'文件名':'h'},{'文件名':'w'},{'文件名':'w'}])
        q={'文件名':'w','源Zone':'7','目标Zone':'8'}
        r.move(q,3);self.assertIn((7,0),r.where['w'])
        r.move(q,4);self.assertNotIn((7,0),r.where['w'])
        self.assertEqual(r.first('w')[0]['结局'],'RR到来仍存活')

    def test_reset_cannot_cross_live_mapping_or_reuse_old_cohort(self):
        r=Replay({'h'},{'w'},{'h':{(7,0)}});r.birth('w',{(7,0)},1)
        with self.assertRaises(ValueError):r.reset(7)
        r.retire('w');r.rr(7,1,2,[{'文件名':'h'}]);r.move({'文件名':'h','源Zone':'7','目标Zone':'8'},3)
        r.reset(7);r.rr(7,2,4,[])
        self.assertEqual(r.first('w')[0]['首次RR'],1)

    def test_rr_population_must_match_actual_copies(self):
        r=Replay({'h'},{'w'},{'h':{(7,0)}});r.birth('w',{(7,0)},1)
        with self.assertRaises(ValueError):r.rr(7,1,2,[{'文件名':'h'}])


if __name__=='__main__':unittest.main()
