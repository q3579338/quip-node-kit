# QUIP 中文教程与一键 CPU 节点脚本

把任务入口、QP 积分查询、普通 CPU 节点部署和常见问题放到一起。适合先用现有服务器小规模参与，再用积分快照核对结果。

**社区工具，非 QUIP 官方。核对日期：2026-10-22。** 当前适配 Aglais spec119；不承诺空投、固定日收益或多开增分。

## 先看哪一份

- [完整中文教程：任务、邀请、积分、钱包与节点](docs/tutorial.zh-CN.md)
- [部署参数与运维命令](docs/node-guide.zh-CN.md)
- [可自行修改发布的推文](docs/x-posts.zh-CN.md)
- [版本验证范围](docs/validation.md)
- [项目与第三方软件说明](NOTICE.md)

## 先分清三件事

| 名称 | 去哪里看 | 不能混作什么 |
| --- | --- | --- |
| Quest 任务积分 | [quest.quip.network](https://quest.quip.network/) | 不当成 QP |
| QP：登录、资产、交换、节点、推荐等分类 | [points.quip.network](https://points.quip.network/) | 不当成现金收益或单台机器收益 |
| Aglais 链上证明、获胜和 AGLS 测试币奖励 | 节点状态、[官方链浏览器](https://portal.aglais.quip.network/) | 不当成 QP 余额 |

当前公开 QP 规则要求父钱包关联 X 并关注 `@quipnetwork`。账号、邀请和积分资格需要在官网另行完成；脚本不能代替这些步骤。[规则原文](https://points.quip.network/)

## 一键部署：默认一个 1 核矿工

需要 Linux、Python 3.10+、Docker Engine 和 Docker Compose 插件。默认借助官方 RPC，**只启动计算矿工，不下载一整套链数据**。镜像下载和网络流量仍占用自己的服务器资源；本工具不购买云服务。

先下载脚本，可以打开文件查看再执行：

```bash
curl -fL https://raw.githubusercontent.com/q3579338/quip-node-kit/v0.1.0/install.sh -o quip-install.sh
sudo bash quip-install.sh \
  --wallet 0x替换成你的公开EVM钱包地址 \
  --public-host 替换成你的服务器IP或已有入口域名
```

不要原样使用中文占位符。`--wallet` 只接受公开 `0x` 地址，不接受私钥、助记词或密码。

**新装 Ubuntu/Debian 还没有 Docker**，可在上述命令里增加 `--install-docker`。这会从 Docker 官方 APT 软件源安装 Docker；若发现冲突的软件包或已有 Docker 软件源，脚本会停下来，不会自动卸载旧环境。已有 Docker 的服务器无需此参数。[Docker 官方说明](https://docs.docker.com/engine/install/)

查看是否真正开始计算：

```bash
sudo python3 /opt/quip-node-kit-data/quipctl.py status
```

`running`、`registered`、`mining` 分别表示容器运行、矿工登记、计算状态；`resultsReceived` 是返回的计算结果数，不代表已经获胜。首次登记可能需要几分钟，`status` 在未就绪时返回非零并保留详细状态。

成功安装后，管理程序保存在专用状态目录，不依赖下载脚本的临时文件。

## 四个各限 1 核的实例

首次部署时指定：

```bash
sudo bash quip-install.sh \
  --wallet 0x替换成你的公开EVM钱包地址 \
  --public-host 替换成你的服务器IP或已有入口域名 \
  --instances 4
```

实例优先分配不同物理核心。请求数量超过可见物理核心数时，需要明确加 `--allow-smt`；超线程会共享物理核心，不会凭空多出算力。

四个实例拥有四份独立测试网节点身份。一个钱包填四个实例是否增加 QP、如何受上限限制，仍需按项目规则和实际入账验证。脚本不会用伪造硬件、假 IP 或浏览器指纹来规避资格规则。

重复同一条安装命令会复用现有状态和身份；参数不同时会拒绝覆盖。要建立另一组，使用新的 `--state-dir`，并明确分配现有 CPU 和端口；不要将同一份节点密钥复制给同时运行的多个实例。

## 不开矿工也能查 QP

下载完整仓库后，以下命令可在 Windows、macOS 或 Linux 的 Python 3.10+ 中运行，不需要 Docker、私钥或钱包连接：

```bash
python3 quipctl.py points 0x替换成你的公开EVM钱包地址 --out ./snapshots
```

至少间隔整日再存一份，比较两份文件：

```bash
python3 quipctl.py compare ./snapshots/第一份.json ./snapshots/第二份.json
```

比较的是同一钱包的真实余额差值。观察不足 24 小时只显示差值，不外推日收益；满 24 小时的折算也只是该窗口的观测值。`projection` 按官网含义保留为“本周预估”，不当作每天已入账。

## 保留身份的日常管理

```bash
# 只看运行摘要
sudo python3 /opt/quip-node-kit-data/quipctl.py logs

# 停止本工具的矿工，保留全部数据
sudo python3 /opt/quip-node-kit-data/quipctl.py stop

# 使用现有身份重新启动
sudo python3 /opt/quip-node-kit-data/quipctl.py restart

# 更改第 2 个实例公开上报的钱包地址，保留节点密钥
sudo python3 /opt/quip-node-kit-data/quipctl.py set-wallet 0x新的公开EVM地址 --instance 2
```

最后一项只是更新公开归属字段，并不自动完成 X 关联、推荐绑定或保证积分计入。账户部署、真实币充值、链上交换及浏览器钱包签名仍由使用者自行核对操作。

## 数据和网络

- 默认数据目录：root 安装为 `/opt/quip-node-kit-data`；普通用户安装在个人目录的 `.local/share/quip-node-kit`。可用 `--state-dir` 指定。
- 每个矿工单独挂载自己的 `instances/N`；官方镜像首次启动自动生成测试网节点密钥并通过免费 AGLS 水龙头登记。工具不会读取这些密钥内容。
- 本机管理端口默认从 `127.0.0.1:24500` 开始，不开放公网管理端口，不修改防火墙，不修改宿主机网络参数。
- `public-host` 和 `advertise-port` 写入公开节点描述，**不代表脚本已经把公网访问配置好了**。若需要外部访问节点面板，使用已有安全网关另行配置；默认可通过 SSH 在本机查看状态。
- 多个计算矿工可以共用出站网络，改变代理端口并不自动改变出口 IP。公共 RPC 有可用性和容量限制；需要时用 `--rpc` 指向自己已有的 RPC。
- Docker 日志有滚动上限，矿工尝试历史仍会增长。查看自己的磁盘空间，停止后按需求归档；工具不会自动删除历史或密钥。

## 验证与来源

```bash
python3 -m unittest discover -s tests -v
bash -n install.sh
python3 quipctl.py install --wallet 0x1111111111111111111111111111111111111111 \
  --public-host 203.0.113.10 --dry-run
```

上面两个地址仅作离线测试占位，不要用于实际部署。测试不会启动矿工或创建真实节点身份。[完整验证范围](docs/validation.md)

官方入口：[任务](https://quest.quip.network/) · [账户](https://account.quip.network/) · [QP](https://points.quip.network/) · [节点文档](https://developers.quip.network/docs/nodes/run-a-node-testnet) · [官方部署仓库](https://github.com/QuipNetwork/nodes.quip.network)
