"""设备累计数按真实采集窗口求增量；合成数据不代表实验结果。"""
import json
from pathlib import Path
import tempfile
import unittest
from watch_repaired_repeat import device_delta,COUNTERS


class RepairedRepeatTest(unittest.TestCase):
    def test_delta_excludes_earlier_arm_and_rejects_rollback(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name)
            before={k:1000 for k in COUNTERS};after={k:1050 for k in COUNTERS}
            after.update(启用=1,历史最高RC=1024)
            (root/'真实准备读取.json').write_text(json.dumps({'设备后':before}))
            (root/'strict.business.json').write_text(json.dumps(after))
            self.assertEqual(device_delta(root),{k:50 for k in COUNTERS})
            after['实际NAND读取']=999
            (root/'strict.business.json').write_text(json.dumps(after))
            with self.assertRaises(ValueError):device_delta(root)


if __name__=='__main__':unittest.main()
