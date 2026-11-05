/* 真实纯计数核边界测试，不把合成读取称作设备实验。 */
#include <assert.h>
#include <stdio.h>
#include "rr-strict.h"
int main(void)
{
    uint64_t a = 1023, b = 8;
    assert(femu_rr_read_action(a, 1024, false) == FEMU_RR_TRIGGER);
    ++a;
    assert(femu_rr_read_action(a, 1024, false) == FEMU_RR_BLOCK);
    assert(femu_rr_read_action(a, 1024, true) == FEMU_RR_SELF_REFRESH);
    assert(femu_rr_read_action(b, 1024, true) == FEMU_RR_COUNT);
    ++b;
    assert(b == 9 && a == 1024);
    assert(femu_rr_read_action(1023, 1024, true) == FEMU_RR_TRIGGER);
    assert(femu_rr_read_action(0, 1, false) == FEMU_RR_TRIGGER);
    assert(femu_rr_read_action(UINT64_MAX, 0, false) == FEMU_RR_COUNT);
    puts("严格RR计数边界通过：自身豁免、其他块计数、普通读取拒绝、搬迁触发及Reset外部管理");
    return 0;
}
