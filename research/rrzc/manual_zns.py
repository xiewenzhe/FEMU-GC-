"""手动ZNS入口：统一参数源、资源门禁、精确PID记录及正常关机。"""
import argparse
import json
import os
from pathlib import Path
import resource
import signal
import socket
import subprocess
import sys
import tempfile
import time

from finerr_profile import DEFAULT, load, settings

PROJECT = Path(__file__).resolve().parents[2]


def command(profile, image, run_dir):
    return [str(PROJECT/'build-femu/qemu-system-x86_64'), '-name', 'FEMU-ZNSSD-VM',
        '-enable-kvm', '-cpu', 'host', '-smp', '4', '-m',
        str(profile['沿用条件']['客机内存GiB'])+'G',
        '-device', 'virtio-scsi-pci,id=scsi0', '-device', 'scsi-hd,drive=hd0',
        '-drive', f'file={image},if=none,aio=native,cache=none,format=qcow2,id=hd0',
        '-device', settings(profile)['设备参数'],
        '-net', 'user,hostfwd=tcp:127.0.0.1:8080-:22', '-net', 'nic,model=virtio',
        '-nographic', '-qmp', f'unix:{run_dir}/qmp.sock,server,nowait',
        '-pidfile', str(run_dir/'qemu.pid')]


def check_memory(profile, available, memlock):
    nand = profile['命名空间']['容量字节']
    guest = profile['沿用条件']['客机内存GiB'] << 30
    if available < nand+guest+(16 << 30):
        raise ValueError('可用内存不足：需NAND后端、客机及16GiB余量；不降低保护门槛')
    if memlock != resource.RLIM_INFINITY and memlock < nand:
        raise ValueError('锁页软额度不足32GiB；需另行授权提高进程额度，不自动修改系统或改用未锁页性能实验')


def preflight(profile, image):
    if not image.is_file() or ',' in str(image):
        raise ValueError('镜像不存在或路径含QEMU参数分隔符')
    if not (PROJECT/'build-femu/qemu-system-x86_64').is_file():
        raise ValueError('未找到编译后的FEMU')
    if not os.access('/dev/kvm', os.R_OK | os.W_OK):
        raise ValueError('当前进程无KVM访问权限')
    memory = dict(line.split(':', 1) for line in Path('/proc/meminfo').read_text().splitlines())
    check_memory(profile, int(memory['MemAvailable'].split()[0])*1024,
                 resource.getrlimit(resource.RLIMIT_MEMLOCK)[0])
    listing = subprocess.run(['ps', '-C', 'qemu-system-x86_64', '-o', 'args='],
                             text=True, capture_output=True, check=False).stdout
    if f'file={image},' in listing:
        raise ValueError('该镜像已有QEMU使用，拒绝重复启动')
    with socket.socket() as probe:
        probe.bind(('127.0.0.1', 8080))


def powerdown(path):
    # 只向本轮唯一QMP发送ACPI关机，不按名称杀进程或强制断电。
    with socket.socket(socket.AF_UNIX) as sock:
        sock.settimeout(5)
        sock.connect(str(path))
        with sock.makefile('rwb') as stream:
            json.loads(stream.readline())
            for name in ('qmp_capabilities', 'system_powerdown'):
                stream.write((json.dumps({'execute': name})+'\n').encode())
                stream.flush()
                while True:
                    response = json.loads(stream.readline())
                    if 'error' in response:
                        raise ValueError('QMP正常关机请求失败')
                    if 'return' in response:
                        break


def interrupted(*_):
    raise KeyboardInterrupt


def launch(profile, image, base):
    preflight(profile, image)
    base.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix='手动ZNS-', dir=base))
    argv = command(profile, image, run_dir)
    state = dict(阶段='启动准备', 启动器PID=os.getpid(), 客机PID=None,
                 镜像=str(image), SSH端口=8080, QMP=str(run_dir/'qmp.sock'),
                 参数=argv, 结果目录=str(run_dir),
                 关机方式='客机poweroff；或通过本轮QMP发送system_powerdown',
                 边界='主机变量不自动进入客机；不初始化SSD、不部署或启用ZenFS机制')

    def record():
        (run_dir/'状态.json').write_text(json.dumps(state, ensure_ascii=False, indent=2))

    record()
    (run_dir/'平台配置.json').write_text(json.dumps(profile, ensure_ascii=False, indent=2))
    proc, code = None, 1
    previous = signal.signal(signal.SIGTERM, interrupted)
    try:
        print('运行目录：'+str(run_dir), flush=True)
        proc = subprocess.Popen(argv, stdin=None, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, start_new_session=True)
        state.update(阶段='运行中', 客机PID=proc.pid)
        record()
        with (run_dir/'运行.log').open('wb') as log:
            while chunk := os.read(proc.stdout.fileno(), 4096):
                log.write(chunk)
                log.flush()
                sys.stdout.buffer.write(chunk)
                sys.stdout.buffer.flush()
        code = proc.wait()
    except KeyboardInterrupt:
        code = 130
    finally:
        if proc is not None and proc.poll() is None:
            try:
                powerdown(run_dir/'qmp.sock')
                proc.wait(timeout=60)
            except (OSError, ValueError, subprocess.TimeoutExpired) as error:
                state['关机反馈'] = str(error)
        alive = proc is not None and proc.poll() is None
        if proc is not None and not alive and proc.stdout is not None:
            proc.stdout.close()
        state.update(阶段='关机未完成，需按记录PID处理' if alive else '已退出',
                     客机PID=proc.pid if alive else None, 本轮客机PID=proc.pid if proc else None,
                     客机退出码=proc.poll() if proc else None, 启动器退出码=code,
                     结束UTC=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()))
        record()
        signal.signal(signal.SIGTERM, previous)
        if alive:
            print('正常关机未完成，未强制断电：'+str(run_dir/'状态.json'), file=sys.stderr)
    return code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', type=Path, default=DEFAULT)
    parser.add_argument('--image', type=Path, default=Path('/home/data/xwz/femu.qcow2'))
    parser.add_argument('--result-base', type=Path, default=Path('/home/data/xwz/rrzc-manual'))
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--dry-run', action='store_true', help='仅显示命令与配置，不启动或创建文件')
    modes.add_argument('--check', action='store_true', help='仅检查启动条件，不启动')
    args = parser.parse_args()
    profile, image = load(args.profile), args.image.resolve()
    if args.dry_run:
        print(json.dumps(dict(settings(profile), 启动命令=command(profile, image, Path('本轮运行目录'))),
                         ensure_ascii=False, indent=2))
        return 0
    if args.check:
        preflight(profile, image)
        print('配置及资源检查通过，未启动客机')
        return 0
    return launch(profile, image, args.result_base)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError) as error:
        print('启动被拒绝：'+str(error), file=sys.stderr)
        sys.exit(1)
