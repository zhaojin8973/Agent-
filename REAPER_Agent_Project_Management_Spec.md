# REAPER Agent 工程管理规范（无人值守混音系统）

Version: 1.0

Author: Jin Zhao

Date: 2026-06

## 设计目标

本规范不是为了优化 REAPER 的人工操作体验。

本规范的目标是：

> 建立一套适用于 AI Agent、无人值守混音、远程手机控制的 REAPER 工程管理体系。

核心原则：

- Agent 不应该处理弹窗
- Agent 不应该猜测状态
- Agent 不应该依赖鼠标点击
- Agent 应该操作工程状态

## 一、总体设计原则

### Principle 1

禁止 Untitled Project

任何工程在导入素材之前必须完成：

- Project Name
- Project Directory
- Project Save

否则禁止导入素材、录音和渲染。

### Principle 2

一个工程对应一个目录

标准结构：

ProjectName/
├─ ProjectName.rpp
├─ Audio
├─ Renders
├─ Stems
├─ References
├─ Backups
└─ Notes

### Principle 3

所有资源必须工程本地化。

### Principle 4

目标不是处理弹窗，而是让弹窗永远不出现。

## 二、工程生命周期

Created → Saved → Prepared → Imported → Mixed → Rendered → Archived → Finished

Agent 永远执行：验证 → 执行 → 验证 → 下一状态。

## 三、新建工程规范

- 用户输入项目名
- 创建项目目录
- 创建标准子目录
- 保存 RPP
- 设置媒体路径
- 进入 Prepared

命名规范：

Artist_Song_Mix

禁止：

- test
- untitled
- NewProject
- Mix1

## 四、媒体管理规范

所有媒体统一进入：

Audio/

禁止外部路径直接引用。

## 五、自动保存规范

推荐：

- 自动保存：60秒
- 自动备份：Backups/

关闭工程前：

- Save Project
- 验证 Dirty Flag = False
- Close Project

## 六、导入规范

导入前验证：

- 项目已保存

导入后自动执行：

- Normalize Naming
- Track Coloring
- Folder Structure
- Bus Routing

## 七、渲染规范

输出目录：

Renders/

命名规范：

Song_Mix_v01.wav
Song_Mix_v02.wav
Song_Mix_v03.wav

禁止覆盖旧版本。

渲染前检查：

- Render Path
- Disk Space
- Sample Rate
- Bit Depth
- Plugin Status

## 八、插件管理规范

启动检查：

- Plugin Database
- Plugin License
- Plugin Availability

Missing Plugin 不弹窗，进入 Error State。

## 九、Agent 控制规范

优先级：

ReaScript > OSC > Web API > GUI Automation

Agent 应操作工程状态，而非模拟鼠标。

## 十、弹窗消灭清单

### Save Dialog

原因：Project Dirty

解决：Save Before Close

### Overwrite Dialog

原因：文件已存在

解决：自动版本号

### Missing File Dialog

原因：媒体丢失

解决：媒体本地化

### Missing Plugin Dialog

原因：插件缺失

解决：预检查数据库

### License Dialog

原因：授权失效

解决：任务前验证授权

## 十一、无人值守目标

手机 → Agent → REAPER → 自动混音 → 自动导出 → 自动归档

目标：

- 0 人工干预
- 0 鼠标点击
- 0 键盘输入
- 0 阻塞弹窗

## 核心理念

不要设计如何处理弹窗。

要设计为什么会出现弹窗，并在流程层面消灭它。

对于 AI Agent：

稳定性 > 智能程度
