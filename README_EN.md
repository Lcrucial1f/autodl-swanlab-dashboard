# AutoDL × SwanLab Dashboard

[简体中文](README.md) | English

Your AutoDL instances, GPU usage, and SwanLab experiments in one dashboard.

## Why I built this

When renting several AutoDL instances, it is easy to lose track of which machines are idle and which experiments they are running. I also use SwanLab to track experiments, so troubleshooting means switching between experiment pages and the corresponding machines.

I built this dashboard to connect those two views: check GPU resources, open the matching experiment to follow its progress, and connect to the right machine when something needs attention.

## Features

- **Organize instances by region:** Add machines, check SSH connections, and remove local entries under custom regions.
- **Live GPU overview:** See online machines, busy and idle GPU counts, utilization, and free GPU memory.
- **Match SwanLab experiments:** Identify the project and experiment running on each machine using training processes and logs.
- **Open experiments directly:** Jump from an instance to its SwanLab experiment to check training progress.
- **Sync experiment names both ways:** Rename an experiment in the dashboard or SwanLab and keep the displayed name in sync.
- **Quick SSH access:** Run `autodl` to select a machine, or `autodl 1` to connect directly to instance 1 using your SSH key.

## Quick start

Supports **macOS, Linux, and Windows WSL**. Requires Python 3.9+, Git, and OpenSSH. Remote instances need Python and `nvidia-smi`.

```sh
git clone https://github.com/Lcrucial1f/autodl-swanlab-dashboard.git
cd autodl-swanlab-dashboard
python3 install.py
~/.local/bin/autodl setup
```

The installer creates a virtual environment and installs SwanLab. The setup command opens the connection settings at [http://127.0.0.1:8766](http://127.0.0.1:8766). The interface currently uses Chinese labels; the steps below include them.

To use `autodl` directly, add this line to `~/.zshrc` or `~/.bashrc`, then open a new terminal:

```sh
export PATH="$HOME/.local/bin:$PATH"
```

### Add an AutoDL instance

1. Start an instance in the AutoDL console and copy its **SSH login command**.
2. Open **Connection settings → AutoDL instances** (连接配置 → AutoDL 实例), paste the command, and optionally enter an alias and region.
3. Enter the instance's SSH password, or select an authorized private key under **Use an existing private key** (使用已有私钥), then click **Add and connect** (添加并连接).

The password is used only to install your public key and is never saved. Password-based setup requires `expect`: run `brew install expect` on macOS or `sudo apt install expect` on Ubuntu / WSL. Alternatively, run `autodl pubkey` and add the public key through the AutoDL console.

If the connection fails, click **Test** (检测) or run `autodl key 1` to retry key setup for instance 1. New instances must be added manually; removing a local entry does not release the cloud instance.

### Connect SwanLab

1. Open [SwanLab settings](https://swanlab.cn/settings) and copy your **API Key**.
2. Paste it under **Connection settings → SwanLab account** (连接配置 → SwanLab 账号), then click **Verify and save** (验证并保存).
3. Once connected, return to the dashboard. Click **Open SwanLab** (打开 SwanLab) to view an experiment, or **Rename** (修改名称) to update its name.

Existing `swanlab login` credentials or `SWANLAB_API_KEY` are detected automatically; use **Check connection** (检查连接) to verify them. GPU monitoring works without SwanLab login. Experiment matching requires SwanLab logs from the remote training job. Currently supports SwanLab public cloud; names changed there appear in the dashboard in about 30 seconds.

## Common commands

```sh
autodl           # Select an instance and connect
autodl ui        # Open the dashboard
autodl setup     # Open connection settings
autodl 1         # Connect to instance 1
autodl free      # Find machines with low-usage GPUs
autodl config    # Show local configuration paths
autodl ui stop   # Stop the local dashboard
```

Instance and account settings are stored in `~/.config/autodl/`; SSH keys default to `~/.ssh/`. They are not included in the repository or share package. See the [example instance configuration](examples/instances.example.json) for manual setup.
