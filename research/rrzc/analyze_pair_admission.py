"""核验配对准入四臂；未通过重复与完整归档时不发布收益结论。"""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import re
import sys

sys.path.insert(0,str(Path(__file__).resolve().parent/'tests'))
from analyze_pool_dbbench import SEMANTIC
from analyze_reproducibility import initial,request_comparison
from analyze_flush_control import flush_files,composition
from ycsb_af import digest,require


def fields(line,marker):
    # 原因和错误文本中也有中文逗号，只在下一组“键=”处切分。
    text=line.split(marker,1)[1].strip();matches=list(re.finditer(r'(?:^|，)([^，=]+)=',text))
    out={}
    for index,m in enumerate(matches):
        key=m[1];require(key not in out,'准入日志字段重复')
        out[key]=text[m.end():matches[index+1].start() if index+1<len(matches) else len(text)]
    return out


def choices(path,enforce):
    rows=[];blocks={}
    for line_number,line in enumerate(path.read_text().splitlines(),1):
        if '配对逐块证据：' in line:
            b=fields(line,'配对逐块证据：');blocks.setdefault(int(b['序号']),[]).append(b)
        if '配对准入决定：' not in line:continue
        f=fields(line,'配对准入决定：');n=lambda k:int(f[k]);seq=n('序号')
        require(seq==len(rows)+1 and n('执行准入')==enforce,'准入序号或开关不符')
        require(n('窗口')==10000000000 and n('预算')==0,'预注册参数改变')
        require(n('H')<=n('账目有效') and n('W')<=n('账目有效') and
                n('HW')<=min(n('H'),n('W')),'标签有效量非法')
        proof=blocks.pop(seq,[])
        if n('共区') and n('允许'):
            require(n('风险可知') and (not(n('类别')&1) or n('H源压力可知')),
                    '允许混放却缺少风险或迁入H证据')
            require(n('ETA')>n('窗口') or n('伴随复制上界')<=n('预算'),
                    '超过复制预算仍被允许')
        if n('风险可知'):
            targets=[b for b in proof if b['角色']=='目标']
            require(targets and len({b['块'] for b in targets})==len(targets),'目标逐块证据缺失或重复')
            require(len({(b['会话'],b['设备代际'],b['主机Reset序号'],b['快照'],b['代际起点'])
                         for b in targets})==1,'目标逐块跨会话、Reset或时间拼接')
        rows.append(dict(日志行=line_number,**f,ZoneID=n('Zone')//(128<<20),逐块证据数=len(proof),
                         有效量账目相同=n('有效')==n('账目有效')))
    require(rows and not blocks,'没有准入记录或存在无决定的块帧')
    summary=dict(目标申请数=len(rows),共区申请数=sum(int(r['共区']) for r in rows),
        拒绝建议数=sum(r['允许']=='0' for r in rows),
        实际拒绝数=sum(r['允许']=='0' and r['执行准入']=='1' for r in rows),
        有效量不匹配数=sum(not r['有效量账目相同'] for r in rows),
        风险可知共区数=sum(r['共区']=='1' and r['风险可知']=='1' for r in rows),
        原因计数=dict(Counter(r['原因'] for r in rows)),
        新文件拒绝数=sum(r['阶段']=='新文件' and r['允许']=='0' and r['执行准入']=='1' for r in rows),
        必要迁移拒绝数=sum(r['阶段']=='必要迁移' and r['允许']=='0' and r['执行准入']=='1' for r in rows))
    return rows,summary


def write_csv(path,rows):
    keys=list(dict.fromkeys(k for r in rows for k in r))
    with path.open('x',newline='') as stream:
        out=csv.DictWriter(stream,keys);out.writeheader();out.writerows(rows)


