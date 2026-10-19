// 跨阶段校验入口回归；只访问临时普通文件，不打开设备。
#include "rrzc_ycsb_audit.h"
#include <iostream>
int main(int argc,char** argv) {
  try {
    if (argc!=2) return 2;
    const std::string mode=argv[1];
    if (mode=="检查") {
      rrzc_ycsb::Audit audit;
      std::cout<<audit.imported<<' '<<audit.expected.size()<<'\n';
    } else {
      std::vector<ycsbc::DB::Field> value={{"field0",mode=="错误读取" ? "错误" : "内容"}};
      rrzc_ycsb::Record(mode=="插入" ? 'I' : mode=="更新" || mode=="未知更新" ? 'U' : 'R',
          mode=="未知更新" ? "user9" : "user1",value,ycsbc::DB::kOK);
      rrzc_ycsb::Save();
    }
  } catch (const std::exception& error) { std::cerr<<error.what()<<'\n'; return 1; }
}
