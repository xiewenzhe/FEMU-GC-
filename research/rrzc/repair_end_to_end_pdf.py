"""将已核验PNG嵌入汇总PDF，规避部分预览器的中文子集字体兼容问题。"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil

os.environ.setdefault('MPLCONFIGDIR', '/tmp/rrzc-figure-cache')
import matplotlib
matplotlib.use('Agg')
from matplotlib import pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
import numpy as np
from PIL import Image


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def export_pdf(pngs, temporary):
    with PdfPages(temporary) as pdf:
        for png in pngs:
            # 保留原始整数像素，避免浮点透明度转换造成颜色舍入。
            pixels = np.asarray(Image.open(png).convert('RGB'))
            h, w = pixels.shape[:2]
            fig = plt.figure(figsize=(w / 300, h / 300), dpi=300)
            ax = fig.add_axes([0, 0, 1, 1])
            ax.imshow(pixels, interpolation='none', aspect='auto')
            ax.set_axis_off()
            pdf.savefig(fig, dpi=300)
            plt.close(fig)


def repair(root):
    # 原PDF保留一次；再次执行时拒绝覆盖备份，防止丢失修复前的证据。
    backup = root / '字体兼容修复'
    backup.mkdir()
    records = []
    names = ('总体结果', '应用长尾延迟', '维护与空间', 'API响应延迟')
    for group in ('原始数值', '归一化'):
        target = root / group / '全部柱状图.pdf'
        original = backup / f'{group}-原向量版.pdf'
        shutil.copy2(target, original)
        pngs = [target.parent / (n + '.png') for n in names]
        temporary = backup / (group + '-待替换.pdf')
        export_pdf(pngs, temporary)
        # 先完整写完临时文件，再替换用户正在查看的汇总PDF。
        temporary.replace(target)
        records.append(dict(文件=str(target), 修复前SHA256=digest(original),
            修复后SHA256=digest(target), 原向量备份=str(original),
            页面PNG_SHA256={p.name: digest(p) for p in pngs}))
    report = dict(原因='原PDF中文字体已嵌入且本地渲染正常；用户预览乱码，具体预览器根因尚未确认。',
        处理='汇总PDF各页嵌入已有300dpi PNG，消除中文字体依赖；保留修复前向量PDF及原SVG。',
        数据变化=False, 限制='兼容版文字不可选中编辑；编辑使用原SVG或向量备份。',
        源码SHA256=digest(Path(__file__)), 文件=records)
    (backup / '修复记录.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('root', type=Path)
    repair(parser.parse_args().root)
