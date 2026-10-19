"""复核趋势验证中的解释性实例；实例按事后条件选取，不用于拟合或宣称预测准确率。"""
import hashlib,json
from pathlib import Path
from analyze_validity_signal import audit as signal_audit,LABEL,SECOND
from analyze_rr_opportunities import stable
from analyze_maintenance_overlap import RR_START
from analyze_stream import rows
from analyze_v1_shadow_stream import fields,require

def follow_empty(current,zones):
    """同Zone追踪十秒；换会话、统计不稳或非空Reset时停止，不拼接新代际。"""
    cutoff=int(current['查询结束纳秒']);last_positive_begin=int(current['查询开始纳秒'])
    for z in zones:
        if z['Zone']!=current['Zone']:continue
        end=int(z['查询结束纳秒'])
        if end<=cutoff:continue
        if end>cutoff+10*SECOND:break
        if int(z['查询开始纳秒'])<=cutoff or not stable(z):
            return dict(确认空区=False,原因='查询重叠或统计不稳定')
        if (z['会话高位'],z['会话低位'])!=(current['会话高位'],current['会话低位']):
            return dict(确认空区=False,原因='设备会话改变')
        device_changed=z['设备代际后']!=current['设备代际后']
        host_changed=z['主机代际后']!=current['主机代际后']
        if device_changed!=host_changed:
            return dict(确认空区=False,原因='设备／主机代际变化未同时确认')
        if int(z['有效字节'])==0:
            return dict(确认空区=True,首次观测有效量为零上界秒=(end-cutoff)/SECOND,
                前次非空查询起点下界秒=max(0,(last_positive_begin-cutoff)/SECOND),
                同时确认Reset=device_changed and host_changed,起点RC=int(current['RC']),
                空区快照=z,
                边界='下界用前次非空查询开始、上界用空区查询结束，不能当成精确失效／Reset时刻。')
        if device_changed or host_changed:return dict(确认空区=False,原因='已跨入新代际且有新有效数据，停止追踪')
        last_positive_begin=int(z['查询开始纳秒'])
    return dict(确认空区=False,原因='现有十秒观察内未确认有效量为零，不等于未来不会失效')

def audit(results):
    root=results/LABEL;report_path=root/'有效量趋势验证.json';report=json.loads(report_path.read_text())
    require(signal_audit(results)==report,'原信号验证复算不一致')
    chosen={}
    for dataset,data in report['固定四份完整观察'].items():
        for r in data['首个观察明细']:
            if r['窗口秒']!=10 or not r['事后审计_整轮从未迁移触及']:continue
            if not r['事后标签']['完整十秒删除观察'] or r['事后标签']['十秒内成功删除SST字节']!=r['当前有效字节']:continue
            if r['范围']=='基础回收资格_已达阈值' and r['事前趋势']['分类']=='多次下降':kind='高RC且下降后清空'
            elif r['范围']=='所有满区SST' and r['事前趋势']['分类']=='未下降':kind='未下降但随后删除'
            else:continue
            if kind in chosen:continue
            run=Path(data['来源']);zones=rows(run/'zones.csv')
            current=next(z for z in zones if z['批次']==r['批次'] and z['Zone']==r['Zone'])
            lines=(run/'zenfs.run.log').read_text().splitlines();rr=[];zc=[]
            for number,line in enumerate(lines,1):
                if (m:=RR_START.search(line)) and m.group(1)==r['Zone']:rr.append(number)
                if '独立约束决策：' in line:
                    d=fields(line,'独立约束决策：')
                    if d['存在Greedy']=='1' and d['实际Zone']==r['Zone']:zc.append(number)
            chosen[kind]=dict(数据集=dataset,原始来源=str(run),原观察=r,后续有效量=follow_empty(current,zones),
                全程该地址RR开始行=rr,全程该地址ZC选中行=zc,
                实例边界='按事后条件挑首个解释例，不是盲测样本；静态基础资格不等于实际GC时刻可以改选，也不计算假想节省量。')
    return dict(原信号核验逐字段一致=True,解释性实例=chosen,
        输入报告SHA256=hashlib.sha256(report_path.read_bytes()).hexdigest(),
        本实例分析源码SHA256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        结论边界='高RC数据可在维护复制前自行失效的动机可以核实；是否据此改选、能否稳定减少总迁移仍未验证。')

if __name__=='__main__':
    import sys
    require(len(sys.argv)==2,'需要已有results目录');results=Path(sys.argv[1]).resolve()
    output=results/LABEL/'解释实例复核.json';require(not output.exists(),'实例已存在，拒绝覆盖')
    output.write_text(json.dumps(audit(results),ensure_ascii=False,indent=2)+'\n')
    print('解释实例及后续清空／Reset观察已复核；没有将其写成模型预测或实际收益。')
