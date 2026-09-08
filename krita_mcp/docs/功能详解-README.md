# Krita 绘画 MCP — 功能详解

> 独立文档(与根目录 README.md 分开), 面向使用与二次开发。项目位置: H:\cu-a\krita_mcp · 插件版本 1.0.0 · 基于 CC0 授权的 Krita×MCP 原型二次开发

---

## 1. 这是什么

把 Krita 的 Python(PyKrita/libkis)API 包装成 Model Context Protocol(MCP)工具, 让 LLM/远端客户端用普通工具调用, 直接在 Krita 里创建画布、绘制、读取画面、修改图层。

### 架构

1) MCP 客户端(DSH web / 任意客户端)
2) mcp_server.py  外部进程, 纯 Python 标准库, 无第三方依赖
3) loopback HTTP + token 鉴权(默认 127.0.0.1:9797)
4) Krita 内部插件 pykrita/krita_mcp
   - extension.py    插件生命周期 / Tools 菜单开关
   - httpserver.py   基于 QTimer 驱动的 HTTP 桥, 只绑 127.0.0.1 并校验 token
   - mainthread.py   跨线程安全: 把请求回投 Krita GUI 线程执行
   - imaging.py      像素管道: node <-> QImage 双向, 处理 BGRA/字节序
   - ops.py          全部操作实现与分发(38 个 op)

### 关键设计点
- 绘制是直接写像素(读图层区域 → QPainter/QImage 操作 → setPixelData 写回), 走不经过 Krita 笔刷引擎的快速通道, 不进 undo 历史(需在可弃图层上画)。
- 像素读写假设为 RGBA / U8 位深(与 QImage ARGB32 字节序一致, selftest 会校验通道顺序)。
- 坐标全部是文档像素坐标, 以文档左上为原点。

---

## 2. 快速开始

bash 下:
  1) powershell -ExecutionPolicy Bypass -File install.ps1   # 先关 Krita
  2) 启动 Krita(插件桥随之在 9797 端口监听)
  3) python mcp_server.py --selftest                        # 期望 9/9 passed, 38 operations

接入 DSH web: 在 H:\dsh-home\profiles\web\cordis.patch.yml 已注册 mcp-krita 条目, 工具以 mcp__krita__<工具名> 暴露(如 mcp__krita__draw_stroke)。

---

## 3. 工具全集(38 个)

坐标均为画布像素; document 可用索引/文件名/省略(取活动文档); layer 可用图层名/路径/#idx/uuid:/省略(取活动层)。

### 3.1 画布: 创建与读取
| 工具 | 作用 | 关键参数 |
|---|---|---|
| create_canvas | 创建新空画布并打开 | width,height 必填; color_model(默认RGBA) color_depth(默认U8) name background(填充色) resolution_dpi |
| get_canvas_content | 把画布渲染成 PNG 返回给模型看 | max_size(最长边默认1024) layer region |
| get_pixel | 取某像素精确颜色 | x, y |
| create_document / open_document / save_document / export_document / close_document | 文档生命周期(保存/导出PNG/JPEG等) | 见各自 schema |

### 3.2 图层
| 工具 | 作用 |
|---|---|
| list_layers | 列出图层树(docker 序) |
| create_layer | 新建图层(铅笔/组/填充/滤镜等) |
| set_layer | 改图层属性: 名称/不透明度/混合模式/可见/锁定, select 设为活动层 |
| select_layer | 切换活动图层 |
| delete_layer / duplicate_layer / move_layer / merge_layer_down | 图层增删移动合并 |
| inspect_document | 文档与图层树+选区详情 |
| list_capabilities | 列出滤镜/混合模式 |

### 3.3 画笔与颜料
| 工具 | 作用 | 关键参数 |
|---|---|---|
| set_brush | 切换画笔 | pattern(solid/dashed/dotted) color width size flow(力度0~1) opacity(不透明度0~1) preset(Krita笔刷预设) layer(切到目标图层) |
| list_brush_presets | 列出可用笔刷预设名 | |
| draw | 综合绘制命令数组 | commands: rect/fill_rect/ellipse/circle/line/polyline/polygon/text/gradient/image/clear |

### 3.4 基础绘制
| 工具 | 坐标约定 | 参数 |
|---|---|---|
| draw_point | 单 [x,y] 或数组 | points(必填) size(直径) color |
| draw_line | 线段 | x1,y1,x2,y2(必填) color width |
| draw_rect | 矩形 | x,y + w/width,h/height; color描边 fill填充 stroke_width |
| draw_ellipse | 椭圆(内切于框) | 同上 |
| draw_polygon | 多边形 | points(必填) fill color width close |

### 3.5 曲线与压感
| 工具 | 作用 | 参数 |
|---|---|---|
| draw_smooth_path | 多点平滑连线(Catmull-Rom) | points(>=2必填) width color samples |
| draw_stroke | 压感笔触 | points(>=2) pressures(与点等长0~1,可省) base_width min_width color oversample |

### 3.6 特殊工具: 擦除 / 变形 / 液化 / 批量
| 工具 | 作用 | 参数 |
|---|---|---|
| erase | 橡皮擦: 把图层区域填成所选颜色(默认白, 非透明) | x,y,w/width,h/height color |
| liquify | 变形画笔(几何扭曲) | stroke=[[cx,cy,radius,strength],...]; strength>1膨胀,<1收缩 |
| smudge | 液化涂抹(区域像素向局部均值混合模糊) | stroke=[[cx,cy,radius,strength],...]; strength 0~1混合强度 |
| run_paint_actions | 批量执行多个工具调用 | actions=[{tool, arguments},...] 逐个执行并汇总 |

