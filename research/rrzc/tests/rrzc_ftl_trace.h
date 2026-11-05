/* 仅用于独立诊断对象；单FTL线程记录实际命令及映射/RC，不改变设备状态。 */
#pragma once

static FILE *rrzc_ftl_file;
static bool rrzc_ftl_initialized;
static uint64_t rrzc_ftl_mapping, rrzc_ftl_state, rrzc_ftl_reads;
static uint64_t rrzc_ftl_records;

static uint64_t rrzc_ftl_mix(uint64_t hash, uint64_t value)
{
    return (hash ^ value) * UINT64_C(1099511628211);
}

static void rrzc_ftl_close(void)
{
    if (rrzc_ftl_file && fclose(rrzc_ftl_file)) {
        fprintf(stderr, "FTL诊断落盘失败\n");
        abort();
    }
    rrzc_ftl_file = NULL;
}

static void rrzc_ftl_cache(struct zns_ssd *zns)
{
    int i;
    uint64_t j;

    for (i = 0; i < zns->cache.num_wc; ++i) {
        rrzc_ftl_state = rrzc_ftl_mix(rrzc_ftl_state,
                                    zns->cache.write_cache[i].sblk);
        rrzc_ftl_state = rrzc_ftl_mix(rrzc_ftl_state,
                                    zns->cache.write_cache[i].used);
        for (j = 0; j < zns->cache.write_cache[i].used; ++j) {
            rrzc_ftl_state = rrzc_ftl_mix(rrzc_ftl_state,
                                        zns->cache.write_cache[i].lpns[j]);
        }
    }
}

static void rrzc_ftl_begin(struct zns_ssd *zns, NvmeRequest *req)
{
    const char *path;

    if (!rrzc_ftl_initialized) {
        rrzc_ftl_initialized = true;
        path = getenv("FEMU_FTL_AUDIT");
        if (path && *path) {
            rrzc_ftl_file = fopen(path, "wbx");
            if (!rrzc_ftl_file ||
                setvbuf(rrzc_ftl_file, NULL, _IOFBF, 262144) ||
                fwrite("RRZCFT01", 1, 8, rrzc_ftl_file) != 8) {
                fprintf(stderr, "FTL诊断文件已存在或无法创建\n");
                abort();
            }
            atexit(rrzc_ftl_close);
        }
    }
    rrzc_ftl_mapping = rrzc_ftl_state = UINT64_C(1469598103934665603);
    rrzc_ftl_reads = 0;
    if (rrzc_ftl_file && req->cmd.opcode == NVME_CMD_WRITE) {
        rrzc_ftl_cache(zns);
    }
}

static void rrzc_ftl_map(uint64_t lpn, uint64_t ppa)
{
    if (rrzc_ftl_file) {
        rrzc_ftl_mapping = rrzc_ftl_mix(rrzc_ftl_mapping, lpn);
        rrzc_ftl_mapping = rrzc_ftl_mix(rrzc_ftl_mapping, ppa);
    }
}

static void rrzc_ftl_read(uint64_t lpn, uint64_t ppa, uint64_t rc)
{
    if (!rrzc_ftl_file) {
        return;
    }
    if (!rrzc_ftl_reads++) {
        /* 严格4KiB命令只有一页，直接保留真实PPA和读取前RC。 */
        rrzc_ftl_mapping = ppa;
        rrzc_ftl_state = rc;
    } else {
        rrzc_ftl_mapping = rrzc_ftl_mix(rrzc_ftl_mapping, lpn);
        rrzc_ftl_mapping = rrzc_ftl_mix(rrzc_ftl_mapping, ppa);
        rrzc_ftl_state = rrzc_ftl_mix(rrzc_ftl_state, rc);
    }
}

static void rrzc_ftl_end(struct zns_ssd *zns, NvmeRequest *req)
{
    uint64_t row[8];
    unsigned i;

    if (!rrzc_ftl_file) {
        return;
    }
    row[0] = req->cmd.opcode;
    row[1] = req->slba;
    row[2] = req->nlb;
    row[3] = le32_to_cpu(req->cmd.cdw13);
    if (req->nr_zone_resets) {
        row[0] = 256;
        row[1] = req->zone_resets[0];
        row[2] = req->nr_zone_resets;
        row[3] = 0;
        for (i = 0; i < req->nr_zone_resets; ++i) {
            rrzc_ftl_mapping = rrzc_ftl_mix(rrzc_ftl_mapping,
                                          req->zone_resets[i]);
        }
    } else if (req->cmd.opcode == NVME_CMD_WRITE) {
        rrzc_ftl_cache(zns);
    }
    row[4] = rrzc_ftl_mapping;
    row[5] = rrzc_ftl_state;
    row[6] = req->status;
    row[7] = le32_to_cpu(req->cqe.n.result);
    for (i = 0; i < 8; ++i) {
        row[i] = cpu_to_le64(row[i]);
    }
    if (fwrite(row, sizeof(row), 1, rrzc_ftl_file) != 1 ||
        ((++rrzc_ftl_records % 4096 == 0 || req->nr_zone_resets) &&
         fflush(rrzc_ftl_file))) {
        fprintf(stderr, "FTL诊断记录失败\n");
        abort();
    }
}
