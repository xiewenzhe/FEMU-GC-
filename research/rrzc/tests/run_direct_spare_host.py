#!/usr/bin/env python3
"""漏算独占替补的独立修复轮次；旧失败、镜像及结果全部保留。"""
import run_direct_host as host

LABEL='direct-spare-v4-20261020'
host.LABEL=LABEL
host.OUTPUT=host.IMAGE_DIR/(LABEL+'-results')
host.BUILD=host.IMAGE_DIR/(LABEL+'-build.qcow2')
host.TARGET=host.DIRECTORY+'/'+LABEL
host.DirectRun.label_prefix=LABEL

if __name__=='__main__':host.main()
