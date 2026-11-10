"""Checks for joint device fixtures; source selection is independently trace-audited."""


def checks(method, variant, case):
    assert (method, case) in (('coldmap', 'joint'), ('caza', 'joint'), ('oaza_gc', 'recovery'))
    common = '''        def rows(name):
            with (target / name).open() as stream: return list(csv.DictReader(stream))
        def logical(name):
            result = {}
            for row in rows(name):
                key = (row['sst'], row['level'])
                result[key] = result.get(key, 0) + int(row['length'])
            return result
        assert logical('device-mapping.csv') == logical('verify-mapping.csv')
        assert '协同池取得目标：' not in log
        verify_log = (target / 'zenfs-verify.log').read_text()
        assert 'ZC 迁移失败：' not in log + verify_log
'''
    if method == 'coldmap':
        specific = r'''        assert 'ColdMap deferred:' not in log + verify_log
        assert '简单双因素实际集合：' not in log
        frames = re.findall(r'ColdMap frame: snapshot=(\d+) compactions=(\d+) depth=(\d+) simulated=(\d+) candidates=(\d+)', log)
        assert any(int(row[2]) > 0 and int(row[3]) > 0 and int(row[4]) >= 2 for row in frames)
        assert 'ColdMap selected: zone=' in log
        assert int((target / 'joint-gc-reset.txt').read_text()) > 0
        details = dict(frames=frames, multiple_candidates_and_simulation_same_frame=True)
'''
    elif method == 'oaza_gc':
        specific = r'''        assert 'OAZA-GC frame:' not in log, 'preparation unexpectedly cleaned before remount'
        assert '简单双因素实际集合：' not in log + verify_log
        assert 'OAZA-GC deferred:' not in verify_log
        frames = re.findall(r'OAZA-GC frame: invalid=(\d+) allocated=(\d+) zones=(\d+) requested=(\d+) selected=(\d+) variant=(\S+)', verify_log)
        assert frames and any(int(row[4]) > 0 for row in frames)
        for invalid, allocated, zones, requested, selected, actual_variant in frames:
            assert int(requested) == int(invalid) * int(zones) * 3 // (int(allocated) * 10)
            assert int(selected) <= int(requested) and actual_variant == VARIANT
        recovered = {int(row['zone']): row for row in rows('oaza-recovered-zones.csv')}
        candidates = re.findall(r'OAZA-GC candidate: zone=(\d+).*?label=(\d+) cost=([0-9.]+)', verify_log)
        assert candidates
        assert any(recovered[int(zone)]['native_label'] == '0' and
                   int(recovered[int(zone)]['file_label']) == int(label) and int(label) > 1
                   for zone, label, cost in candidates)
        assert all(int(row['keys']) > 0 for row in rows('oaza-recovered-keys.csv'))
        assert int((target / 'oaza-gc-reset.txt').read_text()) > 0
        details = dict(frames=frames, unset_zone_labels_recovered_from_files=True,
                       persisted_key_counts_restored=True, gc_after_remount=True)
'''.replace('VARIANT', repr(variant))
    else:
        specific = '''        assert 'CAZA target:' in log
        assert 'ColdMap selected:' not in log + verify_log and 'OAZA-GC frame:' not in log + verify_log
        assert 'ZC 统计：' in log and 'RR 统计：' in log
        state = rows('joint-rr-state.csv'); assert len(state) == 2
        before, after = state
        assert before['mapped'] != after['mapped'] and after['source_rc'] == '0'
        assert int(after['host_generation']) == int(before['host_generation']) + 1
        assert int(after['device_generation']) == int(before['device_generation']) + 1
        assert int(after['threshold_events']) == int(before['threshold_events']) + 1
        assert int((target / 'joint-gc-reset.txt').read_text()) > 0
        details = dict(gc_then_natural_rr=True, rr_before=before, rr_after=after)
'''
    receipt = '''        assert hashlib.sha256(binary.read_bytes()).hexdigest() == proof['binary_sha256']
        (target / 'device_complete.json').write_text(json.dumps(dict(
            passed=True, method=METHOD, variant=VARIANT, case=CASE,
            latest_values_verified=True, checksums_verified=True, remounted=True,
            native_live_mapping_verified=True, gc_enabled=True, joint_details=details,
            performance_experiment=False), indent=2) + '\\n')
'''.replace('METHOD', repr(method)).replace('VARIANT', repr(variant)).replace('CASE', repr(case))
    return common + specific + receipt
