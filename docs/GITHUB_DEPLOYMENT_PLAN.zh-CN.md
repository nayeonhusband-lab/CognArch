# CognArch GitHub 部署计划

更新日期：2026-04-30

## 目标

分成两条线：

1. GitHub 仓库公开源码
2. GitHub Releases 分发可直接运行的 Windows/macOS 包

注意：当前许可证是 `PolyForm Noncommercial 1.0.0`，所以这是
`source-available, noncommercial`，不是 OSI 意义上的开源许可证。

## 一、仓库发布

### 需要进入仓库的内容

- `CognArch/`
- `distribution/`
- `docs/`
- `.github/`
- `README.md`
- `LICENSE`
- `COMMERCIAL.md`
- `AGENTS.md`
- `start.bat`
- `start.ps1`
- `.gitignore`
- `.gitattributes`

### 不进入仓库的内容

- `runtime/`
- `.venv/`
- `dist/`
- 会话、输入、处理后文件、向量库、索引运行态文件
- 私钥、API Key、本机 IDE 垃圾文件

### 建议步骤

1. 创建 GitHub 仓库。
2. 在本地配置 remote。
3. 首次提交当前干净结构。
4. 推送主分支。
5. 检查 GitHub 页面上的 `README`、许可证和目录结构是否正常显示。

## 二、Release 分发

### 发布给普通用户的包

优先发布 runtime 包：

- `CognArch-windows-x64-runtime.zip`
- `CognArch-macos-arm64-runtime.zip`
- `CognArch-macos-x64-runtime.zip`

不要把 GitHub 的源码 ZIP 当作普通用户分发包。

### Windows 本地构建

```powershell
python distribution\build_release.py --target windows --with-runtime --clean-staging --prune-releases
```

### GitHub Actions 自动构建

当前仓库已经有：

- `.github/workflows/release.yml`

它会在打 tag 时自动构建 runtime 包并挂到 GitHub Release。

建议发布流程：

1. 本地完成最终检查。
2. 提交并推送。
3. 打版本 tag，例如 `v0.1.0`。
4. 推送 tag。
5. 等待 GitHub Actions 产出三平台 runtime 包。
6. 在 Release 页面补发布说明。

## 三、发布前检查

### 代码与结构

- `python CognArch/main.py status does-not-exist --json`
- `python distribution/launcher.py --no-browser`
- 根目录 `start.bat` 可双击启动
- `README.md` 与实际启动方式一致

### Windows 包

- 解压后双击 `start.bat`
- 没有系统 Python 时仍能运行
- `ingest --json` 不因 GBK/emoji 崩溃

### macOS 包

- 解压后运行 `run.command` 或 `run.sh`
- 验证首次启动
- 处理系统安全提示与权限问题

## 四、对外文案建议

仓库简介建议写：

`Local knowledge-base application for document-heavy workflows. Source-available under PolyForm Noncommercial 1.0.0.`

避免写成：

- `open source`
- `MIT`
- `Apache`

因为这会和当前许可证冲突。
