<div align="center">

# 🏆 HSLegendArriver（胜率63.8的炉石传说脚本）

### 帮你走完上传说的路

**读取炉石日志 + 获取炉石盒子打法建议 + 自动执行操作**

**33 分钟：钻石 2 → 传说**  
**一下午：冲到传说约 6000 名**  
**多盘实测胜率：机械骑 82.4%（17 局 14 胜）、号角骑 65%、弃牌术 63.8%**

<br>

> 🔥 已实测支持：**机械骑 / 号角骑 / 弃牌术 / 海盗瞎**  
> 🤖 自动识别对局、读取推荐打法并完成出牌操作

</div>

---

## ✨ 项目简介

**HSLegendArriver（传说到达者）** 是一个用于《炉石传说》的自动化打法执行器。

项目通过读取炉石对局日志，并结合 **炉石盒子「推荐打法」** 提供的推荐操作，自动识别当前对局状态并执行对应的鼠标点击与键盘操作。

简单来说：

> **炉石盒子负责告诉你"怎么打"，HSLegendArriver 负责帮你"打出去"。**

## 📊 实测卡组胜率

| 卡组 | 胜率 | 实测场次 |
|---|---|---|
| 机械骑 | 82.4% | 17 局 14 胜 |
| 号角骑 | 65% | 未统计 |
| 弃牌术 | 63.8% | 多盘实测 |
| 海盗瞎 | 已实测支持 | — |


- **控制浮窗**：更好用的操作逻辑
  
  <img width="286" height="500" alt="image" src="https://github.com/user-attachments/assets/5f0b5e03-0ad4-4b50-8e00-e29ed3c86a1f" />


- **自动投降**：避免控制卡组的折磨，可自主设定投降阈值
  
<img width="1158" height="227" alt="image" src="https://github.com/user-attachments/assets/7cdc69c6-79ed-4240-97c6-95a6e1b22793" />



  <img width="1173" height="267" alt="image" src="https://github.com/user-attachments/assets/de4e12d7-f402-4bbd-ac9f-85c1d215c8f7" />


## 🐍 详细安装（含 pip 与清华镜像）

> 需要 **Python 3.12**（自带 pip）。

### 1. 安装 Python 3.12
- 到 <https://www.python.org/downloads/> 下载 **Python 3.12** 安装包；
- 安装时**务必勾选 "Add python.exe to PATH"**；
- 装完在 PowerShell 运行 `python --version` 验证。

### 2. 用清华 TUNA 镜像安装依赖
在项目根目录打开 PowerShell，运行：
```text
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```
依赖较大（含 PaddleOCR / PaddlePaddle），耐心等待。

### 3. 启动
- **以管理员身份**运行：
```text
python web_ui.py
```
- 浏览器会自动打开 `http://127.0.0.1:8765`（端口被占用会自动换，以控制台打印为准）。

### 4. 修改电脑分辨率为1920 x 1080；缩放 100%；修改电脑分辨率为1920 x 1080；缩放 100%；修改电脑分辨率为1920 x 1080；缩放 100%；（重要的事情说三遍！！！！）
### 5. 打开炉石传说和炉石盒子
### 6. 利用“校准推荐区域”功能对盒子UI的区域进行校正


| 功能 | 说明 |
| --- | --- |
| 👤 基础配置 | 填写用户 ID 与炉石日志目录，失焦自动保存并立即生效 |
| ⚔️ 开始对战 | 一键立即启动自动化对战 |
| ⏰ 定时任务 | 设置开始/结束时间；结束时间到后**先打完本局再自动停止** |
| ⏱️ 延时设置 | 换牌/回合/OCR 各级延时均可在页面调整，写入 `ui_config.json` 即时生效 |
| 🛑 停止控制 | 「本局结束后停止」与「立即停止」；Ctrl+Q 立即停止 |
| 📊 运行概览 | 实时显示对局数、胜场、胜率与当前状态 |
| 📜 实时日志 | 页面内实时查看自动化运行日志 |

> ⚠️ 定时任务期间请保持控制台程序运行。

## 🎯 校准截图区域（适配你本机盒子 UI 大小）

盒子「推荐打法」面板的位置/大小会随**盒子窗口大小、分辨率**不同而变化。首次使用或换了盒子窗口大小后，用画框工具手动校准一次：

