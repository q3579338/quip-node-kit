#!/usr/bin/env python3
"""Unofficial QUIP CPU miner helper. Public addresses only; no EVM signer."""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import ipaddress
import json
import os
import platform
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

try:
    import fcntl
except ImportError:
    fcntl = None

VERSION = "0.1.0"
OWNER = "quip-node-kit"
GENESIS = "0x542a9915acf840b8940d35ca6de242f669a0c6e3b40f9a8d8c14b9bf7b8ebecb"
IMAGE = "registry.gitlab.com/quip.network/quip-solver/v0.3/quip-solver@sha256:242aa46f8fc53fd2eb4b33267c7452c7bcc8b5bd8c32cd3dd038099e696b167a"
DEFAULT_RPCS = [f"wss://bootnode-{i}.aglais.quip.network:20049/rpc" for i in (1, 2, 3)]
FAUCET = "https://faucet.aglais.quip.network"
POINTS_API = "https://points.quip.network/api/user/"
UTC = dt.timezone.utc


class KitError(Exception):
    pass


def now() -> str:
    return dt.datetime.now(UTC).isoformat()


def wallet(value: str) -> str:
    if not re.fullmatch(r"0x[0-9a-fA-F]{40}", value):
        raise KitError("钱包必须是 0x 开头、40 位十六进制的公开 EVM 地址。不要输入私钥。")
    return value


def rpc_url(value: str) -> str:
    u = urllib.parse.urlsplit(value)
    if u.scheme not in ("ws", "wss", "http", "https") or not u.hostname:
        raise KitError("RPC 必须是有效的 ws/wss/http/https URL。")
    if u.username or u.password or u.query or u.fragment:
        raise KitError("RPC 不接受账号、密码、查询 token 或片段；RPC 地址可能出现在公开节点描述中。")
    try:
        u.port
    except ValueError as exc:
        raise KitError("RPC 端口无效。") from exc
    scheme = {"http": "ws", "https": "wss"}.get(u.scheme, u.scheme)
    return urllib.parse.urlunsplit((scheme, u.netloc, u.path, "", ""))


def host_name(value: str) -> str:
    value = value.strip()
    try:
        ipaddress.ip_address(value.strip("[]"))
        return value.strip("[]")
    except ValueError:
        if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?", value):
            raise KitError("public-host 只接受 IP 或域名，不接受 URL、路径或空白。")
        return value


