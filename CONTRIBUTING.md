# 参与改进

欢迎补充新的本地计量来源、脱敏格式样本和兼容性修复。

```sh
python3 -m unittest discover -s tests -v
python3 scripts/check-public-files.py
sh scripts/build-macos.sh
```

新增适配器时，请同时写明：日志路径、支持的版本或样本、输入是否已经包含缓存、增量还是累计用量、去重标识，以及不能覆盖的场景。测试应覆盖重复扫描、半行、响应更新、缓存缺失和异常记录。

样本必须手工合成，只保留解析所需字段。不要从自己的完整日志中截取一段后直接提交。可用 `python3 scripts/demo.py` 在隔离数据上预览 UI。

功能修改尽量保持 Python 标准库和原生系统框架，无需引入 Node、数据库服务或前端构建工具。
