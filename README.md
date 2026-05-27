# MusicTools - 音乐文件工具箱

全功能音乐文件管理工具集，覆盖标签写入、歌词搜索、AI 语音识别、歌手分隔符统一、FLAC 压缩修复、合辑标记等场景。

## 目录结构

```
musictools/
├── musictools.py              # 主程序（CLI + GUI）
├── musictools_gui.py           # GUI 启动器（双击直接进图形界面）
├── zimu_lrc.py                # 独立 zimu 语音识别 → LRC 生成
├── tools/
│   ├── fix_artists.py         # 歌手分隔符统一工具
│   ├── generate_comments.py   # 网易云评论爬取生成器
│   ├── sync_subsonic_stars.py # Subsonic 全量收藏同步
│   └── daoliyu_subsonic_bridge.py  # 道理鱼 → Subsonic 桥接
└── docs/
    └── 音乐库接入指南.md       # 用户端接入教程
```

## musictools.py — 主程序

### 命令行模式

```bash
# 基本用法
musictools.exe <命令> <目录路径>
python musictools.py <命令> <目录路径>

# 示例
musictools.exe tag "G:\音乐\我的音乐"
musictools.exe lyrics "G:\音乐\我的音乐"
```

#### tag — 从文件名写入标签

按文件名中提取的信息自动填充 FLAC/MP3 的标题、艺术家、专辑、曲目号等标签。

**文件名匹配规则：** 支持 `01.歌名.flac`、`01 - 艺术家 - 歌名.flac`、`01.歌名-艺术家.flac` 等格式。

```bash
musictools.exe tag "G:\音乐\专辑目录"
```

输出示例：
```
[周杰伦] 01.以父之名.flac <- {'TITLE': '以父之名', 'TRACKNUMBER': '1'}
[周杰伦] 02.懦夫.flac     <- {'TITLE': '懦夫', 'TRACKNUMBER': '2'}
```

#### fix — 修复 FLAC PICTURE 封面

修复 FLAC 文件中 PICTURE 元数据块的 MIME 类型缺失或尺寸信息为 0 的问题。封面图片本身不损。

```bash
musictools.exe fix "G:\音乐\有问题的音乐目录"
```

```
[FIXED] 01.歌名.flac
修复: 2 个
```

#### check — 文件完整性检测

扫描目录下所有音频文件的标签、歌词、封面完整性，输出问题列表和控制台报告，并在程序目录下生成完整报告文件。

```bash
musictools.exe check "G:\音乐\我的音乐"
```

输出示例：
```
总计: 172 个文件
缺标题: 0  缺艺术家: 1  缺歌词: 155
 ─ 缺艺术家: 01.无标题.flac
 ─ 缺歌词: 01.Lo.mp3, 02.En.flac ... 等 155 个
完整报告已保存: 检测报告_20260523_193000.txt
```

#### lyrics — 交互式歌词搜索

对目录下的所有歌曲，依次输出搜索结果（网易云 + QQ 音乐），用户可通过交互菜单选择处理方式：

```
选择说明:
  1-6  = 在线搜索结果（序号）
  l    = LRCMaker（需要先启动 LRCMaker 后端）
  z    = zimu 语音识别（需要先启动 zimu-agent）
  s    = 跳过
  q    = 退出
```

```bash
musictools.exe lyrics "G:\音乐\我的音乐"
```

### GUI 标签页详解

```bash
musictools.exe --gui       # 启动图形界面
musictools-gui.exe          # 双击直接进图形界面
```

GUI 采用三标签页布局（批量标签/单曲歌词/批量歌词）+ 三个扩展功能标签页：

#### 标签/修复/检测

选择目录 → 选择功能（tag/fix/check）→ 点击执行。支持批量递归处理。

#### 单曲歌词

1. 选择文件所在目录 → 点击扫描
2. 下拉选择待处理的单文件
3. **在线搜索**：搜索网易云/QQ 音乐，结果列表双击即可下载保存
4. **LRCMaker**：粘贴歌词文本 → 开始对齐
5. **zimu 识别**：一键启动语音识别，自动生成逐字 LRC

#### 批量歌词

全目录扫描，TreeView 表格列出所有歌曲，支持：
- 勾选/全选/全不选
- 每行可单独设置处理方式（自动 / 手动 / zimu / LRCMaker / 跳过）
- 批量设置按钮快速修改选中行
- **自动模式**：自动匹配在线结果（score ≥ 0.5），低分跳过
- **手动模式**：先点「预搜(手动)」→ 逐首弹窗选择搜索结果
- 进度条 + 实时日志
- zimu 识别包含后端健康检测和自动断连恢复

#### FLAC 压缩

针对 192kHz/24bit 的高码率 FLAC 降采样：
- 自动扫描目录下的 192kHz+ FLAC
- 选择目标采样率（96000 / 48000 / 44100 Hz）和位深（16 / 24bit）
- 显示预计压缩大小
- 保留源文件的所有元数据和封面
- 输出文件名 `原文件名_compressed_96000Hz.flac`

#### 高级检测

