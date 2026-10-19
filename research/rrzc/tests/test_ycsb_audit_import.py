"""真实编译校验头，验证继承边界；合成文件不是设备实验结果。"""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

HERE=Path(__file__).resolve().parent
UPSTREAM=HERE.parents[2]/'build-femu/rrzc/ycsb-upstream-20261017'


class ImportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not (UPSTREAM/'core/db.h').exists():
            raise unittest.SkipTest('没有固定版本YCSB头文件，不能伪造编译通过')
        cls.build=tempfile.TemporaryDirectory(prefix='rrzc-audit-build-')
        cls.binary=Path(cls.build.name)/'audit-test'
        subprocess.run(['g++','-std=c++17','-Wall','-Wextra','-Werror',
            '-I'+str(UPSTREAM),str(HERE/'test_ycsb_audit_import.cc'),'-o',str(cls.binary)],check=True)

    @classmethod
    def tearDownClass(cls):cls.build.cleanup()

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='rrzc-audit-case-')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.source=self.root/'source.expected'
        raw=(6).to_bytes(4,'little')+b'field0'+(6).to_bytes(4,'little')+'内容'.encode()
        value=14695981039346656037
        for b in raw:value=((value^b)*1099511628211)&((1<<64)-1)
        self.valid=f'user1 {value}\nuser2 0\n'
        self.source.write_text(self.valid)

    def run_case(self,mode='检查',*,text=None,keys='2',inherit=True,extra=None):
        if text is not None:self.source.write_text(text)
        env={k:v for k,v in os.environ.items() if not k.startswith('YCSB_AUDIT_')}
        env['YCSB_AUDIT_PREFIX']=str(self.root/'out')
        if inherit:env.update(YCSB_AUDIT_IMPORT=str(self.source),YCSB_AUDIT_KEYS=keys)
        if extra:
            for key,value in extra.items():
                if value is None:env.pop(key,None)
                else:env[key]=value
        return subprocess.run([str(self.binary),mode],env=env,text=True,capture_output=True)

    def test_disabled_keeps_empty_state(self):
        result=self.run_case(inherit=False)
        self.assertEqual((result.returncode,result.stdout),(0,'0 0\n'))

    def test_import_and_read_and_save(self):
        self.assertEqual(self.run_case().stdout,'2 2\n')
        self.assertEqual(self.run_case('读取').returncode,0)
        self.assertEqual((self.root/'out.expected').read_text(),self.valid)
        self.assertEqual((self.root/'out.counts').read_text(),
            '装载=0\n读取=1\n更新=0\n键数=2\n继承键数=2\n')

    def test_wrong_read_unknown_update_and_insert_rejected(self):
        for mode in ('错误读取','未知更新','插入'):
            with self.subTest(mode=mode):self.assertNotEqual(self.run_case(mode).returncode,0)
        self.assertEqual(self.run_case('更新').returncode,0)

    def test_corrupt_lines_rejected_before_trace_creation(self):
        for text in ('','user1 0\n','user1 0\nuser1 1\n','user2 0\nuser1 0\n',
            'user1 -1\nuser2 0\n','user1 18446744073709551616\nuser2 0\n',
            'user1 0 x\nuser2 0\n','user1 0\n\n','user1 0\r\nuser2 0\n',
            'user1 +1\nuser2 0\n','user1 0x10\nuser2 0\n'):
            with self.subTest(text=text):
                self.assertNotEqual(self.run_case(text=text).returncode,0)
                self.assertFalse((self.root/'out.requests').exists())

    def test_configuration_errors(self):
        for extra in ({'YCSB_AUDIT_PREFIX':None},{'YCSB_AUDIT_IMPORT':None},
            {'YCSB_AUDIT_KEYS':None},{'YCSB_AUDIT_IMPORT':''},
            {'YCSB_AUDIT_IMPORT':str(self.root/'out.expected')}):
            with self.subTest(extra=extra):self.assertNotEqual(self.run_case(extra=extra).returncode,0)
        for keys in ('0','1','3','-1','2x'):
            with self.subTest(keys=keys):self.assertNotEqual(self.run_case(keys=keys).returncode,0)


if __name__=='__main__':unittest.main()
