"""同程序H-only起点，只切Flush来源开关；先核验再比较，不把来源当寿命。"""
import argparse
import csv
import json
from pathlib import Path
import re
from analyze_flush_origin import verify_origins
from analyze_sst_compaction import events
from analyze_pool_dbbench import SEMANTIC
from analyze_reproducibility import initial, request_comparison
from ycsb_af import digest, require


def context(root):
    state=json.loads((root/'状态.json').read_text())
    archive=json.loads((root/'阶段归档.json').read_text())
    require(archive['完整'] and archive['业务成功'] and not archive['客机仍存活']
            and state['当前客机PID'] is None, '实验未完整归档退出')
    require(digest(Path(state['镜像处理']['archive'])) == state['镜像处理']['archive_sha256'], '归档哈希不同')
    require(json.loads((root/'Greedy重复核验.json').read_text())['通过'], '同配置独立重复未通过')
    identity=json.loads((root/'生命周期构建核验.json').read_text())
    require(identity['通过'], '程序未验收')
    tags=json.loads((root/'源码/受控标签来源.json').read_text())['标签']
    require(len(tags)==2 and set(tags.values())=={'H'}, '初始标签不是H-only')
    return state,identity,tags


def flush_files(root):
    records=events(root/'run.rocksdb.log')
    jobs={e['记录']['job'] for e in records if e['记录'].get('event')=='flush_started'}
    fresh={'/rrzc-dbbench/'+str(e['记录']['file_number']).zfill(6)+'.sst'
           for e in records if e['记录'].get('event')=='table_file_creation'
           and e['记录'].get('status')=='OK' and e['记录']['job'] in jobs}
    require(fresh, 'Flush输出缺失')
    return fresh


def composition(root, tags, mode, arm):
    fresh=flush_files(root)
    require(not fresh.intersection(tags), 'Flush输出覆盖初始H')
    result=[]
    for n in range(62):
        with (root/f'布局-{n}.extent.csv').open() as f:rows=list(csv.DictReader(f))
        zones={r['Zone'] for r in rows if r['文件'] in tags}
        require(zones, '检查点H已丢失')
        for zone in sorted(zones,key=int):
            selected=[r for r in rows if r['Zone']==zone]
            h=sum(int(r['字节']) for r in selected if r['文件'] in tags)
            w=sum(int(r['字节']) for r in selected if r['文件'] in fresh)
            other=sum(int(r['字节']) for r in selected if r['文件'] not in tags and r['文件'] not in fresh)
            result.append(dict(模式=mode,臂=arm,布局=n,ZoneID=int(zone)//(128<<20),
                               H字节=h,本轮Flush输出字节=w,其他字节=other))
    return result,len(fresh)


def flush_segments(root):
    """仅合计实测时点之差，不把同步调用区间直接命名为物理盘耗时。"""
    jobs={}
    for job,file,phase,t in re.findall(r'Flush分段：任务=(\d+)，文件=(\d+)，阶段=([^，]+)，纳秒=(\d+)',
                                      (root/'run.log').read_text()):
        record=jobs.setdefault((job,file),{})
        require(phase not in record, '同一Flush分段重复')
        record[phase]=int(t)
    require(len(jobs)==360, '业务Flush分段不完整')
    result={}
    for before,after in (('构建前','构建后'),('日志刷新前','日志刷新后'),
                         ('目录同步前','目录同步后'),('数据库锁前','数据库锁后')):
        require(all(before in v and after in v and v[after]>=v[before] for v in jobs.values()), 'Flush阶段缺失或时间倒退')
        values=[v[after]-v[before] for v in jobs.values()]
        result[before+'至'+after]=dict(次数=len(values),累计纳秒=sum(values),最大纳秒=max(values))
    return result


