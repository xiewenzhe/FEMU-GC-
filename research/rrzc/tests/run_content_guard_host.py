#!/usr/bin/env python3
"""独立公共内容隔离修复：保留旧实验，前置回归后才可运行严格健康验收配对。"""
import run_direct_host as host
LABEL='content-guard-20261020'
host.LABEL=LABEL
host.OUTPUT=host.IMAGE_DIR/(LABEL+'-results')
host.BUILD=host.IMAGE_DIR/(LABEL+'-build.qcow2')
host.TARGET=host.DIRECTORY+'/'+LABEL
host.DirectRun.label_prefix=LABEL
if __name__=='__main__':host.main()
