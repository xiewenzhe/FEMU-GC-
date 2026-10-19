"""独立重算新留存评分及改选准入；不替代旧准入、尾部和源复核。"""
from analyze_v1_shadow_stream import fields,require,SECOND,EXTRA
from analyze_v1_growth_stream import qualifies
from analyze_short_shadow_stream import choose_short

class RetentionLog:
    def __init__(self,execute):
        self.execute=execute
        self.candidates={}
        self.suggestions={}
        self.resolved=[]
        self.known=0
        self.declining=0
    def observe(self,line):
        if '留存收益候选：' in line:
            r=fields(line,'留存收益候选：');key=(r['轮次'],int(r['Zone']))
            require(key not in self.candidates,'留存候选重复');self.candidates[key]=r
        if '留存收益建议：' in line:
            r=fields(line,'留存收益建议：');require(r['轮次'] not in self.suggestions,'留存建议重复')
            self.suggestions[r['轮次']]=r
    def resolve(self,rid,pending,estimates,old):
        rid=str(rid);s=self.suggestions[rid]
        require(s['执行']==str(int(self.execute)) and int(s['原建议Zone'])==(old[1] or 0),'留存入口或原建议不符')
        original=choose_short(pending,estimates)
        greedy,suggest,would,extra,predicted,_=original
        rows={}
        for c in pending:
            z=int(c['Zone']);r=self.candidates[rid,z]
            v=int(c['有效字节']);g=next((int(p['有效字节']) for p in pending if int(p['Zone'])==greedy),v)
            cost=max(0,v-g);require(int(r['有效字节'])==v and int(r['额外迁移字节'])==cost,'留存输入不是实际候选')
            known=r['可知']=='1';require(r['可知'] in ('0','1'),'留存可知标记非法')
            if known:
                a,b=int(r['十秒实际间隔纳秒']),int(r['三十秒实际间隔纳秒'])
                require(10*SECOND<=a<=12*SECOND and 30*SECOND<=b<=32*SECOND,'留存窗口跨度不符')
                loss=min(v,int(r['十秒减少字节'])*10*SECOND//a,int(r['三十秒减少字节'])*10*SECOND//b)
                retained=v-loss;require(int(r['预计保留字节'])==retained,'留存线性外推不符')
                benefit=max(0,retained-cost)
                require(int(r['净收益字节'])==benefit,'留存净收益不符')
                self.known+=1;self.declining+=loss>0
                rows[z]=(benefit,retained,cost)
            else:
                require(all(int(r[k])==0 for k in ('预计保留字节','净收益字节')),'未知留存仍输出收益')
        if would:
            choices=[]
            for c in pending:
                z=int(c['Zone']);e=estimates[z]
                if not qualifies(c) or z==greedy or z not in rows or e is None or e>10*SECOND:continue
                benefit,retained,cost=rows[z]
                if cost>EXTRA or benefit<=0:continue
                choices.append((-benefit,int(c['有效字节']),e,z,cost))
            if choices:
                _,_,predicted,suggest,extra=min(choices);would=True
            else:suggest=greedy;would=False;extra=0;predicted=0
        expected=(suggest or 0,int(would),extra,predicted)
        require(tuple(int(s[k]) for k in ('新建议Zone','会改选','额外字节','预测纳秒'))==expected,'留存新建议与独立排序不符')
        require(set(k[1] for k in self.candidates if k[0]==rid)==set(int(c['Zone']) for c in pending),'留存候选集合不符')
        self.resolved.append(rid)
        return (greedy,suggest,would,extra,predicted,None) if self.execute else old
    def finish(self,count):
        require(len(self.resolved)==len(self.suggestions)==count,'留存轮次未闭合')
