# AutoDL 实例看板

在本地按区域管理 AutoDL 实例，查看 GPU 使用情况，以及正在运行的 SwanLab 项目和实验。

- 输入 `autodl` 打开终端菜单，选择实例免密连接。
- 输入 `autodl ui` 打开看板，查看在线机器、逐卡利用率和剩余显存。
- 自定义区域名称、排序和实例归属，支持深浅主题。
- 一键打开 SwanLab 实验，在看板改名后同步到 SwanLab；云端改名也会自动更新回来。

本工具通过 SSH 连接**本地已登记的实例**，不需要填写 AutoDL 网站账号密码，也不会自动枚举账号下的新实例。SwanLab 登录是可选项，未登录仍可查看 GPU 状态。

## 1. 安装并打开配置页面

支持 **macOS、Linux、Windows WSL**；Windows 用户请在 WSL 内执行以下命令。运行电脑需要 Python 3.9+、Git 和 OpenSSH 客户端；远端实例需要 Python 和 `nvidia-smi`。

### 获取项目

```sh
git clone https://github.com/Lcrucial1f/autodl-dashboard.git
cd autodl-dashboard
```

仓库为 private 时，需要有访问权限的 GitHub 账号才能克隆或下载；公开后可直接获取。也可以在 GitHub 点击 **Code → Download ZIP**，解压后进入项目目录。

### 安装和启动

```sh
python3 install.py
~/.local/bin/autodl setup
```