### 3.7 高层/调试
apply_filter(Krita 滤镜), set_selection(选区), transform_image(整体缩放/旋转/裁剪/摊平), trigger_action(触发菜单动作), run_python(在 Krita 内跑脚本), self_test(端到端自检), status(Krita 版本与文档概览)。

---

## 4. 曲线绘制是怎样实现的

### 4.1 draw_smooth_path —— 平滑连线

输入是一串控制点 [[x,y],...]。让线光滑地穿过这些点, 分三步:

1) 样条插值: 用 Catmull-Rom 三次样条代替直线。对每 4 个相邻控制点 (P0,P1,P2,P3), 中间段 P1→P2 写成
   P(t) = 0.5 * [ (2·P1) + (-P0+P2)·t + (2·P0 - 5·P1 + 4·P2 - P3)·t^2 + (-P0 + 3·P1 - 3·P2 + P3)·t^3 ]
   关键性质: 曲线在 t=0 正好过 P1, t=1 正好过 P2 → 逐段拼接就是一条每个控制点都经过的光滑曲线(不像贝塞尔那样只是被控制点拉拽)。
2) 细分采样: 对每段把 t 分成 samples(默认16)份, 算出 samples 个中间点, 连成折线路径 QPainterPath。
3) 描边写像素: 用当前 _brush_pen(color,width)(带纹理/flow) 的 painter.drawPath(path) 一次描出, 再写回图层。

### 4.2 draw_stroke —— 压感笔触

压力体现为线宽 + 不透明度随压力变化:

1) 每个点 P_i 配压力 p_i(默认省略时生成起笔轻→中段重→收笔轻的曲线, 0.2→1.0→0.2)。
2) 对相邻两点, 压力线性插值, 每段:
   - 线宽 w = min_width + (base_width - min_width) * p
   - 不透明度 alpha = 0.30 + 0.70 * p
3) 沿折线按 oversample 采样, 每个采样点画一个实心圆 painter.drawEllipse(点, w/2, w/2), 圆与圆重叠 → 一笔粗细/浓淡渐变的笔触。
4) Krita 6.0 兼容: 若检测到 node.paintLine(6.0 新增), 直接走真实画笔 + 真实压力 node.paintLine(q1,q2,p1,p2); 否则回退到 QPainter 模拟(5.2/5.3 只有这套)。

### 4.3 你想要的 三维点: [x, y, 压力]

现在接口把压力拆成独立参数 pressures(与点等长数组), 数据上是 3 个一维数组。你希望每个点本身就是三维 [x,y,p], 更好用更好维护。

思路: 在参数解析处做维度解包, 兼容两种输入:

   伪代码:
   def resolve_vectors(raw):
       for 每个点 it in raw:
           if len(it) >= 3:    # 三维: x, y, 压力
               x, y = it[0], it[1]
               p = clamp(0..1, it[2])
               pressures.append(p)
           else:               # 二维: x, y, 压力默认 1.0(或由独立 pressures 补充)
               x, y = it[0], it[1]
               pressures.append(1.0)
           pts.append((x, y))
       return pts, pressures

这样 draw_stroke / draw_smooth_path 都能直接吃 [[x,y,p],...]; 三维点里的压力还可以在 Catmull-Rom 里作为第四维一起插值(x/y/p 三通道各自做样条), 得到压力也平滑过渡的曲线。这是较小改动, 我可以直接加上。

---

## 5. 文件结构

  mcp_server.py               外部 MCP server(纯标准库)
  install.ps1 / install.sh    装插件 + 写 kritarc 启用
  README.md                   概览(原始文档 + 本项目说明)
  docs/ 功能详解-README.md    本文档
  docs/ PLAN.md               实施计划
  pykrita/krita_mcp/          Krita 内插件(extension/httpserver/mainthread/imaging/ops)
  pykrita/krita_mcp.desktop   插件注册描述

## 6. 故障排查

- selftest 报 UNAVAILABLE → Krita 没运行、或插件没启用(Krita 重写 kritarc 会丢 [python] 段, 需确保 [python] 下 enable_krita_mcp=true)。
- 画出来没有笔刷纹理/压力 → 5.2/5.3 走 QPainter 模拟, preset/flow 只影响 GUI 真实画笔, 不影响像素绘制(用 pattern/color/width/opacity 控制)。
- 改了 ops.py 不生效 → 复制到 %APPDATA%/krita/pykrita/krita_mcp/ 并重启 Krita。
- 坐标不在画布上 → 检查文档是 RGBA/U8, get_image 会按 max_size 缩放但坐标仍是原始分辨率。

## 7. 二次开发点子
- 三维点压力(见 4.3)已论证可行, draw_stroke / draw_smooth_path 升级为 [x,y,p]。
- 把 draw_smooth_path 与 draw_stroke 合并: 平滑曲线 + 逐点压力 → 带压力的平滑笔触一个工具。
- 增加渐变/图案填充、选区约束绘制、把 liquify/smudge 扩展成沿任意路径变形。
