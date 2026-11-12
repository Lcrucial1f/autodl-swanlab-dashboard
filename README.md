# AutoDL × SwanLab Dashboard

简体中文 | [English](README_EN.md)

把 AutoDL 机器、GPU 状态和 SwanLab 实验放在同一个看板中。

## 作者的话

AutoDL 的机器租多了，哪台空闲、哪台在跑什么实验，很容易记混。平时又用 SwanLab 记录实验，遇到问题时，还要来回找对应的机器和训练任务。

因此做了这个可视化看板，把租用的 AutoDL 实例和正在运行的 SwanLab 实验对应起来，方便查看资源、跳转实验看进度，以及连接机器排查问题。

## 主要功能

- **分区管理实例**：按内蒙、重庆等区域组织机器，支持添加、检测连接和移除本地记录。
- **GPU 实时总览**：查看在线机器、运行与空闲 GPU 数量、逐卡利用率和剩余显存。
- **关联 SwanLab 实验**：根据训练进程和日志，识别机器上正在运行的项目与实验。
- **一键查看实验**：从实例直接跳转到对应的 SwanLab 实验，查看训练进度。
- **实验名称双向同步**：在看板改名会同步到 SwanLab；云端改名也会自动更新回来。
- **快捷免密连接**：终端输入 `autodl` 选择实例，或用 `autodl 编号` 直接连接。

## 快速开始

支持 **macOS、Linux、Windows WSL**，需要 Python 3.9+、Git 和 OpenSSH；远端实例需有 Python 和 `nvidia-smi`。

```sh
git clone https://github.com/Lcrucial1f/autodl-swanlab-dashboard.git
cd autodl-swanlab-dashboard
python3 install.py
~/.local/bin/autodl setup
```

安装器会创建独立 Python 环境并安装 SwanLab。运行后打开「连接配置」，看板地址为 [http://127.0.0.1:8766](http://127.0.0.1:8766)。

想在终端直接使用 `autodl`，将下面一行加入 `~/.zshrc` 或 `~/.bashrc`，重新打开终端：

```sh
export PATH="$HOME/.local/bin:$PATH"
```

### 添加 AutoDL 实例

1. 在 AutoDL 控制台启动实例，复制 **SSH 登录指令**。
2. 打开「连接配置 → AutoDL 实例」，粘贴指令，填写别名和区域（可选）。
3. 填写实例 SSH 密码，或在「使用已有私钥」中指定已授权的私钥，点击 **添加并连接**。

密码仅用于本次安装公钥，不会保存。密码配置免密需要 `expect`：macOS 用 `brew install expect`，Ubuntu / WSL 用 `sudo apt install expect`。也可运行 `autodl pubkey`，将公钥手动添加到 AutoDL 控制台。

连接失败可在列表点击「检测」，或运行 `autodl key 编号` 重试。新增机器需手动登记；移除记录不会释放云端实例。

### 登录 SwanLab

1. 打开 [SwanLab 个人设置](https://swanlab.cn/settings)，复制自己的 **API Key**。
2. 在「连接配置 → SwanLab 账号」中粘贴 Key，点击 **验证并保存**。
3. 显示「已连接」后回到看板，点击 **打开 SwanLab** 查看实验，或点 **修改名称** 同步改名。

已有 `swanlab login` 登录状态或 `SWANLAB_API_KEY` 时会自动尝试读取，点击「检查连接」即可。未登录也能查看 GPU；实验关联需要远端训练已写入 SwanLab 日志。目前支持 SwanLab 公有云，云端改名约 30 秒同步到看板。

## 常用命令

```sh
autodl           # 选择实例并连接
autodl ui        # 打开看板
autodl setup     # 连接配置
autodl 1         # 连接编号 1
autodl free      # 查看含低占用 GPU 的机器
autodl config    # 查看本地配置路径
autodl ui stop   # 停止本地看板
```

实例与账号配置保存在 `~/.config/autodl/`，SSH 密钥默认放在 `~/.ssh/`；不会随项目上传。手动配置格式见 [实例清单示例](examples/instances.example.json)。
