#!/usr/bin/env bash
# QUIP Node Kit bootstrap. A public wallet address is enough; never pass a key.
set -euo pipefail
umask 077
KIT_REF="v0.1.0"
BASE="https://raw.githubusercontent.com/q3579338/quip-node-kit/${KIT_REF}"
install_docker=0
args=()
for arg in "$@"; do
  if [[ "$arg" == "--install-docker" ]]; then install_docker=1; else args+=("$arg"); fi
done

if ! command -v python3 >/dev/null 2>&1; then
  printf '%s\n' '需要 Python 3.10+。Ubuntu/Debian 可先执行：sudo apt-get install python3' >&2
  exit 1
fi
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || {
  printf '%s\n' '需要 Python 3.10 或更新版本。' >&2; exit 1;
}

want_help=0
dry_run=0
for arg in "${args[@]}"; do
  [[ "$arg" == "--help" || "$arg" == "-h" ]] && want_help=1
  [[ "$arg" == "--dry-run" ]] && dry_run=1
done

install_runtime_if_requested() {
if [[ "$install_docker" == 1 && "$want_help" == 0 && "$dry_run" == 0 ]] && ! command -v docker >/dev/null 2>&1; then
  [[ "$EUID" == 0 ]] || { printf '%s\n' '--install-docker 需要 root，请用 sudo bash install.sh ...' >&2; exit 1; }
  [[ -r /etc/os-release ]] || { printf '%s\n' '无法识别 Linux 发行版。' >&2; exit 1; }
  # The system-owned OS release file, not downloaded shell code.
  . /etc/os-release
  [[ "${ID:-}" == ubuntu || "${ID:-}" == debian ]] || {
    printf '%s\n' '自动安装 Docker 仅支持 Ubuntu/Debian；其他系统请按 Docker 官方文档安装。' >&2; exit 1;
  }
  [[ "${VERSION_CODENAME:-}" =~ ^[a-z0-9-]+$ ]] || { printf '%s\n' '发行版代号无效。' >&2; exit 1; }
  for package in docker.io docker-compose docker-compose-v2 podman-docker containerd runc; do
    if dpkg-query -W -f='${Status}' "$package" 2>/dev/null | grep -q 'install ok installed'; then
      printf '发现现有软件包 %s；不会自动卸载或替换。请按 Docker 官方兼容说明处理。\n' "$package" >&2
      exit 1
    fi
  done
  if [[ -e /etc/apt/sources.list.d/docker.list || -e /etc/apt/sources.list.d/docker.sources ]]; then
    printf '%s\n' '已有 Docker 软件源但 docker 命令不可用；请先检查现有安装，不覆盖配置。' >&2
    exit 1
  fi
  apt-get update
  apt-get install -y ca-certificates curl
  install -m 0755 -d /etc/apt/keyrings
  curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
    "https://download.docker.com/linux/${ID}/gpg" -o /etc/apt/keyrings/docker-quip-node-kit.asc
  chmod a+r /etc/apt/keyrings/docker-quip-node-kit.asc
  arch="$(dpkg --print-architecture)"
  printf 'Types: deb\nURIs: https://download.docker.com/linux/%s\nSuites: %s\nComponents: stable\nArchitectures: %s\nSigned-By: /etc/apt/keyrings/docker-quip-node-kit.asc\n' \
    "$ID" "$VERSION_CODENAME" "$arch" > /etc/apt/sources.list.d/docker-quip-node-kit.sources
  apt-get update
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
}

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$script_dir/quipctl.py" ]]; then
  python3 "$script_dir/quipctl.py" install "${args[@]}" --dry-run >/dev/null
  install_runtime_if_requested
  exec python3 "$script_dir/quipctl.py" install "${args[@]}"
fi

command -v curl >/dev/null 2>&1 || { printf '%s\n' '下载模式需要 curl；也可克隆完整仓库后运行。' >&2; exit 1; }
tmp_dir="$(mktemp -d -t quip-kit.XXXXXXXX)"
cleanup() {
  # Delete only the two files created here, then the now-empty temporary folder.
  rm -f -- "$tmp_dir/quipctl.py" "$tmp_dir/SHA256SUMS"
  rmdir -- "$tmp_dir" 2>/dev/null || true
}
trap cleanup EXIT
curl --fail --silent --show-error --location --retry 2 --proto '=https' --tlsv1.2 "$BASE/quipctl.py" -o "$tmp_dir/quipctl.py"
curl --fail --silent --show-error --location --retry 2 --proto '=https' --tlsv1.2 "$BASE/SHA256SUMS" -o "$tmp_dir/SHA256SUMS"
python3 - "$tmp_dir" <<'PY'
import hashlib, pathlib, re, sys
p = pathlib.Path(sys.argv[1])
matches = []
for line in (p / 'SHA256SUMS').read_text(encoding='utf-8').splitlines():
    m = re.fullmatch(r'([0-9a-f]{64})  quipctl\.py', line)
    if m:
        matches.append(m.group(1))
actual = hashlib.sha256((p / 'quipctl.py').read_bytes()).hexdigest()
if len(matches) != 1 or matches[0] != actual:
    raise SystemExit('校验失败，未执行下载的程序。')
PY
# A successful install saves the helper in the private managed state directory.
python3 "$tmp_dir/quipctl.py" install "${args[@]}" --dry-run >/dev/null
install_runtime_if_requested
python3 "$tmp_dir/quipctl.py" install "${args[@]}"
