"""读取真实YCSB吞吐量，并保留计时边界及限速条件；不由延迟倒数推算。"""
from decimal import Decimal, InvalidOperation
import re
from analyze_v1_shadow_stream import require


def throughput(text, expected_ops, properties):
    def one(label):
        rows=re.findall(r'^'+re.escape(label)+r':\s*(\S+)\s*$',text,re.M)
        require(len(rows)==1,'吞吐量字段缺失或重复：'+label)
        try:value=Decimal(rows[0])
        except InvalidOperation as error:raise ValueError('非法吞吐量数字：'+label) from error
        require(value.is_finite() and value>0,'吞吐量数字必须为有限正数：'+label)
        return value

    seconds=one('Run runtime(sec)')
    operations=one('Run operations(ops)')
    rate=one('Run throughput(ops/sec)')
    require(operations==expected_ops and operations==operations.to_integral_value(),'吞吐量请求数不符')
    # 上游默认打印精度有限：用各字段最后一位的半单位做舍入区间交叉核验。
    def half_unit(value):return Decimal(1).scaleb(value.as_tuple().exponent)/2
    ds=half_unit(seconds);dr=half_unit(rate)
    require(seconds>ds and rate+dr>=operations/(seconds+ds)
        and rate-dr<=operations/(seconds-ds),'吞吐量与请求数/运行时间不一致')
    raw_limit=properties.get('limit.ops','0')
    require(re.fullmatch(r'\d+',raw_limit) is not None,'限速配置不是非负整数')
    limit=int(raw_limit)
    schedule=properties.get('limit.file','')
    return dict(平均ops每秒=float(rate),运行时间秒=float(seconds),完成操作数=int(operations),
        配置限速ops每秒=limit,限速计划=schedule,
        口径='上游Run吞吐量：完成操作数/Run计时。含请求生成、审计、限速等待及客户端Cleanup（审计落盘和数据库关闭）；不含初始装载、装载后等待及独立全键校验。',
        精度='保留日志直接打印的吞吐量；时间字段存在打印舍入，不冒充纳秒精度。',
        边界='这是当前负载下实际完成的YCSB逻辑操作数，不是设备IOPS或饱和能力；Update包含Get再Put。有限速时不能仅凭接近限速值认定机制性能相同。')