def get_json(url: str, payload: dict | None = None, timeout: int = 15):
    body = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, headers={"User-Agent": f"quip-node-kit/{VERSION}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.load(response)


def verify_chain(rpcs: list[str]) -> str:
    errors = []
    for endpoint in rpcs:
        u = urllib.parse.urlsplit(endpoint)
        url = urllib.parse.urlunsplit(("https" if u.scheme == "wss" else "http", u.netloc, u.path, "", ""))
        try:
            response = get_json(url, {"jsonrpc": "2.0", "id": 1, "method": "chain_getBlockHash", "params": [0]})
            if response.get("result") == GENESIS:
                return endpoint
            errors.append(f"{endpoint}: 创世区块不匹配")
        except (OSError, ValueError, urllib.error.URLError) as exc:
            errors.append(f"{endpoint}: {exc}")
    raise KitError("没有通过 Aglais spec119 创世区块校验的 RPC；未启动矿工。\n" + "\n".join(errors))


def run(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(args, text=True, encoding="utf-8", errors="replace", capture_output=True)
    if check and result.returncode:
        raise KitError(f"命令失败：{' '.join(args[:5])}\n{result.stderr[-1800:]}")
    return result


def private_write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(temp, path)


def write_json(path: Path, value):
    private_write(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def default_state() -> Path:
    return Path("/opt/quip-node-kit-data") if hasattr(os, "geteuid") and os.geteuid() == 0 else Path.home() / ".local/share/quip-node-kit"


def safe_state(path: Path) -> Path:
    if path.is_symlink():
        raise KitError("状态目录不能是符号链接。")
    path = path.expanduser().resolve()
    if path in (Path("/"), Path("/opt"), Path("/etc"), Path("/tmp"), Path.home().resolve()):
        raise KitError("请使用专门的状态子目录。")
    return path


def read_manifest(state: Path) -> dict:
    try:
        value = json.loads((state / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise KitError("找不到有效的工具管理记录；不会操作未知容器或目录。") from exc
    if value.get("managedBy") != OWNER or value.get("schema") != 1 or value.get("stateDir") != str(state):
        raise KitError("状态目录归属校验失败。")
    return value


def cpu_order() -> tuple[list[int], int]:
    allowed = sorted(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else list(range(os.cpu_count() or 1))
    first, siblings, seen = [], [], set()
    for cpu in allowed:
        topo = Path(f"/sys/devices/system/cpu/cpu{cpu}/topology")
        try:
            core = ((topo / "physical_package_id").read_text().strip(), (topo / "core_id").read_text().strip())
        except OSError:
            core = ("unknown", str(cpu))
        if core in seen:
            siblings.append(cpu)
        else:
            first.append(cpu)
            seen.add(core)
    return first + siblings, len(first)


def make_plan(args) -> dict:
    state = safe_state(Path(args.state_dir))
    address = wallet(args.wallet)
    endpoints = [rpc_url(x) for x in (args.rpc or DEFAULT_RPCS)]
    if len(endpoints) > 8:
        raise KitError("最多设置 8 个 RPC。")
    host = host_name(args.public_host)
    if not 1 <= args.instances <= 64:
        raise KitError("instances 范围为 1～64。")
    if not 1024 <= args.port_base <= 65535 - args.instances + 1:
        raise KitError("本机状态端口超出可用范围。")
    if not 1 <= args.advertise_port <= 65535:
        raise KitError("advertise-port 范围为 1～65535。")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", args.name):
        raise KitError("name 使用 1～32 位字母、数字、下划线或连字符。")
    order, physical = cpu_order()
    if args.instances > len(order):
        raise KitError(f"请求 {args.instances} 个 1 核矿工，但当前只有 {len(order)} 个可用逻辑 CPU。")
    if args.instances > physical and not args.allow_smt:
        raise KitError(f"可见物理核心为 {physical} 个。使用超线程共享物理核须明确加 --allow-smt。")
    project = "qnk-" + hashlib.sha256(str(state).encode()).hexdigest()[:10]
    return {"managedBy": OWNER, "schema": 1, "version": VERSION, "stateDir": str(state), "project": project,
            "wallet": address, "rpcs": endpoints, "publicHost": host, "advertisePort": args.advertise_port,
            "name": args.name, "instances": args.instances, "portBase": args.port_base,
            "cpus": order[:args.instances], "visiblePhysicalCores": physical, "allowSmt": args.allow_smt,
            "image": IMAGE, "createdAtUTC": now(), "instanceWallets": {str(i): address for i in range(1, args.instances + 1)}}


def container_name(m: dict, i: int) -> str:
    return f"{m['project']}-cpu-{i}"


def node_config(m: dict, i: int) -> str:
    q = json.dumps
    return (f"[miner]\nvalidators = {q(m['rpcs'])}\nfaucet_url = {q(FAUCET)}\n"
            f"signer_key = \"/data/keystore.json\"\npublic_host = {q(m['publicHost'])}\n"
            f"public_port = {m['advertisePort']}\nnode_name = {q(m['name']+'-'+str(i)+' - '+m['instanceWallets'][str(i)])}\n\n"
            "[cpu]\nbinary = \"quip-cpu-msa\"\nnum_cpus = 1\n\n"
            "[dashboard]\nlisten = \"0.0.0.0:8086\"\ndata_dir = \"/data/attempts\"\n")


def compose_text(m: dict) -> str:
    q = json.dumps
    chunks = [f"name: {m['project']}\nservices:\n"]
    for i, cpu in enumerate(m["cpus"], 1):
        chunks.append(f"""  cpu-{i}:
    image: {q(m['image'])}
    pull_policy: never
    container_name: {q(container_name(m, i))}
    restart: unless-stopped
    cpus: 1.0
    cpuset: {q(str(cpu))}
    cpu_shares: 128
    mem_limit: 1g
    pids_limit: 256
    shm_size: 64m
    security_opt:
      - no-new-privileges:true
    labels:
      io.satloot.quip-kit: {q(OWNER)}
    volumes:
      - type: bind
        source: {q(str(Path(m['stateDir'])/'instances'/str(i)))}
        target: /data
    ports:
      - {q('127.0.0.1:'+str(m['portBase']+i-1)+':8086')}
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
""")
    return "".join(chunks)


def docker_ready():
    if os.name != "posix" or fcntl is None:
        raise KitError("部署和管理矿工需要 Linux；积分查询与对比可以在其他系统使用。")
    if platform.machine().lower() not in ("x86_64", "amd64"):
        raise KitError("本版本固定的 CPU 镜像只验证了 Linux x86_64；其他架构请用官方节点管理器。")
    if not shutil.which("docker"):
        raise KitError("未安装 Docker。按官方文档安装，或在新 Ubuntu/Debian 上使用 install.sh --install-docker。")
    run(["docker", "info", "--format", "{{.ServerVersion}}"])
    run(["docker", "compose", "version", "--short"])


def compose_cmd(state: Path, *args: str) -> list[str]:
    return ["docker", "compose", "-f", str(state / "compose.yml"), *args]


def owned_container(m: dict, i: int) -> dict | None:
    result = run(["docker", "inspect", container_name(m, i)], check=False)
    if result.returncode:
        if "No such" in result.stderr:
            return None
        raise KitError("无法确认容器状态，停止操作：" + result.stderr[-500:])
    value = json.loads(result.stdout)[0]
    labels = value.get("Config", {}).get("Labels", {}) or {}
    if labels.get("io.satloot.quip-kit") != OWNER or labels.get("com.docker.compose.project") != m["project"]:
        raise KitError("发现同名但不属于本工具的容器，不会覆盖它。")
    return value


def verify_ports(m: dict):
    for i in range(1, m["instances"] + 1):
        port = m["portBase"] + i - 1
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError as exc:
                existing = owned_container(m, i)
                published = [] if not existing else existing.get("NetworkSettings", {}).get("Ports", {}).get("8086/tcp", []) or []
                if not any(x.get("HostIp") == "127.0.0.1" and x.get("HostPort") == str(port) for x in published):
                    raise KitError(f"本机端口 {port} 已被其他服务占用；使用 --port-base 另选端口。") from exc


@contextlib.contextmanager
def operation_lock(state: Path):
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    with open(state / ".operation.lock", "a", encoding="utf-8") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise KitError("另一个管理操作正在运行，请稍后再试。") from exc
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def prepare(plan: dict):
    state = Path(plan["stateDir"])
    old = None
    if (state / "manifest.json").exists():
        old = read_manifest(state)
        identity_fields = ("project", "wallet", "rpcs", "publicHost", "advertisePort", "name", "instances", "portBase", "image")
        if any(old.get(k) != plan.get(k) for k in identity_fields):
            raise KitError("此目录已部署且参数不同。不会覆盖身份；更换公开地址请用 set-wallet，另建实验请用新的 --state-dir。")
        for i in range(1, old["instances"] + 1):
            if not (state / "instances" / str(i) / "config.toml").is_file():
                raise KitError("现有节点配置缺失；不会自动重建或替换身份，请先恢复备份。")
            if old.get("startedAtUTC") and not (state / "instances" / str(i) / "keystore.json").is_file():
                raise KitError("已经启动过的节点缺少密钥；首次启动中请稍候，否则先恢复备份，不会自动换身份。")
        return old
    entries = [p.name for p in state.iterdir()] if state.exists() else []
    if any(name != ".operation.lock" for name in entries):
        raise KitError("目录里有未受本工具管理的数据；不会覆盖。请选空的专用状态目录。")
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(state, 0o700)
    for i in range(1, plan["instances"] + 1):
        data = state / "instances" / str(i)
        data.mkdir(parents=True, mode=0o700)
        private_write(data / "config.toml", node_config(plan, i))
    private_write(state / "compose.yml", compose_text(plan))
    write_json(state / "manifest.json", plan)
    return plan


def install(args):
    plan = make_plan(args)
    if args.dry_run:
        print(json.dumps({"plan": plan, "compose": compose_text(plan), "firstNodeConfig": node_config(plan, 1), "changesMade": False}, ensure_ascii=False, indent=2))
        return
    docker_ready()
    # Check an existing install before network pulls, file changes or container changes.
    state = Path(plan["stateDir"])
    if (state / "manifest.json").exists():
        read_manifest(state)
    elif state.exists() and any(x.name != ".operation.lock" for x in state.iterdir()):
        raise KitError("状态目录不是空目录，且没有本工具的管理记录；不会改动。")
    with operation_lock(state):
        m = prepare(plan)
        verify_ports(m)
        endpoint = verify_chain(m["rpcs"])
        run(compose_cmd(state, "config", "--quiet"))
        for i in range(1, m["instances"] + 1):
            owned_container(m, i)
        if run(["docker", "image", "inspect", m["image"], "--format", "{{.Id}}"], check=False).returncode:
            print("下载固定摘要的官方 CPU 镜像……", flush=True)
            run(["docker", "pull", m["image"]])
        run(compose_cmd(state, "up", "-d", "--pull", "never"))
        if not m.get("startedAtUTC"):
            m["startedAtUTC"] = now()
            write_json(state / "manifest.json", m)
        private_write(state / "quipctl.py", Path(__file__).read_text(encoding="utf-8"))
        print(f"已启动 {m['instances']} 个各限 1 核的矿工。RPC 创世区块校验通过：{endpoint}")
        print(f"状态目录：{state}；管理端口仅绑定 127.0.0.1。")
        print("首次启动会生成独立测试网节点密钥，并通过免费 AGLS 水龙头登记。登记可能需要几分钟。")
        print("公开 EVM 地址只写入节点描述；本工具不连接 EVM 钱包。运行成功不等于 QP 已记入。")
        print(f"管理程序：{state / 'quipctl.py'}")
        print(f"查看状态：python3 {state / 'quipctl.py'} --state-dir {state} status")


def collect_status(state: Path) -> dict:
    m = read_manifest(state)
    rows = []
    for i in range(1, m["instances"] + 1):
        info = owned_container(m, i)
        row = {"instance": i, "container": container_name(m, i), "parentWallet": m["instanceWallets"][str(i)], "localStatusPort": m["portBase"] + i - 1,
               "running": bool(info and info["State"]["Running"]), "nodeAccount": None, "registered": None, "mining": None}
        if info:
            row.update(cpuQuota=info["HostConfig"]["NanoCpus"] / 1e9, cpuset=info["HostConfig"]["CpusetCpus"], restartCount=info["RestartCount"])
        try:
            d = get_json(f"http://127.0.0.1:{row['localStatusPort']}/api/v1/status", timeout=4)["data"]
            row.update(nodeAccount=d.get("ss58_address"), registered=d.get("miner_registered"), mining=d.get("is_mining"),
                       chainHead=d.get("chain", {}).get("head_number"), resultsReceived=sum(v.get("controller", {}).get("results_received", 0) for v in d.get("modes", {}).values()))
        except (OSError, ValueError, KeyError, urllib.error.URLError) as exc:
            row["statusError"] = str(exc)
        rows.append(row)
    return {"checkedAtUTC": now(), "project": m["project"], "instances": rows}


def status(args):
    docker_ready()
    result = collect_status(safe_state(Path(args.state_dir)))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if any(not r["running"] or not r["registered"] or not r["mining"] for r in result["instances"]):
        raise KitError("仍有实例未就绪；上方保留了逐实例状态，不代表所有矿工成功。")


def lifecycle(args):
    docker_ready()
    state = safe_state(Path(args.state_dir))
    read_manifest(state)
    with operation_lock(state):
        m = read_manifest(state)
        for i in range(1, m["instances"] + 1):
            owned_container(m, i)
            if args.action == "restart" and not (state / "instances" / str(i) / "keystore.json").is_file():
                raise KitError("节点密钥缺失，拒绝通过重启生成新身份；请先恢复备份。")
        print(run(compose_cmd(state, args.action)).stdout)
        print("数据目录和节点身份已保留。")


def set_wallet(args):
    new = wallet(args.wallet)
    docker_ready()
    state = safe_state(Path(args.state_dir))
    read_manifest(state)
    with operation_lock(state):
        m = read_manifest(state)
        ids = [args.instance] if args.instance else list(range(1, m["instances"] + 1))
        if any(i < 1 or i > m["instances"] for i in ids):
            raise KitError("instance 超出已部署实例范围。")
        for i in ids:
            owned_container(m, i)
            if not (state / "instances" / str(i) / "keystore.json").is_file():
                raise KitError("节点密钥文件缺失；不会通过重启悄悄创建新身份。")
        stamp = dt.datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        backup = state / "backups" / stamp
        backup.mkdir(parents=True, mode=0o700)
        shutil.copy2(state / "manifest.json", backup / "manifest.json")
        for i in ids:
            cfg = state / "instances" / str(i) / "config.toml"
            shutil.copy2(cfg, backup / f"config-{i}.toml")
            m["instanceWallets"][str(i)] = new
            private_write(cfg, node_config(m, i))
        if not args.instance:
            m["wallet"] = new
        write_json(state / "manifest.json", m)
        try:
            run(compose_cmd(state, "restart", *[f"cpu-{i}" for i in ids]))
        except KitError:
            for i in ids:
                shutil.copy2(backup / f"config-{i}.toml", state / "instances" / str(i) / "config.toml")
            shutil.copy2(backup / "manifest.json", state / "manifest.json")
            # Reapply restored config to any service already restarted before the failure.
            run(compose_cmd(state, "restart", *[f"cpu-{i}" for i in ids]), check=False)
            raise
        print(f"已修改 {len(ids)} 个实例的公开归属地址。节点身份和历史保留。备份：{backup}")
        print("这是描述字段更新；请另行核对官网索引、X 资格和积分入账。")


def logs(args):
    docker_ready()
    m = read_manifest(safe_state(Path(args.state_dir)))
    ids = [args.instance] if args.instance else list(range(1, m["instances"] + 1))
    if any(i < 1 or i > m["instances"] for i in ids):
        raise KitError("instance 超出已部署范围。")
    for i in ids:
        if not owned_container(m, i):
            continue
        result = run(["docker", "logs", "--tail", "120", container_name(m, i)], check=False)
        print(f"--- cpu-{i}：仅展示运行摘要 ---")
        for line in (result.stdout + result.stderr).splitlines():
            lower = line.lower()
            if any(x in lower for x in ("mnemonic", "secret", "seed", "private", "passphrase", "recovery", "password")):
                continue
            if any(x in lower for x in ("new round", "lease done", "miner registered", "filed node descriptor", "error", "warn", "funding")):
                print(line[:600])


def points(args):
    address = wallet(args.wallet)
    d = get_json(POINTS_API + address.lower())
    if str(d.get("wallet", "")).lower() != address.lower():
        raise KitError("积分接口返回的钱包不匹配。")
    record = {"checkedAtUTC": now(), "wallet": address, "source": POINTS_API + address.lower(), "data": d}
    if args.out:
        output = Path(args.out).expanduser().resolve()
        output.mkdir(parents=True, exist_ok=True)
        stamp = dt.datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        path = output / f"points-{address.lower()}-{stamp}.json"
        write_json(path, record)
        print(f"快照已保存：{path}", file=sys.stderr)
    print(json.dumps(record, ensure_ascii=False, indent=2))
    if not d.get("found"):
        print("此地址尚未被公开 QP 接口识别；显示的 0 不是已验证的有效账户余额。", file=sys.stderr)


def compare_records(a: dict, b: dict) -> dict:
    wa, wb = wallet(a["wallet"]), wallet(b["wallet"])
    if wa.lower() != wb.lower():
        raise KitError("两份快照不是同一钱包，不能相减。")
    if not a["data"].get("found") or not b["data"].get("found"):
        raise KitError("至少一份快照的账户尚未被识别，不能据此计算日收益。")
    start = dt.datetime.fromisoformat(a["checkedAtUTC"].replace("Z", "+00:00"))
    end = dt.datetime.fromisoformat(b["checkedAtUTC"].replace("Z", "+00:00"))
    if start.tzinfo is None or end.tzinfo is None:
        raise KitError("快照缺少时区。")
    hours = (end - start).total_seconds() / 3600
    if hours <= 0:
        raise KitError("第二份快照必须晚于第一份。")
    ab, bb = a["data"].get("balance", {}), b["data"].get("balance", {})
    delta = {k: bb[k] - ab[k] for k in sorted(ab.keys() & bb.keys()) if isinstance(ab[k], (int, float)) and isinstance(bb[k], (int, float))}
    result = {"wallet": wa, "startUTC": a["checkedAtUTC"], "endUTC": b["checkedAtUTC"], "hours": round(hours, 3), "deltaQP": delta,
              "warning": "钱包整体差值，不能分摊到每台机器；projection 是官网本周预估，不用于相减。"}
    if any(v < 0 for v in delta.values()):
        result["warning"] += " 存在负差值，可能涉及修正或统计变化，不计算日均。"
    elif hours >= 24 and "node" in delta:
        result["observedNodeQPPer24Hours"] = round(delta["node"] * 24 / hours, 3)
        result["warning"] += " 每24小时数值仅为这个观察窗口的折算，不是未来收益承诺。"
    else:
        result["warning"] += " 观察不足24小时，只展示差值，不外推一天收益。"
    return result


def compare(args):
    a = json.loads(Path(args.before).read_text(encoding="utf-8-sig"))
    b = json.loads(Path(args.after).read_text(encoding="utf-8-sig"))
    print(json.dumps(compare_records(a, b), ensure_ascii=False, indent=2))


def parser():
    p = argparse.ArgumentParser(description="QUIP CPU 节点部署、状态和公开 QP 快照。不会读取 EVM 私钥。")
    p.add_argument("--state-dir", default=str(default_state()), help="专用状态目录；可放在子命令前或后")
    sub = p.add_subparsers(dest="action", required=True)
    x = sub.add_parser("install", help="部署；重复相同参数保留身份")
    x.add_argument("--wallet", required=True, help="公开 EVM 父钱包地址")
    x.add_argument("--public-host", required=True, help="节点描述中的 IP/域名；不会自动开放公网面板")
    x.add_argument("--instances", type=int, default=1)
    x.add_argument("--rpc", action="append", help="可重复；默认官方 Aglais RPC")
    x.add_argument("--name", default="quip-cpu")
    x.add_argument("--port-base", type=int, default=24500)
    x.add_argument("--advertise-port", type=int, default=20049)
    x.add_argument("--allow-smt", action="store_true", help="明确允许实例数超过可见物理核数")
    x.add_argument("--dry-run", action="store_true", help="只打印计划，不联网、不写配置、不启动")
    x.set_defaults(func=install)
    x = sub.add_parser("status", help="逐实例检查真实状态；未就绪时返回非零")
    x.set_defaults(func=status)
    for action in ("stop", "restart"):
        x = sub.add_parser(action, help="只管理本工具的容器，保留数据")
        x.set_defaults(func=lifecycle)
    x = sub.add_parser("set-wallet", help="只更改公开归属地址，不更改节点密钥")
    x.add_argument("wallet")
    x.add_argument("--instance", type=int)
    x.set_defaults(func=set_wallet)
    x = sub.add_parser("logs", help="运行摘要，隐藏可能含密钥的日志行")
    x.add_argument("--instance", type=int)
    x.set_defaults(func=logs)
    x = sub.add_parser("points", help="读取官方公开 QP；不需要 Docker 或钱包连接")
    x.add_argument("wallet")
    x.add_argument("--out", help="保存带时间的积分快照到此目录")
    x.set_defaults(func=points)
    x = sub.add_parser("compare", help="同一钱包两份快照比较，满24小时才折算日增量")
    x.add_argument("before")
    x.add_argument("after")
    x.set_defaults(func=compare)
    for command in sub.choices.values():
        command.add_argument("--state-dir", default=argparse.SUPPRESS, help="专用状态目录（部署/管理）")
    return p


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        args.func(args)
        return 0
    except (KitError, OSError, ValueError, KeyError, urllib.error.URLError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
