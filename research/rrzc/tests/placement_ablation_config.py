"""只输出待运行配置，不能启动进程或复用运行中的客机状态。"""
import argparse
import json
from pathlib import Path

MODES=('G0','G1','G2')
OLD_KEYS=('ZENFS_RRZC_POOL_K','ZENFS_RRZC_POOL_TAGS','ZENFS_RRZC_STREAM_CLASSIFY',
    'ZENFS_RRZC_POOL_FLUSH_W','ZENFS_RRZC_POOL_PAIR_ADMISSION',
    'ZENFS_RRZC_POOL_PAIR_OBSERVE','ZENFS_RRZC_POOL_PAIR_COPY_BUDGET')


def resolve(mode,environment):
    if mode not in MODES:raise ValueError('必须明确选择G0、G1或G2')
    if any(k in environment for k in OLD_KEYS):
        raise ValueError('请先移除旧放置组合开关，禁止静默覆盖')
    if environment.get('ZENFS_RRZC_PLACEMENT_MODE',mode)!=mode:
        raise ValueError('已有显式模式与请求模式不同，禁止切换')
    return dict(模式=mode,环境变量={'ZENFS_RRZC_PLACEMENT_MODE':mode},
        派生K=int(mode!='G0'),H统计=True,Flush来源统计=True,
        必要迁移H入池=mode=='G2',必要迁移HW入池=mode=='G2',
        必要迁移Wonly入池=mode!='G0',已在池内变H='不主动搬出',
        独立启动=True,复用运行状态=False,提供业务执行器=False)


def plan(contract):
    required={'初始磁盘镜像','固定预填输入','请求事件流','公共参数','模式'}
    if set(contract)!=required or contract['模式']!=list(MODES):
        raise ValueError('三组契约字段或模式顺序不完整')
    for key in ('初始磁盘镜像','固定预填输入','请求事件流'):
        item=contract[key]
        if set(item)!={'路径','SHA256'} or not Path(item['路径']).is_absolute():
            raise ValueError('输入必须指定绝对路径及身份')
        value=item['SHA256']
        if not isinstance(value,str) or len(value)!=64 or any(c not in '0123456789abcdef' for c in value):
            raise ValueError('输入SHA256格式非法')
    common=contract['公共参数']
    if common!={'容量GiB':8,'ZoneMiB':128,'Open':8,'Active':12,'RR阈值':1024,
        'GC启动百分比':20,'选择器':'Greedy','随机种子':20261023,
        '缓存':False,'WAL':False,'串行运行':True}:
        raise ValueError('公共参数与当前阶段约定不同')
    return dict(阶段='只生成运行契约，不启动设备',身份验证='提供者声明，尚未核验文件字节或父链',
        各组=[dict(**resolve(m,{}),初始磁盘镜像=dict(contract['初始磁盘镜像']),
            固定预填输入=dict(contract['固定预填输入']),请求事件流=dict(contract['请求事件流']),
            公共参数=dict(common)) for m in MODES],
        启动前必须核验=['同一只读父镜像及完整父链身份，三组各自新启动，串行关闭后再起下一组',
            '独立重建FEMU内存盘及RC；qcow2不能当作NAND状态快照',
            '预填输入、请求流、Zone状态/WP、有效SST映射、块身份/RC、Reset代际及待处理RR',
            '接入image_lifecycle与result_storage；程序与观测一致；不在旧业务脚本原地切换模式'])


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--mode',choices=MODES)
    p.add_argument('--contract',type=Path)
    args=p.parse_args()
    if bool(args.mode)==bool(args.contract):p.error('选择--mode或--contract其中之一')
    if args.mode:
        import os
        result=resolve(args.mode,os.environ)
    else:result=plan(json.loads(args.contract.read_text()))
    print(json.dumps(result,ensure_ascii=False,indent=2))