安装器会在项目的 `.venv` 中安装 SwanLab SDK，并生成 `~/.local/bin/autodl`。第二条命令直接打开「连接配置」，默认看板地址为 [http://127.0.0.1:8766](http://127.0.0.1:8766)。服务仅监听本机。

想直接输入 `autodl`，在当前终端执行：

```sh
export PATH="$HOME/.local/bin:$PATH"
autodl setup
```

如需对新终端生效，将这一行 PATH 设置加入自己的 `~/.zshrc` 或 `~/.bashrc`，再打开新终端。安装器不会修改 shell 配置。

首次使用 SSH 密码自动安装公钥时，还需要 `expect`：

```sh
# macOS（已安装 Homebrew）
brew install expect

# Debian / Ubuntu / Ubuntu WSL
sudo apt install expect
```

已有可用 SSH 私钥时不需要 expect。只使用 SSH/GPU 功能，或已经安装 SwanLab SDK，也可用 `python3 install.py --skip-deps` 跳过依赖安装。

## 2. 添加 AutoDL 实例

在「连接配置 → AutoDL 实例」中操作；也可以从看板右上角重新打开「连接配置」。

1. 在 AutoDL 控制台启动实例，复制它的 **SSH 登录指令**。
2. 将完整指令粘贴到「SSH 命令」，只粘贴命令，不要把密码拼在后面。
3. 可选填写「快捷别名」和「所属区域」。别名使用英文字母、数字、短横线或下划线；区域可填写「内蒙区」「重庆区」等名称，留空时按连接地址识别。
4. 按下表选择一种免密配置方式，然后点击 **添加并连接**。

| 连接方式 | 操作 |
| --- | --- |
| 首次连接，使用实例密码 | 在「SSH 密码」填写该实例的 SSH 密码。工具会生成本机密钥、安装公钥并验证免密连接；密码只用于本次操作，不会保存。 |
| 使用已有私钥 | 展开「使用已有私钥」，填写本机私钥路径，例如 `~/.ssh/id_ed25519`。该私钥需已获实例授权，SSH 密码可以留空。 |
| 手动在 AutoDL 添加公钥 | 先在终端运行 `autodl pubkey`，将输出的公钥添加到 AutoDL 控制台密钥设置，再回到页面添加实例，密码留空。 |

添加成功后，关闭配置窗口即可查看 GPU 状态。连接失败时，实例仍保留在「已登记实例」中，可以点击 **检测**，或运行 `autodl key 编号` 重试免密配置。公钥设置方式参见 [AutoDL 官方 SSH 文档](https://www.autodl.com/docs/ssh/)。

日常使用：

- 新建或重新租用实例后，将新的 SSH 指令添加到看板。
- 点击「分区设置」调整区域排序、显示名称和实例归属。
- 已释放的实例可在「已登记实例」中点击「移除」并确认。该操作只移除本地记录，不释放云端机器、不停止训练、不删除远端文件。
- 在终端运行 `autodl 1` 可直接连接编号 1，也可运行 `autodl` 后选择实例。

## 3. 登录 SwanLab

完成默认安装后，在「连接配置 → SwanLab 账号」中操作。

### 通过页面登录

1. 打开 [SwanLab 个人设置](https://swanlab.cn/settings)，登录自己的账号并复制 API Key。
2. 回到看板，将 Key 粘贴到「SwanLab API Key」。
3. 点击 **验证并保存**，等待显示 **已连接 · 你的用户名**。
4. 点击「完成」回到看板，已识别的实验会开始同步云端名称。

只有验证成功才会保存 Key。它存放在本工具的独立本地凭据文件中，权限为 `0600`，不会写进项目或改动系统已有登录。

### 已在终端登录过

若本机已有 `swanlab login` 登录状态，或设置了 `SWANLAB_API_KEY` 环境变量，看板会尝试读取；点击 **检查连接** 即可验证，通常不必再次输入 Key。

也可以在项目目录使用安装器创建的环境登录，再回到页面检查连接：

```sh
.venv/bin/swanlab login
```

若 SwanLab 安装在其他 Python 环境，展开 **Python 环境设置**，填写该环境的 Python 可执行文件路径，再点「验证并保存」。留空表示自动检测。高级环境变量需要在启动看板前设置；修改后停止并重新启动本地看板。

**清除本工具保存的凭据** 仅删除本工具保存的 Key。如果系统已有登录，会恢复使用系统账号，不会退出其他工具的账号。官方登录说明见 [SwanLab 文档](https://docs.swanlab.cn/api/cli-swanlab-login.html)。

### 查看和修改实验

- GPU 任务下方显示匹配到的项目和实验名称，点击紫色 **打开 SwanLab** 按钮进入对应实验。
- 点击 **修改名称 → 保存并同步**，可修改 SwanLab 云端名称。保存前会核对当前名称，保存后重新读取确认；失败会保留输入。
- 在 SwanLab 网站改名后，看板约 30 秒自动同步，也可以点击「立即刷新」。读取失败时保留上次成功的名称并标记状态。

当前支持 SwanLab 公有云 `https://swanlab.cn`。账号需能访问对应实验，修改名称还需要写权限。远端训练需要已经使用 SwanLab 记录日志；仅登录本地账号不会为没有日志的任务创建实验。

## 常用命令

```sh
autodl                    # 分区菜单，选择实例连接
autodl ui                 # 打开实时看板
autodl setup              # 直接打开连接配置
autodl 1                  # 连接编号 1
autodl last               # 连接上次使用的实例
autodl add                # 交互式添加实例并配置免密
autodl key 1              # 重试编号 1 的免密配置
autodl list               # 刷新实例状态
autodl gpu                # 查看每张 GPU 详情
autodl free               # 查看含低占用 GPU 的实例
autodl remove 1           # 仅移除本地记录
autodl config             # 查看配置路径
autodl ui stop            # 停止本地看板，云端训练继续运行
```

未加入 PATH 时，将命令中的 `autodl` 替换为 `~/.local/bin/autodl`。不安装命令入口也能在项目目录运行 `python3 -B autodl.py ui`。

## 配置文件与独立环境

配置在当前电脑上自动读取，运行 `autodl config` 或打开「连接配置 → 本地配置与手动接入」可查看实际路径。

| 默认文件 | 用途 |
| --- | --- |
| `~/.config/autodl/instances.json` | 实例编号、SSH 地址、端口、用户、快捷名、区域和可选私钥路径 |
| `~/.config/autodl/dashboard-settings.json` | 区域顺序、显示名和实例归属 |
| `~/.config/autodl/swanlab-settings.json` | 可选的 SwanLab Python 路径 |
| `~/.config/autodl/swanlab-credentials.json` | 本工具保存的 SwanLab API Key |
| `~/.ssh/id_autodl_ed25519` 与 `.pub` | 默认 SSH 私钥和公钥 |
| `~/.ssh/autodl.conf` | 生成的 SSH 别名配置 |

也可手动编辑实例清单，格式见 [实例配置示例](examples/instances.example.json)。示例主机为占位地址，使用前需替换；`next_id` 必须大于所有已有编号，编号、别名和 SSH 地址不能重复。可以添加 `identity_file` 字段指定私钥，清单不接受密码字段。实例列表会自动重读；SSH 别名配置在通过工具增删或重命名实例时生成。

使用独立配置目录和端口时，先在当前终端设置：

```sh
export AUTODL_CONFIG_DIR="$HOME/.config/autodl-work"
export AUTODL_PORT=8767
autodl setup
# 后续在同一环境中运行 autodl add、autodl ui stop 等命令
```

自定义配置目录的密钥、known_hosts 和 SSH 别名配置放在该目录的 `ssh/` 下，与默认配置隔离。

SwanLab Python 选择顺序：`AUTODL_SWANLAB_PYTHON` → 页面保存的路径 → 项目 `.venv` → 当前 Python → PATH 中可发现的 SwanLab/Python 环境。自动检测时选择安装了 SDK 的环境；未找到时页面提示安装。凭据优先级为本工具保存的 Key → 环境变量 → 系统登录。

## 常见问题

| 情况 | 处理方式 |
| --- | --- |
| `autodl: command not found` | 使用完整路径 `~/.local/bin/autodl setup`，或按安装步骤设置 PATH。 |
| 克隆仓库提示不存在或无权限 | private 阶段使用有访问权限的 GitHub 账号；也可由仓库所有者提供分享 ZIP。 |
| 安装时提示目标已有其他 `autodl` | 安装器不会覆盖未知入口。用 `python3 install.py --bin-dir "$HOME/.local/autodl-bin"` 安装到其他目录，再使用该目录下的完整命令路径。 |
| Linux 创建 `.venv` 失败，提示缺少 ensurepip | 为所用 Python 安装对应的 venv 支持；Debian/Ubuntu 常见命令为 `sudo apt install python3-venv`，然后重新运行安装器。 |
| SSH 需要配置密钥或密码安装失败 | 确认实例已启动、SSH 地址和密码正确；安装 expect 后运行 `autodl key 编号`，或手动添加公钥。 |
| SSH 提示主机指纹变更 | 先核实该地址是否已分配给新的实例，再处理本机 known_hosts 对应记录。工具不会自动忽略指纹变化。 |
| 未找到 SwanLab SDK | 在项目目录重新运行 `python3 install.py`，或在「Python 环境设置」指定正确解释器，然后点「检查连接」。 |
| SwanLab 登录失败或改名无权限 | 确认使用公有云账号及有效 Key，并检查该账号是否有对应实验的访问/写入权限。 |
| GPU 在运行，但实验未识别 | 检查训练是否写入 SwanLab 日志，日志与进程是否可读取。登录不会补出缺失的训练日志；模糊匹配时工具会显示未识别。 |
| 看板无法启动或端口被占用 | 检查配置目录中的 `dashboard.log`；可设置另一个 `AUTODL_PORT` 后启动。 |

GPU 监控每轮 SSH 并发采样后间隔 6 秒查询。有计算进程、显存使用至少 512 MiB 或利用率至少 5% 时计为运行/占用；只有查询成功且均低于阈值时计为空闲。过期、离线样本不计入可用资源；瞬时空闲不代表资源已预留，SSH 也无法判断实例是否已释放。

实验关联通过 GPU PID、进程树和 SwanLab 日志的 PID、主机、目录、启动时间共同匹配，已验证 0.10.x 日志格式。不会递归扫描数据集或检查点目录；信息不足或同时匹配多个实验时显示未识别。

## 更新与分享

在项目目录更新代码后，重新安装并启动：

```sh
~/.local/bin/autodl ui stop
git pull --ff-only
python3 install.py
~/.local/bin/autodl ui
```

使用自定义命令目录时替换上述入口路径；实例和账号配置独立保存在本机，不会因普通更新被覆盖。移动项目后也需重新运行安装器更新入口。

生成可直接分发的源码 ZIP：

```sh
python3 package.py
```

输出 `dist/autodl-dashboard.zip`，只包含运行代码、安装脚本、说明和配置示例，不包含本机实例清单、API Key、SSH 密钥、日志、测试截图或 `.venv`。对方解压后从本文安装步骤开始，填写自己的连接信息。

## 开发验证

```sh
python3 -B -m unittest discover -s tests -v
node --check static/app.js
```

可选的前端交互测试使用 Playwright，三个 `tests/browser_*.cjs` 均使用模拟 API，不修改云端账号或实验：

```sh
npm install --no-save --package-lock=false playwright
npx playwright install chromium
python3 -B autodl.py ui --no-open
node tests/browser_smoke.cjs
node tests/browser_setup.cjs
node tests/browser_rename.cjs
```

默认使用 Playwright 自带 Chromium；也可设置 `CHROME_EXECUTABLE` 指定浏览器。实验改名适配 SwanLab SDK 0.10.0 及网页使用的名称更新接口，相关代码集中在 `swanlab_bridge.py`。
