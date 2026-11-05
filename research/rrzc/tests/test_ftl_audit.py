"""FTL采证的真实C编译与合成首差测试；合成数据不代表实验结果。"""
from pathlib import Path
import os
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from analyze_ftl_trace import analyze
from build_ftl_audit import instrument,without_pending_trace,ROOT
import run_calibration_host as runner


class FtlAuditTest(unittest.TestCase):
    def test_isolated_launch_keeps_rom_path_and_restores_environment(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);binary=root/'qemu-system-x86_64';binary.write_text('诊断程序占位')
            run=runner.CalibrationRun.__new__(runner.CalibrationRun)
            run.ftl_audit_binary=str(binary);run.ftl_audit_sha256=runner.digest(binary)
            run.output=root;run.record=lambda **values:None
            def capture(argv,timeout):return argv,os.environ.get('FEMU_FTL_AUDIT')
            before=os.environ.get('FEMU_FTL_AUDIT')
            with patch.object(runner.ReuseRun,'command',side_effect=capture):
                argv,path=run.command(['./build-femu/qemu-system-x86_64','-daemonize'])
            self.assertEqual(argv[0],str(binary))
            self.assertEqual(argv[-2:],['-L',str(ROOT/'pc-bios')])
            self.assertEqual(path,str(root/'FEMU设备命令.bin'))
            self.assertEqual(os.environ.get('FEMU_FTL_AUDIT'),before)

    def test_source_preserves_original_body(self):
        source=without_pending_trace((ROOT/'hw/femu/zns/zftl.c').read_text())
        changed=instrument(source)
        self.assertEqual(changed.count('femu_rr_read_action('),source.count('femu_rr_read_action('))
        self.assertEqual(changed.count('zns_wc_flush('),source.count('zns_wc_flush('))
        self.assertIn('latency = rrzc_original_process_req(ns, req);',changed)
        self.assertNotIn('FEMU_RR_EVENT_TRACE',source)

    def test_native_header_records_real_read_before_count(self):
        with tempfile.TemporaryDirectory() as name:
            root=Path(name);trace=root/'trace';client=root/'test.c';binary=root/'test'
            client.write_text('''#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <stdbool.h>
#define NVME_CMD_WRITE 1
#define le32_to_cpu(x) (x)
#define cpu_to_le64(x) (x)
struct wc {uint64_t sblk,used;uint64_t *lpns;};
struct zns_ssd {struct {int num_wc;struct wc *write_cache;} cache;};
typedef struct {struct {unsigned opcode;uint32_t cdw13;} cmd;uint64_t slba;
uint32_t nlb,nr_zone_resets,*zone_resets,status;struct {struct {uint32_t result;} n;} cqe;} NvmeRequest;
#include "rrzc_ftl_trace.h"
int main(void) {struct zns_ssd z={0};NvmeRequest q={.cmd={.opcode=194,.cdw13=0x52520000},.slba=80,.nlb=8};
rrzc_ftl_begin(&z,&q);rrzc_ftl_read(10,123,1023);q.cqe.n.result=1;
rrzc_ftl_end(&z,&q);rrzc_ftl_close();return 0;}
''')
            subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror',
                '-Wno-unused-function','-I'+str(Path(__file__).parent),str(client),'-o',str(binary)],check=True)
            subprocess.run([str(binary)],env=dict(os.environ,FEMU_FTL_AUDIT=str(trace)),check=True)
            self.assertEqual(trace.read_bytes()[:8],b'RRZCFT01')
            self.assertEqual(struct.unpack('<8Q',trace.read_bytes()[8:]),(194,80,8,0x52520000,123,1023,0,1))
            failed=subprocess.run([str(binary)],env=dict(os.environ,FEMU_FTL_AUDIT=str(trace)),
                                  stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,cwd=root,
                                  preexec_fn=lambda:__import__('resource').setrlimit(__import__('resource').RLIMIT_CORE,(0,0)))
            self.assertNotEqual(failed.returncode,0)

    def test_first_different_device_write_is_input_difference(self):
        reset=(256,6,33,0,0,0,0,0)
        same=(194,80,8,0,123,32,0,0)
        write=(1,512,128,0,12,13,0,0)
        other=(1,512,64,0,12,13,0,0)
        with tempfile.TemporaryDirectory() as name:
            path=Path(name)/'trace'
            path.write_bytes(b'RRZCFT01'+b''.join(struct.pack('<8Q',*r) for r in
                (same,reset,same,write,reset,same,other)))
            proof=analyze(path)
            self.assertEqual(proof['首差']['相对命令序号'],[1,1])
            self.assertEqual(proof['首差']['分类'],'设备收到的输入命令或交错不同')
            path.write_bytes(b'RRZCFT01x')
            with self.assertRaises(ValueError):analyze(path)

    def test_only_split_write_does_not_claim_different_page_order(self):
        reset=(256,6,33,0,0,0,0,0)
        read=(194,80,8,0x52520000,123,32,0,0)
        with tempfile.TemporaryDirectory() as name:
            path=Path(name)/'trace'
            values=(reset,(1,512,128,0,12,13,0,0),read,reset,
                    (1,512,64,0,12,13,0,0),(1,576,64,0,14,15,0,0),read)
            path.write_bytes(b'RRZCFT01'+b''.join(struct.pack('<8Q',*r) for r in values))
            proof=analyze(path)
            self.assertIsNotNone(proof['首差'])
            self.assertIsNone(proof['按4KiB页展开的输入首差'])
            self.assertIsNone(proof['读命令及PPA_RC首差'])
            self.assertIsNone(proof['合并相邻连续写后的输入首差'])


if __name__=='__main__':unittest.main()
