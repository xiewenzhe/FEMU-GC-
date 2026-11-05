"""真实短子进程验证监督脱离终端；不启动客机或数据库。"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


class DetachedSupervisorTest(unittest.TestCase):
    def test_parent_does_not_report_pid_when_child_setup_fails(self):
        script='''
import sys
from pathlib import Path
import run_calibration_host as m
def fail():raise OSError('受控测试：setsid失败')
m.os.setsid=fail
m.detach_supervisor(Path(sys.argv[1]))
'''
        with tempfile.TemporaryDirectory() as folder:
            env=os.environ.copy();tests=Path(__file__).resolve().parent
            env['PYTHONPATH']=str(tests)+':'+str(tests.parent)
            reply=subprocess.run([sys.executable,'-c',script,folder],capture_output=True,text=True,
                env=env,timeout=10)
            self.assertNotEqual(reply.returncode,0)
            self.assertNotIn('独立监督PID=',reply.stdout)
            self.assertIn('独立监督启动未确认',reply.stderr)

    def test_child_has_independent_session_and_closed_input(self):
        script='''
import json,os,sys
from pathlib import Path
from run_calibration_host import detach_supervisor
p=Path(sys.argv[1])
if detach_supervisor(p):
    (p/'核验.json').write_text(json.dumps(dict(PID=os.getpid(),SID=os.getsid(0),
        输入=os.readlink('/proc/self/fd/0'),日志权限=(p/'独立监督.log').stat().st_mode&0o777)))
'''
        with tempfile.TemporaryDirectory() as folder:
            env=os.environ.copy()
            tests=Path(__file__).resolve().parent
            env['PYTHONPATH']=str(tests)+':'+str(tests.parent)
            reply=subprocess.run([sys.executable,'-c',script,folder],capture_output=True,text=True,
                env=env,check=True,timeout=10)
            self.assertIn('独立监督PID=',reply.stdout)
            proof=Path(folder)/'核验.json';deadline=time.monotonic()+5
            while not proof.exists() and time.monotonic()<deadline:time.sleep(.01)
            row=json.loads(proof.read_text())
            self.assertEqual(row['PID'],row['SID'])
            self.assertEqual(row['输入'],'/dev/null')
            self.assertEqual(row['日志权限'],0o600)


if __name__=='__main__':unittest.main()
