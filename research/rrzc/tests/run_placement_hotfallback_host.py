"""独立构建后串行配对H普通目标策略；沿用原镜像和结果生命周期。"""
import argparse
import getpass
import json
from pathlib import Path
import shutil
import signal
from types import SimpleNamespace
from placement_hotfallback_source import MODE,prepare
import run_placement_wmig_build_host as build_entry
import run_placement_wmig_pair_host as pair_entry
from analyze_placement_hotfallback import analyze,finish

BUILD_LABEL='joint-controlled-placement-wmig-hotfallback-build-v1-20261109'
PAIR_LABEL='joint-controlled-placement-wmig-hf-pair-v1-20261109'


def check_control_path(label):
    # OpenSSH建监听套接字会追加临时后缀，不能只检查最终ControlPath长度。
    for mode in ('G2',MODE):
        path='/tmp/'+label+'-'+mode.lower().replace('_','-')+'-calibration-ssh.sock'
        pair_entry.require(len(path.encode())+17<108,'实验标签过长，SSH临时控制套接字将超出Unix路径限制')


def configure_pair():
    pair_entry.MODE=MODE;pair_entry.MODES=('G2',MODE)
    pair_entry.PROGRAM=pair_entry.IMAGE_DIR/(BUILD_LABEL+'-results')
    pair_entry.analyze=analyze;pair_entry.finish=finish
    previous_wrapper=pair_entry.wrapper
    pair_entry.wrapper=lambda text:previous_wrapper(text).replace('G2_WMIG_OFF','G2_HFALLBACK')


def run_build(args):
    build_entry.prepare_source=prepare
    build_entry.MODE=MODE
    resource=build_entry.preflight()
    resource['目录已占字节']=build_entry.check_baseline_budget(8<<30)
    build_entry.require(resource['data可用字节']>=42<<30 and resource['可用内存字节']>=30<<30,'原保护线不足')
    output=build_entry.IMAGE_DIR/(args.label+'-results')
    build_entry.require(not output.exists(),'不覆盖旧构建')
    password=getpass.getpass('H普通目标独立构建认证（仅内存）：')
    output.mkdir();payload=build_entry.prepare(output)
    native=SimpleNamespace(label=args.label,mode=MODE,reuse_program=None)
    build_entry.stamp(output/'预注册计划.json',dict(模式=MODE,独立全量编译=True,K=1,
        规则='原合格普通部分目标内，H优先低有效量；同批其他迁移软避开H目标；不叠加W禁止',
        不改变触发和victim=True,资源=resource))
    if args.detach and not build_entry.detach_supervisor(output):return
    try:
        build_entry.execute(native,password,output,payload)
        proof=__import__('json').loads((output/'设备集成核验.json').read_text())
        build_entry.require(proof['通过'] and proof['模式']==MODE,'新设备验证未通过')
    finally:password=None