深度扫描 FLAC 文件的完整性和封面元数据：
- 检测采样率、位深、时长、封面 MIME/尺寸
- 在 4 个时间点（25%/50%/75%/95%）试读，精确定位损坏位置
- **修复 PICTURE**：一键补齐缺失的 MIME 类型和尺寸信息
- **截取修复**：提取损坏点之前的所有有效帧，重建 FLAC
- **批量修复损坏**：全自动处理所有损坏文件
- 输出统计报告

#### 合辑标记

检测同一专辑有多位歌手的合辑，写入统一标签：
- **COMPILATION=1**（FLAC 的 Vorbis Comment 或 MP3 的 ID3 TXXX）
- **ALBUMARTIST**（批量设置为 "Various Artists" 或自定义值）
- 双栏界面：左侧合辑列表 → 右侧文件详情
- 每张合辑显示艺人列表、已标记数/待标记数

### 打包

```bash
# CLI 版（带控制台窗口）
pyinstaller --onefile --console --name musictools musictools.py

# 纯 GUI 版（无控制台，双击即启动图形界面）
pyinstaller --onefile --noconsole --name musictools-gui musictools_gui.py
```

## AI 后端配置

歌词搜索功能依赖两个本地 AI 后端：zimu-agent（语音识别）和 LRCMaker（文本对齐）。以下为完整部署教程。

### zimu-agent — 语音识别后端

**下载与安装：**

```bash
# 1. 从 https://www.supergeti.com/zimu/index.html 下载 Windows 版
#    解压到 zimu-agent-windows-v1.2.1/

# 2. 安装三个模型（从魔搭社区 ModelScope 下载）
pip install modelscope

# 型号 ① FireRedASR2-AED
modelscope download --model xukaituo/FireRedASR2-AED
# 型号 ② Qwen3-ASR-1.7B
modelscope download --model Qwen/Qwen3-ASR-1.7B
# 型号 ③ Qwen3-ForcedAligner-0.6B
modelscope download --model Qwen/Qwen3-ForcedAligner-0.6B
```

下载完成后将三个模型移入 zimu-agent 的 `model/` 目录：

```
zimu-agent-windows-v1.2.1/
└── model/
    ├── FireRedASR2-AED/
    ├── Qwen3-ASR-1.7B/
    └── Qwen3-ForcedAligner-0.6B/
```

**启动：** 按顺序运行目录下的批处理文件：

```
1. 自动安装组件.bat      # 首次运行，安装依赖
3. 启动服务.bat           # 启动 zimu-agent 后端（默认端口 5003）
```

### LRCMaker — 文本对齐后端

从 GitHub 仓库 [Flare-Sky/LRCMaker-AI-Backend](https://github.com/Flare-Sky/LRCMaker-AI-Backend) 下载 Windows 版后直接运行即可。默认启动在端口 8000。

同时支持网页插件：在 [lrc-maker.github.io](https://lrc-maker.github.io/) 页面上可调用本地后端进行歌词对齐。

### 在 musictools 中使用

CLI 模式下选择 `lyrics` → 检测到后端后可选 `z`（zimu）或 `l`（LRCMaker）。GUI 下对应「单曲歌词」和「批量歌词」两个标签页。

## zimu_lrc.py — AI 语音识别

调用本地 zimu-agent 将音频文件识别为逐字 LRC 歌词（支持中日双语）。内部完整复刻网页前端 app.js 的处理流程。

## tools/ 工具集

### fix_artists.py — 歌手分隔符统一

将 `ARTIST` / `ALBUMARTIST` 字段中的 `,` `;` `&` `、` `feat.` 等分隔符统一替换为 `/`。

```bash
python3 fix_artists.py /path/to/music               # 交互模式
python3 fix_artists.py /path/to/music --yes         # 自动模式（cron）
python3 fix_artists.py /path/to/music --backup      # 仅备份
```

### generate_comments.py — 网易云评论生成

扫描本地音乐库 → 调用网易云增强 API 搜索匹配 → 获取热门评论 → 生成 `comments_data.json`。

```bash
python3 generate_comments.py "/path/to/music" comments_data.json --full
```

### sync_subsonic_stars.py — Subsonic 收藏同步

遍历 Subsonic API 所有歌曲 → 逐首 Star，确保 Subsonic 客户端能看到全量曲库。

```bash
python3 sync_subsonic_stars.py https://subsonic.xxx.com user pass --dry-run
```

### daoliyu_subsonic_bridge.py — 道理鱼 Subsonic 桥

直接读取道理鱼的数据库，对外暴露标准 Subsonic REST API。零外部依赖（只用 Python 内置 sqlite3），自动检测表结构/音乐路径。

```bash
python3 daoliyu_subsonic_bridge.py --host 0.0.0.0 --port 4040
```

## 依赖

| 依赖 | 用途 | 必需 |
|------|------|------|
| Python 3.9+ | 所有脚本 | ✅ |
| mutagen | MP3 标签读写 | 可选 |
| PyInstaller | 编译 EXE | 可选 |
| scipy / soundfile / numpy | FLAC 压缩（Tab 4） | 可选 |

## 编译好的 EXE

`dist/` 目录下：
- `musictools.exe` (~58MB) — CLI 版（带控制台）
- `musictools-gui.exe` (~58MB) — 纯 GUI 版（无控制台，双击直接进）
