"""H普通回退独立对照；复用既有逐笔账目和共同aux计时。"""
import json
import analyze_phase_time
import analyze_placement_wmig as previous
from analyze_placement_business import analyze as business,compare
from ycsb_af import require

MODE='G2_HFALLBACK'


def analyze(root,mode):
    analyze_phase_time.KINDS[14]='aux目录同步实际调用'
    # 直接复用业务审计后独立核验新模式，不将新模式伪装成旧模式。
    report=business(root,mode)
    log=(root/'run.zenfs.log').read_text()
    require('放置消融配置：模式='+mode+'，' in log,'配置模式不精确')
    require('W迁移准入回退：' not in log,'本轮不允许叠加W迁移禁止')
    records=[]
    for lineno,line in enumerate(log.splitlines(),1):
        if 'H回退目标排序：' not in line:continue
        suffix=line.split('H回退目标排序：',1)[1]
        values={k:int(v) for k,v in (part.split('=',1) for part in suffix.split('，'))}
        records.append(dict(行=lineno,**values))
    if mode=='G2':require(not records,'G2执行了新排序')
    report['H普通回退']=dict(排序事件=records,次数=len(records),历史H不参与=True,额外池名额=0)
    # aux原始区间的格式及720次业务调用沿用上一轮；保留观测边界。
    import struct
    raw=(root/'等待区间.bin').read_bytes()
    intervals=[r for r in struct.iter_unpack('<6Q',raw[16:]) if r[0]==14]
    require(len(intervals)==720,'aux业务调用记录缺失')
    report['aux实际调用']=dict(次数=len(intervals),调用累计秒=sum(r[2]-r[1] for r in intervals)/1e9,
        区间并集秒=sum(b-a for a,b in analyze_phase_time.merge([(r[1],r[2]) for r in intervals]))/1e9,
        包围额外秒=None,边界='两臂相同内存计时；不代表纯系统调用或介质时间')
    report['W迁移准入']=dict(拒绝次数=0,事件=[])
    return report


def finish(output):
    # 原汇总只依赖两臂通用字段；模块变量仅本进程改变，不写旧脚本或旧结果。
    original=previous.MODE
    previous.MODE=MODE
    try:result=previous.finish(output)
    finally:previous.MODE=original
    result['边界']='同程序、相同逻辑输入、独立客机的单轮配对；H普通目标重排可能改变后续空间与维护成本。'
    result['因果限制']='必须检查首次策略执行前的维护布局差异，不能把全部差额强归因于新排序。'
    (output/'配对分析/完整核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    path=output/'配对分析/结果分析.md'
    text=path.read_text().replace('G2与W-only迁移准入配对结果','G2与H普通迁移目标改进配对结果')
    text=text.replace('|指标|G2|G2_WMIG_OFF|','|指标|G2|G2_HFALLBACK|')
    text=text.replace('旧W回退普通区可能转移成本','H普通目标重排可能改变后续空间与维护成本')
    text=text.replace('没有按跳过目标数量推导节省字节','没有按目标重排次数推导节省字节')
    path.write_text(text)
    return result
