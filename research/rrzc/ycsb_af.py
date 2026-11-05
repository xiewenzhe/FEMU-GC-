"""本轮32GiB A至F的固定负载、二进制请求核验及原始指标解析；不调整机制。"""
from collections import Counter
import hashlib
from pathlib import Path
import re
import struct

def require(ok, why):
    if not ok:
        raise ValueError(why)

WORKLOADS = {
    'a': ('读写各半', {'readproportion': '0.5', 'updateproportion': '0.5'}, 'zipfian'),
    'b': ('读多写少', {'readproportion': '0.95', 'updateproportion': '0.05'}, 'zipfian'),
    'c': ('纯读', {'readproportion': '1'}, 'zipfian'),
    'd': ('读取最新', {'readproportion': '0.95', 'insertproportion': '0.05'}, 'latest'),
    'e': ('短范围扫描', {'scanproportion': '0.95', 'insertproportion': '0.05'}, 'zipfian'),
    'f': ('读与读改写', {'readproportion': '0.5', 'readmodifywriteproportion': '0.5'}, 'zipfian'),
}
RATIOS = ('readproportion', 'updateproportion', 'insertproportion',
          'scanproportion', 'readmodifywriteproportion')
LABELS = {'space': '空间对照', 'model': '当前RR-aware'}
RECORDS = 6291456  # 单字段原始值24GiB；实际SST占用另行记录，不宣称等于24GiB。
OPERATIONS = 1200000


def properties(text):
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        key, value = line.split('=', 1)
        require(key not in out, '配置重复：' + key)
        out[key] = value
    return out


def make_properties(base, upstream, letter, records, operations, *, posix=None):
    require(letter in WORKLOADS and records > 0 and operations > 0, '负载或规模非法')
    source = properties(upstream)
    _, ratios, distribution = WORKLOADS[letter]
    require(all(float(source.get(k, '0')) == float(ratios.get(k, '0')) for k in RATIOS),
            '原始A至F操作比例与登记定义不符')
    require(source.get('requestdistribution') == distribution, '原始选键分布不符')
    out = {k: v for k, v in properties(base).items()
           if not k.startswith('rrzc.') and k not in RATIOS}
    # 保留上游每种负载的选键、插入顺序和扫描分布；设备/字段大小显式适配。
    out.update(source)
    out['insertorder'] = source.get('insertorder', 'hashed')
    out.update({k: ratios.get(k, '0') for k in RATIOS})
    out.update(recordcount=str(records), operationcount=str(operations), fieldcount='1',
               fieldlength='4096', readallfields='true', writeallfields='true',
               measurementtype='hdrhistogram', status='true', zeropadding='20',
               sleepafterload='0' if posix else '30', **{
                   'status.interval': '10', 'limit.ops': '0', 'rrzc.independent_keys': 'false',
                   'rocksdb.disable_wal': 'true', 'rocksdb.no_block_cache': 'true',
                   'rocksdb.fs_uri': '' if posix else 'zenfs://dev:nvme0n1',
                   'rocksdb.dbname': posix or '/rrzc-ycsb'})
    out.pop('limit.file', None)
    return '\n'.join(k + '=' + str(v) for k, v in sorted(out.items())) + '\n'


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def check_trace(path, records, operations, letter):
    """T记录逻辑操作，随后的R/U/I/S记录真实API；F不能误算成两次业务。"""
    mapping = {0: 'I', 1: 'R', 2: 'U', 3: 'S', 4: 'RU'}
    allowed = {'a': {1, 2}, 'b': {1, 2}, 'c': {1}, 'd': {0, 1}, 'e': {0, 3}, 'f': {1, 4}}[letter]
    loaded = 0
    business = False
    pending = ''
    last_key = None
    logical, calls = Counter(), Counter()
    with Path(path).open('rb', buffering=1 << 20) as stream:
        def event():
            code = stream.read(1)
            if not code:
                return None
            size = stream.read(4)
            require(len(size) == 4, '请求长度截断')
            count, = struct.unpack('<I', size)
            require(count <= 128, '请求键长度非法')
            key, value = stream.read(count), stream.read(8)
            require(len(key) == count and len(value) == 8, '请求载荷截断')
            return code.decode('ascii'), key, struct.unpack('<Q', value)[0]
        while (row := event()) is not None:
            op, key, value = row
            if op == 'P':
                require(not business and not key and value == records and loaded == records,
                        '装载边界或键数不符')
                business = True
            elif op == 'T':
                require(business and not pending and not key and value in allowed,
                        '逻辑请求边界或类型非法')
                logical[value] += 1
                pending, last_key = mapping[value], None
            elif not business:
                require(op == 'I' and key, '装载阶段出现非插入请求')
                loaded += 1
            else:
                require(pending and op == pending[0] and key, 'API请求与逻辑操作不一致')
                if last_key is not None:
                    require(key == last_key, '读改写不是同一个键')
                if op == 'S':
                    require(1 <= value <= 100, '扫描长度非法')
                if op == 'R':
                    require(value == 0, '读请求载荷非法')
                calls[op] += 1
                last_key, pending = key, pending[1:]
    require(business and not pending and loaded == records and sum(logical.values()) == operations,
            '请求总数或最后请求不完整')
    return dict(装载=loaded, 业务操作=operations, 读取=calls['R'], 更新=calls['U'],
                插入=calls['I'], 扫描=calls['S'], 读改写=logical[4],
                逻辑类型={str(k): v for k, v in sorted(logical.items())},
                最终键数=records + calls['I'])


def latencies(text, trace):
    rows = [s for s in text.splitlines() if ' sec: ' in s and 'operations;' in s]
    require(rows, '缺少YCSB最终直方图')
    expected = {k: trace[v] for k, v in [('READ', '读取'), ('UPDATE', '更新'),
                ('INSERT', '插入'), ('SCAN', '扫描'), ('READMODIFYWRITE', '读改写')] if trace[v]}
    result = {}
    for op, body in re.findall(r'\[([^:]+):([^\]]+)\]', rows[-1]):
        require(op not in result and op in expected, '失败、重复或未知API：' + op)
        values = dict(re.findall(r'(?:^|\s)(Count|Avg|99|99\.9|99\.99)=([\d.]+)(?=\s|$)', body))
        require(set(values) == {'Count', 'Avg', '99', '99.9', '99.99'} and
                int(values['Count']) == expected[op], 'API直方图字段或次数不符：' + op)
        result[op] = dict(次数=int(values['Count']), 平均微秒=float(values['Avg']),
                          **{'P' + q + '微秒': float(values[q]) for q in ('99', '99.9', '99.99')})
    require(set(result) == set(expected), '缺少已执行操作的延迟')
    return result


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('base', type=Path)
    p.add_argument('upstream', type=Path)
    p.add_argument('letter', choices=WORKLOADS)
    p.add_argument('records', type=int)
    p.add_argument('operations', type=int)
    p.add_argument('--posix')
    a = p.parse_args()
    print(make_properties(a.base.read_text(), a.upstream.read_text(), a.letter,
                          a.records, a.operations, posix=a.posix), end='')
