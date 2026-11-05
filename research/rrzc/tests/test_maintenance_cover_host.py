"""独立接线、默认关闭、真实候选算术和归档边界检查；合成日志不是实验结果。"""
import io
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from maintenance_cover_source import selection,frontend
from colocation_choice_source import selection as previous_selection,frontend as previous_frontend
from run_maintenance_cover_host import wrapper,valid_tag,extract,freeze_reader
from analyze_maintenance_cover import decisions
from replay_maintenance_cover import BASE,snapshots,evaluate,compile_reader


class CoverHostTest(unittest.TestCase):
    def test_default_path_and_all_original_safety_are_retained(self):
        donor=BASE/'joint-controlled-fragments-repeat-v2-20261028-results/joint-controlled-fragments-repeat-v2-20261028/共同观测-原始选区.inc'
        if not donor.is_file():self.skipTest('冻结源缺失')
        old=previous_selection(donor.read_text());new=selection(old)
        self.assertIn('getenv("ZENFS_RRZC_MAINTENANCE_COVER") &&',new)
        self.assertIn('} else if(unit_cost)',new)
        self.assertIn('rrzc_unit_cost::Select(inputs)',new)
        self.assertEqual(old.count('row.safe=source && destination;'),new.count('row.safe=source && destination;'))
        self.assertIn('if(greedy_execute)decision=cal_greedy;',new)
        self.assertIn('if(row.safe && row.candidate.zone==chosen.zone)',new)
        with self.assertRaises(ValueError):selection(new)

    def test_wrapper_has_same_new_model_observation_and_serial_clients(self):
        text=wrapper();subprocess.run(['bash','-n'],input=text,text=True,check=True)
        self.assertIn('ZENFS_RRZC_MAINTENANCE_COVER=1',text)
        self.assertIn('RRZC_CAL_GREEDY_EXECUTE="$greedy"',text)
        self.assertIn('ZENFS_RRZC_CANDIDATE_POOL=original',text)
        self.assertIn('program=$target/colocation_credit',text)
        self.assertIn('program=$target/colocation_choice',text)
        self.assertIn('wait "$bench"',text)
        for tag in ('choice-k1-greedy-r1','retire-k1-model-r2','live-k1-model-r1'):valid_tag(tag)
        for tag in ('../bad','choice-k0-greedy-r1','choice-k1-model-r3'):
            with self.assertRaises(ValueError):valid_tag(tag)

    def test_frontend_and_builder_markers_are_explicit(self):
        text=frontend('#include "rrzc_unit_cost_selector.h"\n正文')
        self.assertIn('#include "rrzc_maintenance_cover.h"',text)
        builder=(Path(__file__).parent/'build_colocation_guest.py').read_text()
        self.assertIn("cover=(target/'启用同空间目标接线').is_file()",builder)
        self.assertIn("prefix=client if cover else 'cases'",builder)
        self.assertIn("('构建身份-'+client+'.json') if cover",builder)
        self.assertIn("K0与K1使用同一程序=client!='colocation_credit'",builder)

    def test_frozen_host_reader_records_actual_header_closure(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'源码').mkdir()
            binary,headers=freeze_reader(root)
            self.assertTrue(binary.is_file())
            self.assertTrue({'rrzc_maintenance_cover.h','rrzc_space_plan.h','rrzc_unit_cost_selector.h'}<=headers.keys())

    def test_archive_rejects_links_or_cross_arm_members(self):
        for path,kind in (('choice-k1-greedy-r1/link',tarfile.SYMTYPE),('other/log',tarfile.REGTYPE)):
            with tempfile.TemporaryDirectory() as temp:
                root=Path(temp);archive=root/'evidence.tar.gz'
                with tarfile.open(archive,'w:gz') as tar:
                    m=tarfile.TarInfo(path);m.type=kind;tar.addfile(m,io.BytesIO())
                with self.assertRaises(ValueError):extract(archive,root,'choice-k1-greedy-r1')

    def test_synthetic_logged_plans_recompute_and_tampering_fails(self):
        # 真实历史候选仅作为输入，重建的日志是解析测试夹具，不是新策略实际运行。
        tag='joint-controlled-colocation-credit-v2-20261029'
        source=BASE/(tag+'-results')/tag/'retire-k1-greedy-r1'
        if not source.exists():self.skipTest('冻结候选缺失')
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);binary=root/'reader';compile_reader(binary)
            r=evaluate(binary,[snapshots(source)[0]])[0];new=r['新建议'];d=r['需求'];a=r['原实际决策']
            a.update(模型存在=int(new['存在']),模型Zone=new['Zone'])
            def line(marker,values):return marker+'：'+ '，'.join(f'{k}={v}' for k,v in values.items())+'\n'
            text='受控放置业务开始：\n'
            for row in r['候选']:text+=line('时序成本',row)
            text+=line('时序方案需求',d)
            text+=line('空间覆盖需求',dict(轮次=r['轮次'],可知=int(new['目标可知']),退出目标=new['退出目标'],
                退出缺口=new['退出缺口'],分配等待=d['分配等待'],启动水平=d['目标百分比']))
            for p in new['计划']:
                row=dict(轮次=r['轮次'],**p);row['覆盖']=int(row['覆盖']);row['计划区']='/'.join(map(str,row['计划区']))
                text+=line('空间覆盖候选',row)
            text+=line('空间覆盖决策',dict(轮次=r['轮次'],存在=int(new['存在']),Zone=new['Zone'],输入非法=0,
                仅空间进展=int(new['仅空间进展']),实际只搬首区=1))
            text+=line('共同观测实际决策',a)+'受控放置业务截止：\n'
            log=root/'zenfs.log';log.write_text(text)
            self.assertEqual(len(decisions(root,binary)),1)
            log.write_text(text.replace('当前计划复制=2097152','当前计划复制=1'))
            with self.assertRaises(AssertionError):decisions(root,binary)


if __name__=='__main__':unittest.main()
