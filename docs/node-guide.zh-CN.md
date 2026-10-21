# 部署参数与运维

本工具只创建有自己专用前缀和归属标签的计算矿工，不管理现有其他项目容器。默认不创建全链节点、不开放公网面板、不修改防火墙。

## 完整仓库方式

```bash
git clone --branch v0.1.0 --depth 1 https://github.com/q3579338/quip-node-kit.git
cd quip-node-kit
sudo bash install.sh --wallet 0x你的公开地址 --public-host 你的IP或域名
```

本地 `install.sh` 直接使用同目录的 `quipctl.py`；单独下载的安装脚本会从同一个固定版本下载程序，并核对 SHA256SUMS。校验可以发现文件不一致，不等于对整个第三方供应链作安全保证。

## 参数

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--wallet` | 必填 | 公开 EVM 父钱包，不是私钥或矿工 SS58 |
| `--public-host` | 必填 | 节点公开描述中的 IP/域名；不自动开放网络入口 |
| `--instances` | `1` | 每实例限 1 个 CPU 额度 |
| `--rpc` | 3 个官方 Aglais RPC | 可重复填写；必须不含账号、密码或查询 token |
| `--state-dir` | root：`/opt/quip-node-kit-data` | 独立目录；不要混用其他项目目录 |
| `--port-base` | `24500` | 管理端口从这里递增，仅绑定本机 |
| `--advertise-port` | `20049` | 节点描述字段，须按实际入口安排；不等于自动发布端口 |
| `--name` | `quip-cpu` | 节点名前缀，限字母数字、下划线和连字符 |
| `--allow-smt` | 关闭 | 实例数超过物理核时，明确接受超线程共享 |
| `--dry-run` | 关闭 | 只生成计划，不联网、不写运行文件、不调用 Docker |
| `--install-docker` | 关闭 | 仅供 Shell 安装器，显式在新 Ubuntu/Debian 安装 Docker |

脚本固定使用已核对的 CPU 镜像摘要。RPC 创世区块不匹配就停止启动；镜像不会自动变成 `latest`。不支持自动切到其他链继续运行。

## 连接自己已有的链节点

```bash
sudo bash install.sh \
  --wallet 0x你的公开地址 \
  --public-host 你的IP或已有网关 \
  --rpc wss://你的已有RPC域名/rpc \
  --instances 2
```

`--rpc` 只提供连接信息，不把私钥或认证 token 写进参数。若自己的服务需要带密钥的 URL，先配置受控的内部网关；不要把 token 放到可能被公开的节点描述里。

## 两组不同的钱包

第一组部署后，可以只修改其中一个实例公开上报的钱包：

```bash
sudo python3 /opt/quip-node-kit-data/quipctl.py set-wallet 0x另一个公开地址 --instance 2
```

配置会备份、对应矿工会重启；节点签名身份保留。程序不连接浏览器钱包，不会替新钱包完成邀请、X 关联或资格核验。每个实例的资源额度也不会因换地址而增加。

想创建全新的隔离实验，可使用新的状态目录和本机端口起点。多个独立实验会各自消耗 CPU，工具只检查单组实例数，**不会替你保证多组总量不超出主机容量**。

## 查看状态、摘要和停止

```bash
sudo python3 /opt/quip-node-kit-data/quipctl.py status
sudo python3 /opt/quip-node-kit-data/quipctl.py logs --instance 1
sudo python3 /opt/quip-node-kit-data/quipctl.py stop
sudo python3 /opt/quip-node-kit-data/quipctl.py restart
```

`status` 未就绪时返回非零，便于人工或现有监控识别。`logs` 只显示特定运行摘要，过滤含恢复口令等敏感关键词的行；不要据此认为任何未过滤的任意日志都可以公开上传。

管理程序会核对容器标签与 Compose 项目归属。同名但属于其他项目的容器会被拒绝操作。`stop` 不删除卷、密钥或历史；本工具没有一键删除数据命令。

## 备份和恢复范围

停止该组矿工后，安全备份整个专用状态目录：包括 `manifest.json`、`compose.yml`、管理程序、各 `instances/N` 目录及其节点身份。备份放在自己控制的位置，不上传到公开仓库。不要让同一节点身份在两台机器同时运行。

本版本不提供自动跨主机恢复向导。Compose 中含绝对状态路径，CPU 绑定也与原主机有关；迁移时应重新核对路径、核心编号、网络和版本。不要原样启动一份未经核对的备份，更不要用重新生成密钥来冒充恢复成功。

每次 `set-wallet` 会把原配置保存在 `backups/时间戳`。节点身份不在这些配置备份中被重新生成；原密钥仍保存在各实例自己的目录。

## 安装失败时

- **Python 版本不足**：使用 Python 3.10+；不要下载未知来源的“修复包”。
- **Docker 权限或服务不可用**：先核对 `docker info` 和 `docker compose version`；工具不会重装或重启其他容器来解决。
- **已有冲突 Docker 包**：按 Docker 官方安装说明处理，安装器不会替你卸载旧环境。
- **端口占用**：首次部署选择另一个 `--port-base`，不要结束不认识的服务。
- **已有目录参数不同**：使用 `set-wallet` 修改地址；新实验使用新目录。不覆盖现有密钥。
- **节点密钥缺失**：已启动过的实例会拒绝静默重建，优先找备份。
- **RPC 不通或链不匹配**：连接自己已有的正确 RPC，或等待官方恢复。工具不会自动放松创世区块校验。
- **登记成功但没有 QP**：看 EVM 父钱包、X 资格、官网索引和真实积分记录；不要先假设需要充值或无限增加实例。

## 审查和离线检查

```bash
python3 -m unittest discover -s tests -v
bash -n install.sh
python3 quipctl.py install \
  --wallet 0x1111111111111111111111111111111111111111 \
  --public-host 203.0.113.10 --instances 1 --dry-run
```

测试只用临时合成身份标记，不带真实密钥或运行服务器的访问资料。`--dry-run` 输出中有公开配置与镜像摘要，可先检查再正式执行。
