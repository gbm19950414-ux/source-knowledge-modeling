# Source Knowledge

把来源文本、解释、审阅与派生视图区分保存的本地知识建模工具。Source-faithful document and knowledge modeling, with explicit review and replayable artifacts.

这是 0.9.9 开发代码的可追溯公开导出；开发仍在原项目维护，公开版本由相同来源重新导出，不独立维护第二套代码。公开范围包括 Python CLI、JSON schemas、知识／语义／组织模型与虚构社区花园案例。没有公开任何书籍正文、私人工作区或真实评审记录。

## 安装与最小例子

需要 Python 3.11 或更新。

```sh
python3 -m venv .venv
.venv/bin/python -m pip install '.[dev]'
.venv/bin/skm --help
.venv/bin/skm validate contracts/knowledge-foundation/v1/examples/community-garden.v1.json --root .
.venv/bin/skm query contracts/knowledge-foundation/v1/examples/community-garden.v1.json --root . --term 浇水
.venv/bin/python examples/reading_demo.py
.venv/bin/python -m pytest tests
```

打开 demo-output/garden.html 查看来源阅读视图；示例沿用测试里的虚构语义审阅，仅演示格式，不产生真实人工批准。更多命令以 skm --help 和各子命令 --help 为准。contracts 中的样例只展示格式；语义草案不自动代表通过人工评审。PDF 摄入需额外安装并指定 MinerU 或已有的经核对解析缓存；本例不需要网络服务或付费 API。

## 边界与许可

记录完整性和 schema 校验不能证明解释符合来源，必须保留来源定位与真正的审阅证据。工具不能凭借自动化检查制造人工批准。

项目尚未指定覆盖全部原创代码的复用许可，公开上传不等于授予开源复用许可。内嵌 humanizer 资源保留其独立 MIT 许可、作者信息和 provenance（src/source_knowledge_modeling/resources/humanizer/）。该许可不扩展到其余代码。PUBLIC_EXPORT.json 保存来源提交及逐文件哈希；不包含私人机器路径。
