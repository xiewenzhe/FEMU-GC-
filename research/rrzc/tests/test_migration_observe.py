"""新归档审核及候选桥接的合成边界，不代表设备实验或策略收益。"""
import copy,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import test_validity_signal as fixtures
import test_validity_migration_window as marker_fixtures
from analyze_migration_observe import bridge,marker_audit,subsequent_empty
from analyze_rocksdb_observe import analyze
from validity_migration_window import index_markers
from analyze_validity_signal import SECOND

class MigrationObserveTest(unittest.TestCase):
    def fixture(self):
        z,p=fixtures.ValiditySignalTest().fixture({0:33,1:37})
        for r in z:r['写指针']=str(int(r['Zone'])+(256<<20))
        raw=[marker_fixtures.MigrationWindowTest().row(t,side) for t in range(41) for side in ('前','后')]
        markers=index_markers(raw);s=z[-1]
        b=dict(统计准入='1',设备会话='1/2',设备代际='0',有效字节=s['有效字节'])
        host=dict(主机代际='0/0/0',观察序号='0/0/0',重置窗稳定='1',快照写指针=s['写指针'])
        before=dict(markers['40',s['Zone'],'前'],冻结绝对纳秒=str(40*SECOND+SECOND//2))
        after=dict(before,侧='后',冻结绝对纳秒=str(40*SECOND+3*SECOND//4))
        files={(v['文件编号'],v['文件']):int(v['区内有效字节']) for v in p['40',s['Zone']]}
        return z,p,raw,markers,b,host,before,after,files
    def check_bridge(self,changes=None):
        z,p,raw,m,b,h,a,c,f=self.fixture()
        if changes:changes(z,p,m,b,h,a,c,f)
        return bridge(z[-1],b,h,a,c,m,p,f)
    def test_bridge_same_files_valid_epoch_and_no_migration(self):
        self.assertTrue(self.check_bridge()['可用'])
    def test_current_file_set_or_valid_change_is_not_same_snapshot(self):
        self.assertFalse(self.check_bridge(lambda z,p,m,b,h,a,c,f:b.update(有效字节='999'))['可用'])
        self.assertFalse(self.check_bridge(lambda z,p,m,b,h,a,c,f:f.clear())['可用'])
    def test_reset_session_or_unknown_stats_reject_bridge(self):
        for changes in (dict(设备代际='1'),dict(设备会话='2/3'),dict(统计准入='0')):
            self.assertFalse(self.check_bridge(lambda z,p,m,b,h,a,c,f:b.update(changes))['可用'])
        self.assertFalse(self.check_bridge(lambda z,p,m,b,h,a,c,f:h.update(主机代际='1/1/1'))['可用'])
    def test_old_or_future_snapshot_not_joined(self):
        self.assertFalse(self.check_bridge(lambda z,p,m,b,h,a,c,f:a.update(冻结绝对纳秒=str(44*SECOND)))['可用'])
        self.assertFalse(self.check_bridge(lambda z,p,m,b,h,a,c,f:a.update(冻结绝对纳秒=str(39*SECOND)))['可用'])
    def test_migration_between_snapshot_and_candidate_rejects(self):
        self.assertFalse(self.check_bridge(lambda z,p,m,b,h,a,c,f:c.update(序号='2',末次结束绝对纳秒=str(40*SECOND+SECOND//2)))['可用'])
    def test_markers_cover_queries_and_not_future_filter(self):
        z,p,raw,m,b,h,a,c,f=self.fixture()
        audit,_=marker_audit(z,raw,[])
        self.assertEqual(audit['原始标记行'],82)
        self.assertEqual(audit['有标记区数'],0)
    def test_missing_or_duplicate_marker_fails(self):
        z,p,raw,*_=self.fixture()
        for changed in (raw[:-1],raw+[copy.deepcopy(raw[0])]):
            with self.assertRaises(ValueError):marker_audit(z,changed,[])
    def test_copy_zones_without_any_mark_not_confirmed(self):
        z,p,raw,*_=self.fixture()
        with self.assertRaises(ValueError):marker_audit(z,raw,['V1后续迁移复制：源Zone=805306368，目标Zone=100，复制成功=1'])
    def test_snapshot_reset_does_not_clear_migration_counter(self):
        z,p,raw,*_=self.fixture()
        raw[3].update(序号='2',末次结束绝对纳秒=str(SECOND))
        with self.assertRaises(ValueError):marker_audit(z,raw,[])
    def test_zero_after_copy_is_not_natural_expiry(self):
        z,p,raw,m,b,h,a,c,f=self.fixture();current=z[30]
        future=dict(z[34],有效字节='0',RC='0',设备代际前='1',设备代际后='1',主机代际前='1',主机代际后='1',主机序号前='2',主机序号后='2')
        cm=m['30',current['Zone'],'后']
        self.assertTrue(subsequent_empty(current,[future],cm,m)['确认无迁移清空'])
        m['34',current['Zone'],'后'].update(序号='2',末次结束绝对纳秒=str(33*SECOND))
        self.assertFalse(subsequent_empty(current,[future],cm,m)['确认无迁移清空'])
    def test_explicit_analysis_flag_does_not_allow_mixed_family(self):
        with self.assertRaises(ValueError):analyze(Path('/不存在'),{},migration_observe=True,execution_pair='greedy')

if __name__=='__main__':unittest.main()
