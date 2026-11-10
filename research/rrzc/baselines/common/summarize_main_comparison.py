"""Export descriptive metrics only from the verified, user-selected main arms."""
import csv
from pathlib import Path

from verify_main_comparison import WORK, EVIDENCE, PLAN, read, read_plan, digest, atomic_json
from summarize_comparison import zone_metrics, layout_metrics

OUTPUT = WORK / '主对照五臂结果'


def summarize():
    assert not OUTPUT.exists(), 'refuse overwrite'
    proof = read(EVIDENCE); plan = read_plan()
    assert proof['passed'] and proof['selected_main_full_input_verified']
    assert proof['selection'] == plan and proof['selection_sha256'] == digest(PLAN)
    assert list(proof['reports']) == plan['main_arms']
    assert not proof['literal_failure_reference']['passed']
    failure = Path(proof['literal_failure_reference']['path'])
    assert digest(failure) == proof['literal_failure_reference']['sha256']
    assert not proof['formal_joint_model_comparison'] and not proof['statistical_performance_claim']
    metrics = []; stages = []; latency = []; space = []; layouts = {}
    for arm, report in proof['reports'].items():
        archive = proof['archives'][arm]
        assert digest(Path(archive['path'])) == archive['sha256'], 'archive changed after verification'
        parent = Path(archive['result_directory']); folder = parent / read(parent / '状态.json')['标签']
        by_phase = {}
        for phase, data in report['phases'].items():
            clock = read(folder / (phase + '-clock.json'))
            row = dict(arm=arm, phase=phase, gc_bytes=data['gc_copied_bytes'], rr_bytes=data['rr_copied_bytes'],
                total_copy_bytes=data['gc_copied_bytes'] + data['rr_copied_bytes'],
                gc_rounds=len(data['gc_rounds']), rr_rounds=len(data['rr_rounds']),
                gc_source_empty_at_return=sum(x[2] for x in data['gc_rounds']),
                rr_source_empty_at_return=sum(x[1] for x in data['rr_rounds']),
                phase_trace_reset_events=report['trace_receipts'][phase]['各类事件'].get('Reset', 0),
                process_seconds=(clock['end_ns'] - clock['begin_ns']) / 1e9)
            stages.append(row); by_phase[phase] = row
            zones = zone_metrics(read(folder / (phase + '-end.json')))
            space.append(dict(arm=arm, phase=phase, **{k: v for k, v in zones.items() if k != 'boundary'}))
        metrics.append(dict(arm=arm, source_revision=archive['source_revision'], binary_sha256=archive['binary_sha256'],
            requests=report['requests']['count'], business_gc_bytes=by_phase['run']['gc_bytes'],
            business_rr_bytes=by_phase['run']['rr_bytes'], business_copy_bytes=by_phase['run']['total_copy_bytes'],
            verification_copy_bytes=sum(by_phase[p]['total_copy_bytes'] for p in ('verify', 'reopen')),
            all_phase_copy_bytes=sum(row['total_copy_bytes'] for row in by_phase.values()),
            application_ops_per_second=report['application']['应用完成吞吐ops'],
            business_process_seconds=by_phase['run']['process_seconds']))
        latency.extend(dict(arm=arm, **row) for row in report['application']['指标'])
        layouts[arm] = {phase: layout_metrics(folder, phase) for phase in ('load', 'run', 'verify')}
    OUTPUT.mkdir()
    for name, rows in [('维护与吞吐.csv', metrics), ('逐阶段维护.csv', stages),
                       ('应用端到端延迟.csv', latency), ('阶段末端设备空间.csv', space)]:
        with (OUTPUT / name).open('x', newline='') as stream:
            writer = csv.DictWriter(stream, list(rows[0])); writer.writeheader(); writer.writerows(rows)
    atomic_json(OUTPUT / '汇总证据.json', dict(passed=True, verification=str(EVIDENCE),
        verification_sha256=digest(EVIDENCE), selection=plan, metrics=metrics,
        ending_layout_observations=layouts, literal_failure_reference=proof['literal_failure_reference'],
        no_statistical_significance_claim=True, all_methods_complete=False, boundaries=proof['boundaries']))
    lines = ['# 主对照五臂：单轮完整输入集成结果', '',
        '用户已确定 OAZA adapted 作主对照，literal 保留为歧义与失败分析。'
        'ColdMap printed 与 artifact-survival 继续分列，本次未选择其中一版代表原论文。', '',
        '本表复用已归档且重新独立核验的成功臂，没有重跑实验。每臂 12 窗口、2260992 请求；'
        '各自完成 load/run/verify/reopen，逐请求计划、共同输入及逻辑摘要核对通过。', '',
        '|臂|源码版本|业务 GC MiB|业务 RR MiB|验证/重开复制 MiB|全阶段复制 MiB|应用完成 ops/s|',
        '|---|---|---:|---:|---:|---:|---:|']
    for row in metrics:
        lines.append('|%s|%s|%.3f|%.3f|%.3f|%.3f|%.3f|' % (row['arm'], row['source_revision'],
            row['business_gc_bytes'] / (1 << 20), row['business_rr_bytes'] / (1 << 20),
            row['verification_copy_bytes'] / (1 << 20), row['all_phase_copy_bytes'] / (1 << 20),
            row['application_ops_per_second']))
    lines += ['', 'OAZA 为字节加权的 adapted 成本，明确区别于字面公式；v9 另包含恢复快照提前发布和分源迁移执行适配。'
        'ColdMap v6 包含恢复启动适配。程序哈希及原 tar 路径保存在核验凭据和 CSV 中。', '',
        'G2 是 G2 放置配公共有效量 Greedy GC，没有启用完整 Credit/Score 受害区模型。'
        '本表属于相关工作接线和完整输入集成验收，不能替代最终联合机制的正式论文对照。', '',
        '每臂仅一次完整输入，运行日期和资源交错可能不同；单轮吞吐及延迟为实际观测，不作统计显著性或普遍优劣结论。'
        'OAZA 保持 15% GC 启动，其余 20%；逐请求逻辑相同，但装载后的物理布局和 RC 可不同。', '',
        '业务、验证扫描、重开成本分列，后两者不从总账删除。空间 CSV 使用真实 WP/Finish 占用及可写尾部，'
        '不等于逻辑有效字节。逐阶段表中的 source_empty_at_return 是返回时空源区数；'
        'phase_trace_reset_events 是该阶段全部 Reset 事件，未进一步归因为 GC 或 RR。', '',
        'literal 的原失败完整保留：v9 最后成功批次为 408，业务 -6，未完成 verify/reopen；'
        '它没有进入五臂通过名单，也没有被重写为成功。详见 ../OAZA字面版v9完整复验与失败分析.md。', '',
        'ColdMap 本组完整负载的非平凡模拟压实步数为零，复杂预测与实际 GC 的联合证据仍缺。'
        '逻辑 CRC32C 和校验和也不等于逐字节最新版本证明；没有跨层原子 NAND 检查点。'
        'all_methods_complete 仍为 false；本次未改写论文性能结果。']
    (OUTPUT / '结果与边界.md').write_text('\n'.join(lines) + '\n')
    print(OUTPUT, flush=True)


if __name__ == '__main__': summarize()
