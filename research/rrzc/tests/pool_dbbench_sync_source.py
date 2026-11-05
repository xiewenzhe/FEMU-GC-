"""给真实DB独立副本补接既有受控空间交接；选择器、候选与原GC正文保持不变。"""
from controlled_gc_source import synchronous_space
from target_alloc_source import controlled_retry,synchronous_space_retry


def require_ready(fs,zbd):
    if not all(token in text for text,token in (
            (fs,'rrzc_boundary::State().Pending()'),
            (fs,'rrzc_boundary::State().Complete(sequence)'),
            (zbd,'rrzc_boundary::State().Request()'),
            (zbd,'rrzc_boundary::State().Wait(request,boundary_deadline)'))):
        raise ValueError('真实DB协同对象缺少已验收的空间请求/完成/确认；先补接编译再复测，不复用轮询对象发布收益')


def patched(fs,zbd):
    if 'rrzc_boundary::State().Pending()' in fs or '受控空间请求：' in zbd:
        raise ValueError('同步交接已接线或输入版本不明，拒绝重复修改')
    # 复用已经单独验收的两端，不重写请求/完成/确认协议。
    new_fs=synchronous_space(fs)
    new_zbd=synchronous_space_retry(controlled_retry(zbd))
    anchor='    uint64_t non_free = zbd_->GetUsedSpace()'
    if fs.split(anchor,1)[1]!=new_fs.split(anchor,1)[1]:
        raise ValueError('GC触发或选区正文发生变化')
    if 'rrzc_boundary::State().Pending()' not in new_fs or '受控空间请求：' not in new_zbd:
        raise ValueError('空间交接两端不完整')
    require_ready(new_fs,new_zbd)
    return new_fs,new_zbd
