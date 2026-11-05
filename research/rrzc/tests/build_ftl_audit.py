#!/usr/bin/env python3
"""复用原链接对象，验证原样重链接和无日志代码段，再隔离构建FTL诊断版本。"""
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def without_pending_trace(source):
    source = source.replace('#include "qemu/log.h"\n', '')
    a = source.index('/* 仅诊断开关启用时记录稀疏事件')
    b = source.index('static inline struct ppa get_maptbl_ent', a)
    source = source[:a] + '\n' + source[b:]
    a = source.index('    if (zns_rr_event_trace_enabled()) {')
    b = source.index('    return maxlat;', a)
    source = source[:a] + source[b:]
    a = source.index('        if (action == FEMU_RR_TRIGGER) {\n            uint64_t after')
    b = source.index('        ftl_debug', a)
    return source[:a] + source[b:]


def instrument(source):
    from calibration_source import once
    source = once(source, '#include "zns.h"', '#include "zns.h"\n#include "rrzc_ftl_trace.h"')
    source = once(source, '    zns->maptbl[lpn] = *ppa;',
                  '    zns->maptbl[lpn] = *ppa;\n    rrzc_ftl_map(lpn, ppa->ppa);')
    source = once(source, '        struct nand_cmd srd;',
                  '        rrzc_ftl_read(lpn, ppa.ppa, get_blk(zns, &ppa)->read_count);\n'
                  '        struct nand_cmd srd;')
    source = once(source, 'uint64_t zns_ftl_process_req(NvmeNamespace *ns, NvmeRequest *req)',
                  'static uint64_t rrzc_original_process_req(NvmeNamespace *ns, NvmeRequest *req)')
    # 原FTL函数可能提前返回；包装完整调用以保证Reset路径同样记录。
    source += '''
uint64_t zns_ftl_process_req(NvmeNamespace *ns, NvmeRequest *req)
{
    uint64_t latency;
    struct zns_ssd *zns = ns->zns;

    rrzc_ftl_begin(zns, req);
    latency = rrzc_original_process_req(ns, req);
    rrzc_ftl_end(zns, req);
    return latency;
}
'''
    return source


def link_args(build):
    lines = (build/'build.ninja').read_text().splitlines()
    i = next(i for i, line in enumerate(lines) if line.startswith('build qemu-system-x86_64:'))
    edge = lines[i].split(': c_LINKER_RSP ', 1)[1].split(' | ', 1)[0]
    inputs = shlex.split(edge)
    assert lines[i+1].startswith(' LINK_ARGS = '), '链接规则改变，停止'
    return inputs, shlex.split(lines[i+1].split(' = ', 1)[1])


def build(output, fix_fragments=False):
    output = output.resolve()
    output.mkdir(exist_ok=False, parents=True)
    native = ROOT/'build-femu'
    binary = native/'qemu-system-x86_64'
    obj = native/'libsystem.a.p/hw_femu_zns_zftl.c.o'
    original = dict(二进制=digest(binary), FTL对象=digest(obj))
    inputs, flags = link_args(native)

    def link(path, objects):
        with (output/(path.parent.name+'-链接.log')).open('w') as log:
            subprocess.run(['cc', '-m64', '-o', str(path), *objects, *flags],
                           cwd=native, stdout=log, stderr=subprocess.STDOUT, check=True)

    relink = output/'原样重链接';relink.mkdir()
    link(relink/'qemu-system-x86_64', inputs)
    assert digest(relink/'qemu-system-x86_64') == original['二进制'], '原样重链接不符，拒绝引入新设备身份'
    source = without_pending_trace((ROOT/'hw/femu/zns/zftl.c').read_text())
    # 原对象身份检查使用修复前正文；实际修复作为单独开关记录，不能暗中混用。
    repaired = source
    if 'start_lpn = DIV_ROUND_UP(lba, secs_per_pg);' in source:
        a=source.index('    int wcidx;\n',source.index('static uint64_t zns_write('))
        b=source.index('    if(wcidx==-1)',a)
        source=source[:a]+'    int wcidx = zns_get_wcidx(zns, req_zone);\n\n'+source[b:]
    if fix_fragments:
        assert repaired!=source, '没有找到已审阅的写片段修复'
    cc = next(row for row in json.loads((native/'compile_commands.json').read_text())
              if row['file'].endswith('/zftl.c'))

    def compile_one(name, text):
        path = output/(name+'.c');path.write_text(text)
        argv = shlex.split(cc['command'])
        argv[argv.index('-o')+1] = str(output/(name+'.o'))
        argv[argv.index('-MF')+1] = str(output/(name+'.d'))
        argv[-1] = str(path)
        argv += ['-I'+str(ROOT/'hw/femu/zns'), '-I'+str(Path(__file__).parent)]
        with (output/(name+'-编译.log')).open('w') as log:
            subprocess.run(argv, cwd=native, stdout=log, stderr=subprocess.STDOUT, check=True)
        return output/(name+'.o')

    rebuilt = compile_one('无新日志', source)
    for name, path in [('原始', obj), ('重编', rebuilt)]:
        subprocess.run(['objcopy', '--dump-section', '.text='+str(output/(name+'.text')), str(path)], check=True)
    assert (output/'原始.text').read_bytes() == (output/'重编.text').read_bytes(), '原始FTL机器代码不匹配，停止'
    # 同时保留带重定位的汇编，不能仅凭.text相同声称全部链接身份相同。
    for name, path in [('原始', obj), ('重编', rebuilt)]:
        (output/(name+'-重定位汇编.txt')).write_text(subprocess.check_output(['objdump', '-dr', str(path)], text=True))
    traced = compile_one('设备采证', instrument(repaired if fix_fragments else source))
    assert inputs.count(str(obj.relative_to(native))) == 1
    diagnostic = output/'诊断程序';diagnostic.mkdir()
    link(diagnostic/'qemu-system-x86_64', [str(traced) if p == str(obj.relative_to(native)) else p for p in inputs])
    assert original == dict(二进制=digest(binary), FTL对象=digest(obj)), '原有构建被改变'
    proof = dict(原有身份=original, 原样重链接完全相同=True, 无日志FTL代码段相同=True,
                 诊断二进制SHA256=digest(diagnostic/'qemu-system-x86_64'),
                 原源码SHA256=digest(ROOT/'hw/femu/zns/zftl.c'),
                 诊断源码SHA256=digest(output/'设备采证.c'),
                 诊断头SHA256=digest(Path(__file__).with_name('rrzc_ftl_trace.h')),
                 顺序写片段去重=fix_fragments,
                 边界='仅替换一个FTL诊断对象；原库和二进制不覆盖，不改变RR/GC规则；新身份须独立重复，不能混作旧性能')
    (output/'构建核验.json').write_text(json.dumps(proof, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(proof, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    build(Path(sys.argv[1]), '--fix-write-fragments' in sys.argv[2:])