1. 网页控制台点「**校准推荐区域**」，或命令行运行 `python calibrate_roi.py`；
2. 屏幕上出现**绿框** = 程序实际截图范围，右下角有**缩放手柄**；
3. 拖右下角手柄调整大小、拖绿框区域整体移动，把盒子面板顶部「**打法参考A**」红头区域框进绿框；
4. 按 **S** 保存（写入 `ui_config.json`，变为蓝框），**Esc** 退出；
5. 保存后重开一局生效。
<img width="1560" height="328" alt="image" src="https://github.com/user-attachments/assets/81f83406-9a09-412c-9572-eabb5075d5c2" />

> 程序固定 **1920×1080、缩放 100%**，如需更大/更小的盒子窗口，重新画框即可。

## 🖥️ 实时日志浮窗（右上角）

自动对局开始时，屏幕**右上角**会出现一个**置顶半透明**小窗，实时滚动显示自动化日志：

- **自己回合开始**那一行用**绿色**高亮，并自动把炉石窗口唤回前台；
- **📊 头部显示实时战绩**：已完成场数、胜场、负场、胜率（真正打完一局才计入）；
- `[推荐]` / `[执行]` → 白，`等待` → 灰；
- 日志**增量追加**，缓冲 **2000 行**，可在底部停留时自动跟随；
- 长日志**自动换行**；底部有**延时进度条 + 文字说明**；
- **💾 保存日志**按钮：写入项目根 `logs/` 子目录；
- 点击浮窗**不抢炉石前台**（`WS_EX_NOACTIVATE`）；按住左键可拖动窗口；
- 随「开始对战」自动弹出，对局结束随 `web_ui` 退出。



## 🃏 已测试卡组

### 号角骑（针对脏牧特攻）

```text
AAEBAaToAgaD3gO2igS8jwbOnAbRqQblwQcMiA740gKR5APJoAThpATBxAXI+AWFjgaZjgb1lQaDwgea/AcAAA==
```

### 机械骑

```text
AAEBAaToAgiftwPM6wP5pAS5/gXHpAaf4Qa/+Qad3QcLpfUCh64DkrUE1L0E2tMEhKUF2dAF4vEGupYH2uIHmvwHAAEE1/4Cnd0H87MGx6QG9rMGx6QG6N4Gx6QGAAA=
```

### 弃牌术

```text
AAEBAa35AwaPggPV0QP5xgXxoQb2oQbGsgcMzge1uQPQ4QOYkgWrkgWVygbXlweEmQekrQfWvgfZvgfPvwcAAA==
```

## 🛠️ 环境要求

| 项目 | 要求 |
| --- | --- |
| 操作系统 | Windows |
| Python | 3.10 – 3.12（推荐 3.12；不支持 3.13+） |
| 炉石传说 | 已安装 |
| 炉石盒子 | 已安装，简体中文 |
| 桌面 / 炉石分辨率 | **1920 × 1080** |
| Windows 缩放 | **100%** |

## ⚠️ 启动前检查

- [ ] 盒子推荐打法位于屏幕左侧
- [ ] 桌面 / 炉石分辨率均为 **1920 × 1080**，Windows 缩放 **100%**
- [ ] 炉石使用全屏窗口 / 全屏显示
- [ ] 炉石盒子使用**简体中文**，左侧「推荐打法」完整显示
- [ ] 炉石 / 炉石盒子窗口未被其他窗口遮挡
- [ ] 使用**管理员权限**启动 Python，`HEARTHSTONE_LOG_ROOT` / `YOUR_NAME` 配置正确

## 🤝 Contributing

如果你在使用过程中遇到问题，或想反馈 Bug / 功能建议，欢迎在 Issue 中提供：问题现象 + 游戏界面截图 + 运行环境。

## ⭐

如果你觉得这个项目有意思或帮到了你，欢迎点一个 **Star ⭐**，这对我真的很重要！

> **如果真的靠它上传说了，回来留个 Star 吧 😎**

---

## 🙏 致谢

本项目参考了以下开源项目：

- [Yiyuan-Dong/AutoHS](https://github.com/Yiyuan-Dong/AutoHS)
- [FallAbyss/AutoHS](https://github.com/FallAbyss/AutoHS)

## ⚠️ Disclaimer

本项目仅用于 **技术研究与代码交流**。本团队声明反对长期滥用脚本的行为。

---

<div align="center">

### 🏆 HSLegendArriver

**让 AI 帮你走完最后一段上传说的路。**

## ⭐ Star 一下吧 ⭐

</div>
