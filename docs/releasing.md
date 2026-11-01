# 发布步骤

当前目录是独立的公开源码候选版。不要在日常运行目录初始化或推送 Git，也不要复制个人配置、日志、数据库和备份。

1. 本机检查：

   ```sh
   python3 -m unittest discover -s tests -v
   python3 scripts/check-public-files.py
   sh scripts/build-macos.sh
   python3 scripts/package-release.py
   ```

2. 检查生成的 `Formula/token-monitor.rb`、源码包及 `dist/SHA256SUMS`，提交源码与配方。`dist` 和 `.build` 不进入 Git。

3. 在 GitHub 创建空的公开仓库 `spiritn/token-monitor`。本地已使用 GitHub noreply 地址，不需要公开私人邮箱。推送 `main` 后，先检查两种 Mac 架构的 CI 结果；通过后再将 README 中对应验证状态更新为实际结果。

4. 推送 `v0.1.0` 标签。Release 工作流检查版本、测试、公开文件和原生编译，再上传源码包、SHA256SUMS 和配方。标签必须与 VERSION 一致。

5. 在另一台 Mac 上完成一键安装、Homebrew 安装、登录启动、升级和卸载验证，之后再把这些安装入口标为正式推荐。首次发布尤其检查远端文件可下载，不要只依赖本地打包成功。

后续发布时修改 VERSION、安装器中的版本号和原生 Info.plist，重新打包生成配方。源码包按 Git 跟踪列表收集，排除 Formula 目录，以便嵌入对应校验值；相同公开文件内容会生成相同的包。

目前尚未执行上传、远端 CI、Release 创建或 Apple Silicon 实机测试。发布前继续保持这一状态说明准确。
