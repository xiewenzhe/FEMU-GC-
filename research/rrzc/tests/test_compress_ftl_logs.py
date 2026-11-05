"""验证无损压缩和恢复凭据；不生成真实实验结果。"""
import gzip
import json
from pathlib import Path
import tempfile
import unittest
from compress_archived_ftl_logs import compress


class CompressTest(unittest.TestCase):
    def test_lossless_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'commands.bin';content=b'RRZCFT01'+b'\0\x10'*10000;path.write_bytes(content)
            dry=compress(path);self.assertTrue(path.exists());self.assertNotIn('解压SHA256',dry)
            row=compress(path,True);self.assertFalse(path.exists())
            self.assertEqual(gzip.open(row['压缩路径'],'rb').read(),content)
            self.assertEqual(json.loads(path.with_suffix('.bin.压缩核验.json').read_text())['原SHA256'],row['解压SHA256'])
            path.write_bytes(content)
            with self.assertRaises(ValueError):compress(path,True)


if __name__=='__main__':unittest.main()