def compare(off,on,output):
    require(not output.exists(), '不覆盖派生结果')
    states={};builds={};tags={}
    for mode,root in (('off',off),('on',on)):
        states[mode],builds[mode],tags[mode]=context(root)
        require(bool(states[mode]['业务Flush来源W']) == (mode=='on'), '实际来源开关不符')
    require(tags['off']==tags['on'], '初始H标签不同')
    for key in ('程序SHA256','新客户端源码SHA256','冻结ZenFS对象SHA256','计划头SHA256'):
        require(builds['off'][key]==builds['on'][key], '软件身份不同：'+key)
    require(states['off']['实际FEMUSHA256']==states['on']['实际FEMUSHA256'], 'FEMU身份不同')
    a=(off/'源码/run_pool_business_guest.sh').read_text()
    b=(on/'源码/run_pool_business_guest.sh').read_text()
    require(a.count('flush_w=0;hotspot=1;phase run')==1 and
            b.count('flush_w=1;hotspot=1;phase run')==1 and
            a.replace('flush_w=0;hotspot=1;phase run','flush_w=1;hotspot=1;phase run')==b,
            '业务包装器不止来源开关不同')
    for name in ('受控标签.txt','run_pool_source_guest.sh','expected.json'):
        require((off/'源码'/name).read_bytes()==(on/'源码'/name).read_bytes(), '共同输入文件不同：'+name)
    plans=[json.loads((p/'预注册计划.json').read_text()) for p in (off,on)]
    require(plans[0]['平台']==plans[1]['平台'], '平台配置不同')
    starts={};requests={};reports={};layouts=[];metrics=[];latencies=[];transactions=[]
    from flush_control_bundle import disabled_guard
    for arm in ('p1','p2'):
        left,right=(r/'逐臂采证'/arm for r in (off,on))
        starts[arm]=initial(left,right)
        require(all(starts[arm][k]['相同'] for k in SEMANTIC), '跨开关起点不符：'+arm)
        requests[arm]=request_comparison(left,right)
        require(requests[arm]['逻辑提交相同'] and
                requests[arm]['完整成功请求数']==[1130496,1130496], '跨开关请求不同')
        disabled_guard(left)
        origin,_=verify_origins(right,tags['on'])
        for mode,root in (('off',off),('on',on)):
            path=root/'逐臂采证'/arm
            r=json.loads((root/(arm+'-核验.json')).read_text())
            layout,count=composition(path,tags[mode],mode,arm);layouts+=layout
            fresh=flush_files(path)
            for n,tx in enumerate(r['事务'],1):
                copied=dict(H=0,本轮Flush输出=0,其他=0)
                for item in tx['复制']:
                    fields=item['字段'];name=fields['文件名']
                    category='H' if name in tags[mode] else '本轮Flush输出' if name in fresh else '其他'
                    require(fields['复制成功']=='1', '事务中存在失败复制')
                    copied[category]+=int(fields['有效字节'])
                require(sum(copied.values())==tx['迁移字节'], '来源分解与真实迁移量不同')
                transactions.append(dict(模式=mode,臂=arm,维护序号=n,类型=tx['类型'],
                    源ZoneID=tx['Zone']//(128<<20),日志开始行=tx['开始行'],迁移字节=tx['迁移字节'],
                    **{k+'复制字节':v for k,v in copied.items()}))
            log=(path/'run.zenfs.log').read_text()
            free=list(map(int,re.findall(r'GC 检查：[^\n]*空闲比例=(\d+)%',log)))
            finish=[(int(n),int(ok)) for n,ok in re.findall(
                r'公共普通分配Finish代价：[^\n]*牺牲尾部字节=(\d+)，成功=(\d+)',log)]
            rr_tail=sum(map(int,re.findall(r'RR 开始：[^\n]*封闭剩余容量 (\d+) 字节',log)))
            evidence=dict(真实Flush输出数=count,新来源标记数=origin['创建标记数'] if mode=='on' else 0,
                重挂载来源数=origin['重挂载有效来源数'] if mode=='on' else 0,
                H与本轮Flush输出共区布局数=len({l['布局'] for l in layout if l['本轮Flush输出字节']>0}),
                GC检查数=len(free),最低空闲百分比=min(free),最高空闲百分比=max(free),
                普通分配成功Finish数=sum(ok for _,ok in finish),
                普通分配成功Finish尾损字节=sum(n for n,ok in finish if ok),RR开始源剩余封闭字节=rr_tail)
            reports[mode+'/'+arm]=dict(维护=r['维护'],计时=r['计时'],末端空间={k:v for k,v in r['末端空间'].items() if k!='Zone'},
                                      API延迟=r['API延迟'],证据=evidence,Flush分段=flush_segments(path),改选次数=r['改选次数'])
            metrics.append(dict(模式=mode,臂=arm,**r['维护'],**r['计时'],**reports[mode+'/'+arm]['末端空间'],**evidence))
            latencies.extend(dict(模式=mode,臂=arm,**item) for item in r['API延迟'])
    output.mkdir(parents=True)
    result=dict(来源={'off':str(off),'on':str(on)},程序身份={k:builds['off'][k] for k in
                ('程序SHA256','新客户端源码SHA256','冻结ZenFS对象SHA256')},跨开关起点=starts,
                跨开关请求=requests,业务包装器仅开关不同=True,各臂=reports,
                完整归档SHA256={k:v['镜像处理']['archive_sha256'] for k,v in states.items()},
                分析源码SHA256=digest(Path(__file__)),
                边界='两次重复的受控放置开关对照，非模型对照、非正常并发性能；Flush来源不是未来寿命真值。')
    (output/'同程序开关核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    for name,items in (('维护与吞吐.csv',metrics),('API延迟.csv',latencies),('H所在Zone构成.csv',layouts),
                       ('逐次维护与复制来源.csv',transactions)):
        with (output/name).open('x',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=list(items[0]));writer.writeheader();writer.writerows(items)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for n in ('off','on','output'):p.add_argument(n,type=Path)
    a=p.parse_args();result=compare(a.off,a.on,a.output)
    print(json.dumps({k:dict(维护=v['维护'],证据=v['证据']) for k,v in result['各臂'].items()},ensure_ascii=False,indent=2))