def run_pair(args):
    configure_pair();check_control_path(args.label)
    pair_entry.wait_for_slot()
    resource=pair_entry.preflight();used=pair_entry.check_baseline_budget(8<<30)
    pair_entry.require(resource['data可用字节']>=42<<30 and resource['可用内存字节']>=30<<30,'原配对保护线不足')
    output=pair_entry.IMAGE_DIR/(args.label+'-results')
    pair_entry.require(not output.exists(),'不覆盖旧业务结果')
    proof=__import__('json').loads((pair_entry.PROGRAM/'设备集成核验.json').read_text())
    pair_entry.require(proof['通过'] and proof['模式']==MODE,'先完成构建和真实设备验证')
    password=getpass.getpass('H普通目标串行对照认证（仅内存）：')
    output.mkdir();freeze=output/'入口源码';freeze.mkdir()
    files=[Path(__file__),Path(__file__).with_name('placement_hotfallback_source.py'),
           Path(__file__).with_name('run_placement_wmig_pair_host.py'),
           Path(__file__).parents[1]/'model/rrzc_hot_fallback.h',
           Path(__file__).parents[1]/'analyze_placement_hotfallback.py']
    for p in files:shutil.copyfile(p,freeze/p.name)
    pair_entry.stamp(freeze/'SHA256.json',{p.name:pair_entry.digest(p) for p in freeze.iterdir() if p.is_file()})
    pair_entry.stamp(output/'预注册计划.json',dict(模式=['G2',MODE],平台=pair_entry.profile8(),
        请求数=2260992,种子=20261023,K=1,RR=1024,GC启动百分比=20,选择器='两臂Greedy',
        缓存=False,WAL=False,额外Greedy重复=False,无QPS限速=True,串行=True,
        观测='共同aux调用计时与H回退资源；组成账目不参与选择',资源=resource,目录已占字节=used,
        边界='自然后台维护；检查首个策略执行前布局分歧，不将全部差异强归因于策略'))
    signal.signal(signal.SIGTERM,lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    if args.detach and not pair_entry.detach_supervisor(output):return
    try:pair_entry.execute(output,SimpleNamespace(label=args.label,prepared_source=None),password)
    except BaseException as e:
        pair_entry.record(output,阶段='H普通目标对照停止，保留失败证据',原始错误=repr(e));raise
    finally:password=None


def resume_pair(args):
    configure_pair();check_control_path(args.label)
    output=args.resume_pair.resolve()
    pair_entry.require(output.parent==pair_entry.IMAGE_DIR.resolve(),'续跑来源越界')
    state=json.loads((output/'状态.json').read_text())
    pair_entry.require(state.get('当前客机PID') is None and state.get('已完成')==['G2'],'只能接续已完成基线且未完成新臂的本轮')
    pair_entry.closed_archive(output/'G2')
    failed=output/MODE;old=json.loads((failed/'状态.json').read_text())
    pair_entry.require(old['当前客机PID'] is None and not old.get('最近观测') and
        '认证失败' in old.get('原始错误','') and not (failed/'业务核验.json').exists(),'失败臂已有业务或尚未退出')
    pair_entry.wait_for_slot(output)
    pair_entry.preflight();pair_entry.check_baseline_budget(8<<30)
    info=json.loads((output/'共同材料身份.json').read_text())
    for name,key in [('db_bench','程序SHA256'),('固定输入复用.tar','输入包SHA256'),('程序及工具.tar.gz','程序包SHA256')]:
        pair_entry.require(pair_entry.digest(output/'共同材料'/name)==info[key],'共同材料身份发生变化')
    parents=json.loads((output/'父链共同身份.json').read_text())
    pair_entry.require(pair_entry.parent_identity()==parents,'父链变化，不续跑')
    password=getpass.getpass('仅补跑未开始新臂认证（仅内存）：')
    saved=output/(MODE+'-启动失败');pair_entry.require(not saved.exists(),'失败证据已经整理，不重复续跑')
    failed.rename(saved)
    # 移动失败证据后同步已回收镜像的归档引用，旧tar本身保持逐字不变。
    image=old.get('镜像处理',{})
    if image.get('status')=='deleted' and image.get('archive'):
        from image_lifecycle import ImageLease
        moved=saved/Path(image['archive']).name
        pair_entry.require(pair_entry.digest(moved)==image['archive_sha256'],'失败归档移动后身份不符')
        image.update(archive=str(moved),output=str(saved))
        lease=ImageLease(pair_entry.IMAGE_DIR,Path(image['image']),Path(image['parent']),saved,
                         pair_entry.ROOT.parents[1]/'build-femu/qemu-img')
        lease.data=image;lease.save();pair_entry.stamp(saved/'状态.json',old)
    plan=dict(保留基线=True,基线不重跑=True,新臂没有业务请求=True,失败证据=str(saved),
        续跑短标签=args.label,原始错误=old['原始错误'],程序SHA256=info['程序SHA256'],
        证据边界='原SSH退出内容未记录；116字节本地Unix路径失败可复现，短路径认证将单独核验。')
    pair_entry.stamp(output/'续跑计划.json',plan)
    freeze=output/'续跑源码';freeze.mkdir();shutil.copyfile(__file__,freeze/Path(__file__).name)
    pair_entry.stamp(freeze/'SHA256.json',{p.name:pair_entry.digest(p) for p in freeze.iterdir() if p.is_file()})
    signal.signal(signal.SIGTERM,lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    # 原监督日志是失败证据，接续监督写入独立子目录。
    monitor=output/'续跑监督';monitor.mkdir()
    if args.detach and not pair_entry.detach_supervisor(monitor):return
    try:
        pair_entry.run_mode(output,SimpleNamespace(label=args.label),password,MODE,info,parents)
        result=finish(output)
        pair_entry.record(output,阶段='H普通目标对照完成，基线保留且新臂已归档退出',完整实验完成=True,
            当前客机PID=None,总迁移变化百分比=result['总迁移变化百分比'])
        ref=info['固定输入引用'];payload=output/'共同材料/固定输入复用.tar'
        pair_entry.require(pair_entry.digest(Path(ref['archive']))==ref['sha256'],'来源变化，保留传输包')
        pair_entry.stamp(output/'共享传输包回收核验.json',dict(路径=str(payload),SHA256=pair_entry.digest(payload),来源=ref))
        payload.unlink()
    except BaseException as e:
        pair_entry.record(output,阶段='续跑失败，停止推进并保留证据',原始错误=repr(e));raise
    finally:password=None


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage',choices=['build','pair'],required=True)
    parser.add_argument('--label');parser.add_argument('--detach',action='store_true')
    parser.add_argument('--resume-pair',type=Path)
    args=parser.parse_args()
    args.label=args.label or (BUILD_LABEL if args.stage=='build' else PAIR_LABEL)
    pair_entry.require(args.label.startswith('joint-controlled-placement-wmig-') and
        all(c in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in args.label),'标签非法')
    if args.stage=='build':
        pair_entry.require(not args.resume_pair,'构建不能接续业务');run_build(args)
    elif args.resume_pair:resume_pair(args)
    else:run_pair(args)


if __name__=='__main__':main()
