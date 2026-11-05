// 严格RR接线的线程局部请求类型；仅标识真正的迁移读，不全局放行普通读。
#pragma once
namespace rrzc_strict {
inline thread_local bool migration=false;
inline thread_local bool reached=false;
class MigrationScope {
  bool previous_;
 public:
  MigrationScope():previous_(migration) {migration=true;}
  ~MigrationScope() {migration=previous_;}
};
}
