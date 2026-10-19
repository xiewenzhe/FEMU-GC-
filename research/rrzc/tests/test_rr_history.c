/* 使用实际历史内核验证时间窗口；合成事件不是设备实验结果。 */
#include <assert.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static int fail_allocation;
static void *history_malloc(size_t n)
{
    if (fail_allocation) {
        fail_allocation = 0;
        return NULL;
    }
    return malloc(n);
}
#define malloc history_malloc
#include "../../../hw/femu/zns/rr-history.h"
#undef malloc
static unsigned checks;
#define CHECK(x) do { checks++; assert(x); } while (0)
#define SEC(n) ((uint64_t)(n) * FEMU_RR_SECOND_NS)

int main(void)
{
    FemuRrHistory h = {0}, other = {0};
    uint64_t t;
    CHECK(!femu_rr_history_valid(&h, SEC(9), 0, SEC(10)));
    CHECK(femu_rr_history_valid(&h, SEC(10), 0, SEC(10)));
    CHECK(femu_rr_history_delta(&h, SEC(30), SEC(30)) == 0);
    femu_rr_history_record(&h, 0, 64);
    femu_rr_history_record(&h, SEC(10), 64);
    femu_rr_history_record(&h, SEC(20), 64);
    femu_rr_history_record(&h, SEC(30), 64);
    CHECK(femu_rr_history_delta(&h, SEC(30), SEC(10)) == 1);
    CHECK(femu_rr_history_delta(&h, SEC(30), SEC(30)) == 3);
    CHECK(femu_rr_history_delta(&h, SEC(30) - 1, SEC(10)) == 1);
    CHECK(femu_rr_history_delta(&h, SEC(30) + 1, SEC(10)) == 1);
    femu_rr_history_clear(&h);
    for (t = 1; t <= 100; t++) {
        femu_rr_history_record(&h, SEC(t), 64);
        CHECK(femu_rr_history_delta(&h, SEC(t), SEC(10)) == (t < 10 ? t : 10));
        CHECK(femu_rr_history_delta(&h, SEC(t), SEC(30)) == (t < 30 ? t : 30));
    }
    CHECK(h.capacity <= 64 && h.count == 30);
    femu_rr_history_clear(&h);
    CHECK(h.count == 0 && !h.has_loss);
    femu_rr_history_record(&h, SEC(102), 64);
    CHECK(femu_rr_history_delta(&h, SEC(105), SEC(30)) == 1);
    CHECK(!femu_rr_history_valid(&h, SEC(105), SEC(100), SEC(10)));
    CHECK(femu_rr_history_valid(&h, SEC(130), SEC(100), SEC(30)));
    femu_rr_history_destroy(&h);

    /* 上限溢出只使覆盖丢失事件的窗口无效，不伪造精确性。 */
    femu_rr_history_record(&h, SEC(1), 2);
    femu_rr_history_record(&h, SEC(2), 2);
    femu_rr_history_record(&h, SEC(3), 2);
    CHECK(h.count == 2 && h.has_loss && h.lost_ns == SEC(1));
    CHECK(!femu_rr_history_valid(&h, SEC(10), 0, SEC(10)));
    CHECK(femu_rr_history_valid(&h, SEC(11), 0, SEC(10)));
    CHECK(!femu_rr_history_valid(&h, SEC(30), 0, SEC(30)));
    CHECK(femu_rr_history_valid(&h, SEC(31), 0, SEC(30)));
    femu_rr_history_destroy(&h);
    fail_allocation = 1;
    femu_rr_history_record(&h, SEC(40), 64);
    CHECK(h.count == 0 && h.has_loss);
    CHECK(!femu_rr_history_valid(&h, SEC(41), 0, SEC(10)));
    femu_rr_history_record(&h, SEC(42), 64);
    CHECK(femu_rr_history_delta(&h, SEC(43), SEC(10)) == 1);
    CHECK(femu_rr_history_valid(&h, SEC(70), 0, SEC(30)));
    femu_rr_history_clear(&h);
    femu_rr_history_record(&h, SEC(80), 64);
    femu_rr_history_record(&h, SEC(79), 64);
    CHECK(!femu_rr_history_valid(&h, SEC(89), 0, SEC(10)));
    CHECK(femu_rr_history_valid(&h, SEC(90), 0, SEC(10)));
    femu_rr_history_destroy(&h);

    /* 最大块身份切换时仍查询新最大块自身的完整历史。 */
    for (t = 1; t <= 10; t++) {
        femu_rr_history_record(&h, SEC(t), 64);
    }
    for (t = 21; t <= 31; t++) {
        femu_rr_history_record(&other, SEC(t), 64);
    }
    CHECK(femu_rr_history_delta(&h, SEC(31), SEC(10)) == 0);
    CHECK(femu_rr_history_delta(&other, SEC(31), SEC(10)) == 10);
    CHECK(femu_rr_history_delta(&other, SEC(31), SEC(30)) == 11);
    femu_rr_history_clear(&other);
    CHECK(femu_rr_history_delta(&other, SEC(32), SEC(30)) == 0);
    CHECK(!femu_rr_history_valid(&other, SEC(32), SEC(31), SEC(30)));
    femu_rr_history_destroy(&h);
    femu_rr_history_destroy(&other);
    printf("逐块历史内核通过：%u项合成断言\n", checks);
    return 0;
}
