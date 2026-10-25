# 第三方组件

运行依赖固定于 `requirements.txt` 和 `requirements-lock.txt`。项目源码尚未指定自身的开源许可证；下面的第三方组件各自遵循其原有许可。

| 组件 | 版本 | 许可及来源 |
| --- | --- | --- |
| Flask | 3.1.2 | BSD-3-Clause，https://github.com/pallets/flask |
| Waitress | 3.0.2 | ZPL-2.1，https://github.com/Pylons/waitress |
| Python-Markdown | 3.9 | BSD-3-Clause，https://github.com/Python-Markdown/markdown |
| Bleach | 6.2.0 | Apache-2.0，https://github.com/mozilla/bleach |
| Pillow | 13.0.0 | MIT-CMU，https://github.com/python-pillow/Pillow |
| HTMX | 2.0.8 | 0BSD；随附 `workbench/static/HTMX-LICENSE.txt` |
| PDF.js | 4.10.38 | Apache-2.0；随附 `workbench/static/pdfjs/LICENSE` |
| PDF.js CMap／字体 | 随 PDF.js | 保留 `cmaps/LICENSE`、`standard_fonts/LICENSE_FOXIT`、`standard_fonts/LICENSE_LIBERATION` |

间接依赖及其许可证以安装包自身的元数据和许可证为准。所有已随仓库分发的静态组件许可证保持原样，安装依赖会一并取得包内许可证。PDF.js 用于浏览器预览，不将提取结果写入业务正文。图标使用本项目生成的位图，截图仅含虚构测试资料。
