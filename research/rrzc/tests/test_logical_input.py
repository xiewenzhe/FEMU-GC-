"""固定输入回放的拒绝条件、命令与状态检查；合成数据不作为实验结果。"""
import ctypes
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import gzip
import hashlib
import logical_input_guest as m
from calibration_source import wrapper


class LogicalInputTest(unittest.TestCase):
    def test_io_is_single_aligned_nvme_command(self):
        def ioctl(fd,request,raw,mutate):
            self.assertEqual(fd,9);self.assertEqual(request,0xc0484e43)
            command=m.Command.from_buffer_copy(raw)
            self.assertEqual((command.opcode,command.nsid,command.cdw10,command.cdw12),(1,1,512,127))
            self.assertEqual(ctypes.string_at(command.addr,command.data_len),b'x'*65536)
            return 0
        with patch.object(m.fcntl,'ioctl',side_effect=ioctl):m.io(9,512,65536,b'x'*65536)
        with self.assertRaises(AssertionError):m.io(9,512,513)

    def test_wrapper_has_no_reload_and_preserves_read_write_requests(self):
        base=wrapper(Path(__file__).with_name('run_all_blocks_dbbench_guest.sh').read_text())
        base=base.replace('python3 "$target/calibration_guest.py" blocks',
            'python3 "$target/calibration_guest.py" warmup "$result"\npython3 "$target/calibration_guest.py" blocks')
        source,replay=m.wrappers(base)
        for text in (source,replay):subprocess.run(['bash','-n'],input=text,text=True,check=True)
        self.assertNotIn('rrzccalload',replay)
        self.assertNotIn('zenfs mkfs',replay)
        self.assertIn('--reads=786432 --writes=589824',replay)
        self.assertIn('shutil.copyfile(p',replay)
        self.assertNotIn('hotspot=1;phase run',source)

    def test_replay_uses_full_zones_first_and_closes_partial_zone(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);dest=root/'逻辑输入';dest.mkdir()
            source=root/'source';source.mkdir();(source/'aux').mkdir();(dest/'aux').mkdir()
            result=root/'g1';result.mkdir()
            zones=[dict(slba=0,cap=1,wp=1,state='CLOSED'),dict(slba=1,cap=1,wp=2,state='FULL')]
            empty=[dict(z,wp=z['slba'],state='EMPTY') for z in zones]
            rows=[]
            for i in range(2):
                data=bytes([i])*512;p=dest/(str(i)+'.bin.gz')
                with gzip.open(p,'wb') as f:f.write(data)
                rows.append(dict(Zone=i,字节=512,SHA256=hashlib.sha256(data).hexdigest(),压缩SHA256=hashlib.sha256(p.read_bytes()).hexdigest()))
            for name in ('装载-0.sst.csv','装载-0.extent.csv'):(dest/name).write_text('合成输入')
            m.dump(dest/'清单.json',dict(Zone=zones,逐区=rows,辅助路径=str(source/'aux'),边界='合成数据'))
            calls=[]
            with patch.object(m,'guard',side_effect=[zones,empty]),patch.object(m,'report',return_value=zones),\
                 patch.object(m.os,'open',return_value=9),patch.object(m.os,'close'),\
                 patch.object(m,'io',side_effect=lambda fd,lba,size,payload:calls.append(lba)),\
                 patch.object(m.subprocess,'run') as command:
                m.replay(root,result)
            self.assertEqual(calls,[1,0])
            self.assertEqual(command.call_args_list[0].args[0],['nvme','zns','reset-zone',m.DEVICE,'-a'])
            self.assertIn('close-zone',command.call_args_list[1].args[0])
            self.assertTrue(json.loads((result/'逻辑输入回放.json').read_text())['通过'])
            self.assertTrue((result/'回放前辅助文件').is_dir())


if __name__=='__main__':unittest.main()
