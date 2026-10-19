/* 精确保存同一物理块最近30秒的计数事件；不使用Zone最大值差分。 */
#ifndef FEMU_ZNS_RR_HISTORY_H
#define FEMU_ZNS_RR_HISTORY_H

#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>

#define FEMU_RR_SECOND_NS UINT64_C(1000000000)
#define FEMU_RR_HISTORY_NS (30 * FEMU_RR_SECOND_NS)

typedef struct FemuRrHistory {
    uint64_t *events;
    uint32_t head, count, capacity;
    uint64_t last_ns, lost_ns;
    bool has_time, has_loss;
} FemuRrHistory;

static inline uint64_t femu_rr_history_at(const FemuRrHistory *h, uint32_t i)
{
    return h->events[(h->head + i) % h->capacity];
}

static inline void femu_rr_history_clear(FemuRrHistory *h)
{
    /* Reset保留已分配空间，但本代际的事件及丢失状态全部清除。 */
    h->head = h->count = 0;
    h->has_time = h->has_loss = false;
    h->last_ns = h->lost_ns = 0;
}

static inline void femu_rr_history_destroy(FemuRrHistory *h)
{
    free(h->events);
    h->events = NULL;
    h->capacity = 0;
    femu_rr_history_clear(h);
}

static inline void femu_rr_history_prune(FemuRrHistory *h, uint64_t now)
{
    if (now < FEMU_RR_HISTORY_NS) {
        return;
    }
    while (h->count && femu_rr_history_at(h, 0) <= now - FEMU_RR_HISTORY_NS) {
        h->head = (h->head + 1) % h->capacity;
        h->count--;
    }
}

static inline void femu_rr_history_lost(FemuRrHistory *h, uint64_t when)
{
    if (!h->has_loss || when > h->lost_ns) {
        h->lost_ns = when;
    }
    h->has_loss = true;
}

static inline void femu_rr_history_record(FemuRrHistory *h, uint64_t now,
                                         uint32_t limit)
{
    uint32_t i, capacity;
    uint64_t *events;

    if (h->has_time && now < h->last_ns) {
        /* 时钟异常不能产生看似有效的差分；旧时间经过前保持窗口不可用。 */
        uint64_t previous = h->last_ns;
        femu_rr_history_clear(h);
        femu_rr_history_lost(h, previous);
    }
    h->last_ns = now;
    h->has_time = true;
    femu_rr_history_prune(h, now);
    if (!limit) {
        femu_rr_history_lost(h, now);
        return;
    }
    if (h->count == h->capacity && h->capacity < limit) {
        capacity = h->capacity ?
            (h->capacity > limit / 2 ? limit : h->capacity * 2) : 16;
        if (capacity > limit) {
            capacity = limit;
        }
        events = malloc((size_t)capacity * sizeof(*events));
        if (!events) {
            /* 诊断分配失败只使近期窗口无效，不停止IO或丢失累计RC。 */
            femu_rr_history_lost(h, now);
            return;
        }
        for (i = 0; i < h->count; i++) {
            events[i] = femu_rr_history_at(h, i);
        }
        free(h->events);
        h->events = events;
        h->capacity = capacity;
        h->head = 0;
    }
    if (h->count == h->capacity) {
        femu_rr_history_lost(h, femu_rr_history_at(h, 0));
        h->head = (h->head + 1) % h->capacity;
        h->count--;
    }
    h->events[(h->head + h->count) % h->capacity] = now;
    h->count++;
}

static inline uint64_t femu_rr_history_delta(const FemuRrHistory *h,
                                            uint64_t now, uint64_t window)
{
    uint32_t lo = 0, hi = h->count;
    uint32_t first;

    if (now >= window) {
        while (lo < hi) {
            uint32_t mid = lo + (hi - lo) / 2;
            if (femu_rr_history_at(h, mid) <= now - window) {
                lo = mid + 1;
            } else {
                hi = mid;
            }
        }
    }
    first = lo;
    hi = h->count;
    while (lo < hi) {
        uint32_t mid = lo + (hi - lo) / 2;
        if (femu_rr_history_at(h, mid) <= now) {
            lo = mid + 1;
        } else {
            hi = mid;
        }
    }
    return lo - first;
}

static inline bool femu_rr_history_valid(const FemuRrHistory *h, uint64_t now,
                                        uint64_t born, uint64_t window)
{
    return now >= born && now - born >= window &&
           (!h->has_time || now >= h->last_ns) &&
           (!h->has_loss || (now >= h->lost_ns && now - h->lost_ns >= window));
}
#endif
