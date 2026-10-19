"""过去窗口迁移审核的合成测试，不把未来RR排除或冒充真实实验。"""
import copy,subprocess,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import test_validity_signal as fixtures
from validity_migration_window import check_window,clean_trend,index_markers
from analyze_validity_signal import SECOND

class MigrationWindowTest(unittest.TestCase):
    def row(self,batch,side,**changes):
        result={k:str(v) for k,v in dict(批次=batch,Zone=805306368,侧=side,
            启用=1,可信=1,实例=1,创建绝对纳秒=0,冻结绝对纳秒=batch*SECOND+(2000 if side=='后' else 0),
            轨迹原点绝对纳秒=0,序号=0,活动数=0,末次结束绝对纳秒=0).items()}
        result.update({k:str(v) for k,v in changes.items()});return result
    def fixture(self):
        z,p=fixtures.ValiditySignalTest().fixture({0:33,1:37})
        markers=index_markers([self.row(t,side) for t in range(41) for side in ('前','后')])
        return z,p,markers
    def test_clean_ten_and_thirty_second_window(self):
        z,p,m=self.fixture()
        for seconds in (10,30):
            result=clean_trend(z[:-1],z[-1],seconds,p,m)
            self.assertTrue(result['可用']);self.assertEqual(result['事前趋势']['分类'],'多次下降')
    def test_missing_old_markers_are_unknown_not_clean(self):
        z,p,m=self.fixture()
        result=clean_trend(z[:-1],z[-1],10,p,{})
        self.assertFalse(result['可用']);self.assertFalse(result['历史迁移']['可知'])
    def test_disabled_or_unreliable_unknown(self):
        for field in ('启用','可信'):
            a=self.row(0,'前');b=self.row(40,'后',**{field:0})
            self.assertFalse(check_window(a,b,0,40*SECOND)['可知'])
    def test_active_begin_or_end_contaminates(self):
        for side in ('前','后'):
            a=self.row(0,'前');b=self.row(40,'后')
            (a if side=='前' else b)['活动数']='1'
            result=check_window(a,b,0,40*SECOND)
            self.assertTrue(result['可知']);self.assertFalse(result['无迁移触及'])
    def test_copy_and_commit_completed_between_samples_are_not_missed(self):
        a=self.row(0,'前');b=self.row(40,'后',序号=2,末次结束绝对纳秒=35*SECOND)
        self.assertFalse(check_window(a,b,0,40*SECOND)['无迁移触及'])
    def test_old_touch_expires_not_whole_run_exclusion(self):
        a=self.row(30,'前',序号=2,末次结束绝对纳秒=20*SECOND)
        b=self.row(40,'后',序号=2,末次结束绝对纳秒=20*SECOND)
        self.assertTrue(check_window(a,b,30*SECOND,40*SECOND)['无迁移触及'])
    def test_completion_exactly_at_window_start_is_conservative(self):
        a=self.row(30,'前',序号=2,末次结束绝对纳秒=30*SECOND)
        b=self.row(40,'后',序号=2,末次结束绝对纳秒=30*SECOND)
        self.assertFalse(check_window(a,b,30*SECOND,40*SECOND)['无迁移触及'])
    def test_migration_only_in_extended_anchor_interval_is_still_rejected(self):
        z,p,m=self.fixture()
        # 实际锚点为29秒，名义过去十秒为30秒；不能漏掉29.5秒的完成。
        key=('40',z[-1]['Zone'],'后');m[key].update(序号='2',末次结束绝对纳秒=str(29*SECOND+SECOND//2))
        result=clean_trend(z[:-1],z[-1],10,p,m)
        self.assertFalse(result['可用']);self.assertTrue(result['历史迁移']['可知'])
    def test_future_rr_cannot_change_current_history(self):
        z,p,m=self.fixture();expected=clean_trend(z[:-1],z[-1],10,p,m)
        m['41',z[-1]['Zone'],'后']=self.row(41,'后',序号=2,末次结束绝对纳秒=41*SECOND)
        self.assertEqual(clean_trend(z[:-1],z[-1],10,p,m),expected)
    def test_another_zone_touch_does_not_contaminate(self):
        z,p,m=self.fixture()
        m['40','123','后']=self.row(40,'后',Zone=123,序号=2,活动数=1)
        self.assertTrue(clean_trend(z[:-1],z[-1],10,p,m)['可用'])
    def test_reset_still_unknown_even_with_clean_migration(self):
        for field in ('设备代际后','主机代际后','主机序号后','会话高位'):
            z,p,m=self.fixture();z[-1][field]='99'
            result=clean_trend(z[:-1],z[-1],10,p,m)
            self.assertFalse(result['可用']);self.assertFalse(result['事前趋势']['可知'])
    def test_changed_instance_origin_or_zone_unknown(self):
        for changes in (dict(实例=2),dict(轨迹原点绝对纳秒=5),dict(创建绝对纳秒=5),dict(Zone=123)):
            self.assertFalse(check_window(self.row(0,'前'),self.row(40,'后',**changes),0,40*SECOND)['可知'])
    def test_bad_fields_or_short_history_unknown(self):
        for changes in (dict(冻结绝对纳秒=-1),dict(活动数='错'),dict(创建绝对纳秒=5),dict(冻结绝对纳秒=39*SECOND),dict(末次结束绝对纳秒=41*SECOND)):
            self.assertFalse(check_window(self.row(0,'前'),self.row(40,'后',**changes),0,40*SECOND)['可知'])
        z,p,m=self.fixture();m['29',z[-1]['Zone'],'前']['轨迹原点绝对纳秒']='错'
        self.assertFalse(clean_trend(z[:-1],z[-1],10,p,m)['可用'])
    def test_sequence_decrease_unknown(self):
        self.assertFalse(check_window(self.row(0,'前',序号=10),self.row(40,'后',序号=2),0,40*SECOND)['可知'])
    def test_last_completion_time_cannot_decrease(self):
        self.assertFalse(check_window(self.row(30,'前',序号=2,末次结束绝对纳秒=20*SECOND),
            self.row(40,'后',序号=2,末次结束绝对纳秒=19*SECOND),30*SECOND,40*SECOND)['可知'])
    def test_ten_clean_but_thirty_contaminated(self):
        z,p,m=self.fixture()
        for (batch,zone,side),r in m.items():
            if int(batch)>=20:r.update(序号='2',末次结束绝对纳秒=str(20*SECOND))
        self.assertTrue(clean_trend(z[:-1],z[-1],10,p,m)['可用'])
        self.assertFalse(clean_trend(z[:-1],z[-1],30,p,m)['可用'])
    def test_duplicate_marker_is_not_silently_overwritten(self):
        a=self.row(0,'前')
        with self.assertRaises(ValueError):index_markers([a,copy.deepcopy(a)])
        a['侧']='未知'
        with self.assertRaises(ValueError):index_markers([a])

class MigrationWiringTest(unittest.TestCase):
    def test_patch_hunk_counts_are_valid(self):
        root=Path(__file__).resolve().parents[1]
        for path in (root/'zenfs-migration-window.patch',root/'tests'/'rocksdb_observe_migration.patch'):
            result=subprocess.run(['git','apply','--numstat',str(path)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
    def test_patch_only_touches_constructor_and_migration_not_selector(self):
        root=Path(__file__).resolve().parents[1]
        patch=(root/'zenfs-migration-window.patch').read_text()
        self.assertEqual(patch.count('diff --git '),2)
        forbidden=('EvaluateShortShadow','GC_START_LEVEL','ReadReclaimOneZone','SelectVictim','TakeMigrateZone(')
        added='\n'.join(line[1:] for line in patch.splitlines() if line.startswith('+') and not line.startswith('+++'))
        for text in forbidden:self.assertNotIn(text,added)
        self.assertIn('ZENFS_RRZC_MIGRATION_WINDOW',added)
        self.assertIn('GetMigrationWindowSnapshot',added)
    def test_observer_patch_preserves_request_plan_and_existing_csv(self):
        root=Path(__file__).resolve().parents[1]
        patch=(root/'tests'/'rocksdb_observe_migration.patch').read_text()
        self.assertIn('migration.csv',patch)
        changed='\n'.join(line for line in patch.splitlines() if line[:1] in ('+','-') and not line.startswith(('+++','---')))
        self.assertNotIn('put_batch(',changed)
        self.assertNotIn('RRZC_TEST_DB_PROFILE',changed)
        # 现有列不插入字段；只增加单独文件，避免改变已有归档分析器口径。
        removed=[line for line in patch.splitlines() if line.startswith('-') and not line.startswith('---')]
        self.assertEqual(len(removed),2)

if __name__=='__main__':unittest.main()