def analyze(root,dest):
    state=json.loads((root/'状态.json').read_text());archive=json.loads((root/'阶段归档.json').read_text())
    require(archive['完整'] and archive['业务成功'] and not archive['客机仍存活'] and
            state['当前客机PID'] is None,'未完整归档退出，不作收益比较')
    require(digest(Path(state['镜像处理']['archive']))==state['镜像处理']['archive_sha256'],'归档哈希不同')
    for name in ('原放置Greedy','准入Greedy'):
        require(json.loads((root/(name+'重复核验.json')).read_text())['通过'],'同策略重复未通过')
    identity=json.loads((root/'生命周期构建核验.json').read_text())
    require(identity['配对准入版本']==1 and identity['交错小测逐条请求通过'],'程序身份未验收')
    records=[];metrics=[];latencies=[];summaries={};proofs={};transactions=[];layouts=[]
    tags=json.loads((root/'源码/受控标签来源.json').read_text())['标签']
    for tag in ('p1','p2','m1','m2'):
        path=root/'逐臂采证'/tag;enforce=int(tag.startswith('m'))
        rows,summary=choices(path/'run.zenfs.log',enforce)
        records.extend(dict(臂=tag,**r) for r in rows);summaries[tag]=summary
        r=json.loads((root/(tag+'-核验.json')).read_text())
        fresh=flush_files(path);copied_totals=dict(H=0,Flush输出=0,其他=0)
        for number,tx in enumerate(r['事务'],1):
            copied=dict(H=0,Flush输出=0,其他=0)
            for item in tx['复制']:
                f=item['字段'];name=f['文件名'];kind='H' if name in tags else 'Flush输出' if name in fresh else '其他'
                require(f['复制成功']=='1','出现失败复制，不能遗漏其成本')
                copied[kind]+=int(f['有效字节'])
            require(sum(copied.values())==tx['迁移字节'],'复制分类与维护字节不符')
            for k,v in copied.items():copied_totals[k]+=v
            transactions.append(dict(臂=tag,维护序号=number,类型=tx['类型'],源ZoneID=tx['Zone']//(128<<20),
                迁移字节=tx['迁移字节'],**{k+'复制字节':v for k,v in copied.items()}))
        layout,_=composition(path,tags,'准入放置' if enforce else '现有放置',tag);layouts+=layout
        log=(path/'run.zenfs.log').read_text()
        finish=[(int(n),int(ok)) for n,ok in re.findall(
            r'公共普通分配Finish代价：[^\n]*牺牲尾部字节=(\d+)，成功=(\d+)',log)]
        metrics.append(dict(臂=tag,模式='准入放置' if enforce else '现有放置',**r['维护'],**r['计时'],
            **{k:v for k,v in r['末端空间'].items() if k!='Zone'},**{k:v for k,v in summary.items() if k!='原因计数'},
            **{k+'复制字节':v for k,v in copied_totals.items()},
            普通成功Finish次数=sum(ok for _,ok in finish),普通成功Finish尾损字节=sum(n for n,ok in finish if ok),
            RR源剩余封闭字节=sum(map(int,re.findall(r'RR 开始：[^\n]*封闭剩余容量 (\d+) 字节',log)))))
        latencies.extend(dict(臂=tag,**x) for x in r['API延迟'])
        if tag!='p1':
            start=initial(root/'逐臂采证/p1',path);request=request_comparison(root/'逐臂采证/p1',path)
            require(all(start[k]['相同'] for k in SEMANTIC) and request['逻辑提交相同'] and
                    request['完整成功请求数']==[1130496,1130496],'跨策略起点或逻辑输入不同')
            proofs[tag]=dict(起点=start,请求=request)
    require(not dest.exists(),'不覆盖分析结果');dest.mkdir(parents=True)
    write_csv(dest/'逐次准入.csv',records);write_csv(dest/'维护空间与吞吐.csv',metrics)
    write_csv(dest/'API延迟.csv',latencies)
    write_csv(dest/'逐笔维护来源.csv',transactions);write_csv(dest/'H共区快照.csv',layouts)
    result=dict(通过=True,来源=str(root),完整归档SHA256=state['镜像处理']['archive_sha256'],
        构建=identity,准入=summaries,跨臂=proofs,
        边界='四臂均Greedy；比较放置准入，非victim模型收益；受控单前台与维护边界；未证明正常并发性能')
    (dest/'配对准入核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('source',type=Path);p.add_argument('output',type=Path)
    a=p.parse_args();r=analyze(a.source,a.output)
    print(json.dumps(dict(通过=r['通过'],准入=r['准入']),ensure_ascii=False,indent=2))
