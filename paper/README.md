# 论文工作区

**唯一的论文 Git 工程是 `paper/overleaf/`。以后编辑这里的 `main.tex`。**

Overleaf 项目：https://www.overleaf.com/project/6af173ccdc9b295019b1cb82

## 实际写作文件

- [overleaf/main.tex](overleaf/main.tex)：英文论文正文，包括 Introduction；当前为单栏作者稿。
- [overleaf/references.bib](overleaf/references.bib)：参考文献库。
- [overleaf/elsarticle-num.bst](overleaf/elsarticle-num.bst)：引用排版样式，通常无需修改。
- `overleaf/elsarticle/`、`overleaf/cas-dc.cls`、`overleaf/cas-common.sty`：网页原有模板资料，本次为保持与网页完全一致而保留；当前主文件使用 `elsarticle`，不使用 CAS 类。
- `overleaf/.git/`：这个论文项目的 Git 元数据，不是 FEMU 主仓库的 Git 元数据，不要手动删除。

## 其他目录

```text
paper/
├── README.md
├── .gitignore
├── overleaf/       # 唯一论文工程，与 Overleaf 网页根目录对应
├── planning/       # 研究论证、写作笔记；历史记录供参考
├── reading/        # 文献与精读笔记
└── archive/        # 清理前的一份备份
```

已移除重复路径 `overleaf-remote/`，以及旧的 `exports/`、`templates/`、`tools/`。
没有保留多份活动论文工程。研究代码和实验数据未修改。
请关闭编辑器中旧的 `paper/overleaf-remote/main.tex` 标签页，重新打开 `paper/overleaf/main.tex`；避免把内容再次保存到旧路径。

## 本次同步状态（2026年11月10日）

- 已重新认证并执行远端 fetch；本地 `main` 与远端 `origin/main` 均为 `9e1f033ab19a836ca0de3237dc98d54f68f40ef5`。
- 已核对全部 29 个项目文件，逐字节与本次读取的远端版本一致；论文 Git 工作区干净。
- 本次只下载和恢复本地内容，没有 commit 或 push，没有修改网页文件。
- 刚才重写但未上传的 Introduction 不在当前活动工程中；已保存在清理前备份里。当前 `main.tex` 是本次读取的网页版本。
- 已在临时目录用 Tectonic 0.15.0 编译通过，共 6 页，5 条文献解析成功；仍有少量原有排版警告。没有向论文目录写入编译中间文件。尚未直接检查网页 PDF 的编译日志。
- 网页编辑后，本地不会自动跟随更新。

备份为 [archive/before-overleaf-cleanup-20261110-180256.tar.gz](archive/before-overleaf-cleanup-20261110-180256.tar.gz)。
它保存清理前的两个工程、未上传改动、旧导出包及旧说明；内部 `BACKUP-MANIFEST.json` 的 SHA-256 已逐文件核验。
原论文 Git 元数据已随工程移动到 `overleaf/.git/`。

## 后续使用

在 FEMU 根目录查看论文修改：

```bash
git -C paper/overleaf status
git -C paper/overleaf diff
```

需要更新网页现稿到本地时，先检查本地修改，再执行 `git -C paper/overleaf pull --ff-only`；
有未提交修改时先核对和保留，不能直接覆盖。Token 只在终端认证时输入，不写入论文或聊天。

本地编辑不会自动上传网页。上传是另一次明确的 Git 提交和推送操作。
父仓库通过 `paper/.gitignore` 排除此独立论文仓库；不要在 FEMU 根目录添加 Overleaf 远端。
