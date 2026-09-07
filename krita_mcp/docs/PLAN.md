# Krita 绘画 MCP — 实施计划

> 目标:用 Krita 的 Python(PyKrita)API 做绘画 MCP,让 LLM 通过 MCP 工具直接操作 Krita 画布。
> 已获用户确认:借鉴成熟架构(外部 MCP server + Krita 内 HTTP 桥接插件),放在独立目录 H:\cu-a\krita_mcp,Krita 由用户自行安装。

## 架构总览

LLM / MCP 客户端(dsh web / 其他)
   |  stdio JSON-RPC 2.0
   V
mcp_server.py (外部独立进程, 纯 Python 标准库, 无第3方依赖)
   |  loopback HTTP (127.0.0.1:8787)
   V
Krita 内部插件 pykrita/krita_mcp (由 Krita 启动时加载)
  - Extension 入口 (extension.py)    插件生命周期/开关
  - Bridge HTTP server (bridge.py)  QTimer驱动监听本地端口
  - ops.py                           操作分发: 所有画布/绘图/画笔操作
  - imaging.py                       像素读写辅助

- 插件用 QTimer 轮询驱动 HTTP 服务,不阻塞 Krita GUI 线程。
- 所有操作在 Krita GUI 线程执行(mainthread 编排),保证线程安全。
- MCP server 只用标准库,无 pip 依赖。
- 坐标/颜色/图层约定:文档宽高像素坐标,#RRGGBB/颜色名,图层自上而下 docker 序。

## 阶段一:基础功能(MVP)

| MCP 工具 | 说明 | 关键 Krita API |
|---|---|---|
| status | 检查 Krita 连接/版本/打开文档 | Krita.instance(), documents() |
| create_canvas | 创建画布 | Krita.instance().createDocument(w,h,name,model,bitdepth,profile,dpi) |
| get_canvas_content | 读画布内容,渲染 PNG 返回 LLM | QImage 读取 + mergeImage 导出 PNG, base64 |
| get_pixel / get_pixel_block | 精确取色 / 快返回选区 | node.pixelData() |
| draw_point | 画点(实心圆) | QPainter drawEllipse 或写像素 |
| draw_line | 画直线 | QPainter drawLine |
| draw_rect / draw_ellipse / draw_polygon | 简单几何图形 | QPainter drawRect/drawEllipse/drawPolygon |
| set_brush | 切换画笔纹理(preset) | Krita.instance().resources('brush') 取预设, activeBrush() |

基础功能实现顺序(每步可独立测试):
1. 项目骨架 + install 脚本 + 插件占位(能加载)
2. bridge HTTP + 基础 status/create_canvas
3. imaging 像素读写 + get_canvas_content 渲染 PNG
4. draw_point / draw_line
5. draw_rect / draw_ellipse / draw_polygon
6. set_brush 切换预设
7. MCP server 联通 + 自测脚本

## 阶段二:高级功能

| MCP 工具 | 说明 | 实现要点 |
|---|---|---|
| draw_smooth_path | 根据点平滑连线 | 对输入点做 Catmull-Rom/贝塞尔拟合 → QPainterPath → 描边 |
| draw_stroke | 模拟压感笔触 | 每点带压力 pressure(0~1),按压力动态控制笔刷/宽度/不透明度 |
| (可选)draw_text | 文字 | QPainter drawText + 字体 |
| (可选)export_image | 导出 PNG/JPEG | mergeImage + save |

## 测试与验证
- selftest:启动后 python mcp_server.py --selftest 验证插件连通与各操作。
- 每个工具做像素回读验证。
- 用户手动在 Krita 中观察效果。

## 交付物
- H:\cu-a\krita_mcp\:完整代码。
- install.ps1:一键装插件到 Krita 并启用。
- git 提交:Krita 未装也能先提交代码;插件需等 Krita 装了才能联调。
