// 受控客户端只读旁观者：保存既有C2查询结果，不修改RC、不发起NAND数据读。
#pragma once
#include "rrzc_recent_sampler.h"
#include <fstream>
#include <memory>
#include <stdexcept>

class JointReadTrace {
 public:
  using Query=std::function<rrzc_v1::RecentReport()>;
  JointReadTrace(const char* enabled,const std::string& directory,Query query,
      std::chrono::nanoseconds period=std::chrono::milliseconds(500)) : directory_(directory) {
    if (!enabled || std::strcmp(enabled,"1")) return;
    if (period.count()<=0) throw std::runtime_error("块轨迹采样周期无效");
    output_.open(directory_+"/block-trace.csv");
    if (!output_) throw std::runtime_error("块轨迹文件不能创建");
    output_<<"批次,查询开始纳秒,查询结束纳秒,查询墙钟纳秒,设备会话高位,设备会话低位,RR阈值,区数,Zone,设备代际,块编号,通道,LUN,plane,block,设备快照纳秒,代际起点纳秒,RC,十秒真实增量,三十秒真实增量,窗口标志\n";
    sampler_=std::make_unique<rrzc_v1::RecentSampler>([this,query](rrzc_v1::RecentReport* unused) {
      (void)unused;
      try {
        const auto begin=Now();const auto report=query();const auto end=Now();const auto wall=WallNow();
        if (end<begin || end-begin>2*rrzc_v1::kSecond || report.zones.empty())
          throw std::runtime_error("块轨迹查询失败、过慢或区集合为空");
        for (const auto& b:report.zones)
          output_<<batches_<<','<<begin<<','<<end<<','<<wall<<','<<report.session_hi<<','<<report.session_lo<<','
              <<report.threshold<<','<<report.zones.size()<<','<<b.zone<<','<<b.generation<<','
              <<b.block_id<<','<<b.ch<<','<<b.lun<<','<<b.plane<<','<<b.block<<','<<b.snapshot_ns<<','
              <<b.epoch_start_ns<<','<<b.rc<<','<<b.delta10<<','<<b.delta30<<','<<b.flags<<'\n';
        output_.flush();
        if (!output_) throw std::runtime_error("块轨迹写入失败");
        ++batches_;return false;
      } catch (const std::exception& error) { error_=error.what();return false; }
      catch (...) { error_="块轨迹查询发生未知错误";return false; }
      // 返回false只是不保存额外内存历史；本旁观者的数据在CSV，不参与任何选择。
    },period);
    sampler_->Start();
  }
  JointReadTrace(const JointReadTrace&)=delete;
  JointReadTrace& operator=(const JointReadTrace&)=delete;
  bool Enabled() const { return bool(sampler_); }
  static uint64_t WallNow() {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(
        std::chrono::system_clock::now().time_since_epoch()).count();
  }
  ~JointReadTrace() { try { Close(); } catch (...) {} }
  void Close() {
    if (!sampler_) return;
    sampler_->Stop();sampler_.reset();output_.close();
    if (!output_ && error_.empty()) error_="块轨迹关闭失败";
    if (!batches_ && error_.empty()) error_="块轨迹没有完整批次";
    std::ofstream status(directory_+"/block-trace.status");
    status<<"完整="<<error_.empty()<<"\n批次="<<batches_<<"\n原因="<<error_<<'\n';
    status.close();
    if (!status) throw std::runtime_error("块轨迹收尾状态写入失败");
    if (!error_.empty()) throw std::runtime_error(error_);
  }
 private:
  static uint64_t Now() {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count();
  }
  std::string directory_,error_;
  std::ofstream output_;
  std::unique_ptr<rrzc_v1::RecentSampler> sampler_;
  uint64_t batches_=0;
};
