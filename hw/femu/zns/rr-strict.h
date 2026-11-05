/* 严格RR触发计数：仅已触发块自身的刷新读取豁免，其他搬迁读取照常计数。 */
#ifndef FEMU_ZNS_RR_STRICT_H
#define FEMU_ZNS_RR_STRICT_H
#include <stdbool.h>
#include <stdint.h>

enum FemuRrReadAction {
    FEMU_RR_COUNT = 0,
    FEMU_RR_TRIGGER = 1,
    FEMU_RR_SELF_REFRESH = 2,
    FEMU_RR_BLOCK = 3,
};

/* 不截断、不清零计数；Reset仍由原擦除路径负责。FTL单线程串行调用。 */
static inline enum FemuRrReadAction femu_rr_read_action(uint64_t rc,
                                                      uint64_t threshold,
                                                      bool migration)
{
    if (!threshold) {
        return FEMU_RR_COUNT;
    }
    if (rc >= threshold) {
        return migration ? FEMU_RR_SELF_REFRESH : FEMU_RR_BLOCK;
    }
    return rc == threshold - 1 ? FEMU_RR_TRIGGER : FEMU_RR_COUNT;
}
#endif
