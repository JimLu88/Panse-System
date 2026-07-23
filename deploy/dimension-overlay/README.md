# 产品最终尺寸文件生产叠加层

ERP 产品总表只显示两个最终交付入口：最终尺寸图和文字说明。编辑工作留在
Synology Drive 源目录；`tools/publish_dimension_finals.py` 仅在内容变化后发布到
群晖 ERP，并在服务端保留带版本的 SVG/JSON 母版。原嵌入式编辑器地址现在会
返回产品总表。

- API 基于当前 `panse-system-api` 生产镜像叠加尺寸模型、接口、迁移与导入器。
- Web 基于当前 `panse-system-web:lan` 生产镜像叠加产品表最终文件按钮。
- 资产保存在既有 `/app/storage/product_dimensions/` 持久化目录。
- `0130` 迁移承接生产现有 `0129`。
