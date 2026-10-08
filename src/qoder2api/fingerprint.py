"""虚拟机器指纹(移植自 rec2api machine-profile.ts,2026-10)

每个账号(按 uid/email 种子)确定性生成一台"虚拟机器":
  - 系统(Windows/macOS/Linux)、硬件(CPU/内存/DMI)、地区(时区/语言/用户名)从真实世界池中抽取
  - 永不模拟中国大陆/港澳时区(FORBIDDEN_TIME_ZONES,fallback Asia/Singapore)
  - hostname 形态按 OS 对齐(DESKTOP-XXXXXXX / user's-MacBook-Air.local / user-desktop)
  - PC 内存扣固件保留(128-385MB),Mac 全量——与真实机器上报口径一致
  - machine_id 用 uuid5(种子) 派生:跨重启稳定、多号互异(防风控折叠),对应 Reso2api 的
    sha256("wb2a:"+purpose+":"+uid)[:16] 手法

网关侧派生稳定请求头 ID:derive_id(uid, kind, conversation) → 32 hex,
同一会话的 X-Session-ID 恒定、不同账号互不相同,提升上游前缀缓存命中。
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Any

# (cpu, threads, memory GB 选项, vendor, product, name, laptop)
_PC_HARDWARE = (
    ("13th Gen Intel(R) Core(TM) i5-13400", 16, (16, 32), "ASUSTeK COMPUTER INC.", "PRIME B760M-A WIFI", "desktop", False),
    ("13th Gen Intel(R) Core(TM) i7-13700H", 20, (16, 32), "Dell Inc.", "XPS 15 9530", "xps-15", True),
    ("AMD Ryzen 7 7735HS", 16, (16, 32), "LENOVO", "83D4", "legion", True),
)
_MAC_HARDWARE = (
    ("Apple M2", 8, (16, 24), "Apple Inc.", "Mac14,2", "MacBook-Air", True),
    ("Apple M3", 8, (16, 24), "Apple Inc.", "Mac15,12", "MacBook-Air", True),
    ("Apple M3 Pro", 11, (18, 36), "Apple Inc.", "Mac15,6", "MacBook-Pro", True),
)
_SYSTEMS = (
    ("windows", "Windows 11 Pro 24H2", "10.0.26100"),
    ("windows", "Windows 11 Pro 25H2", "10.0.26200"),
    ("darwin", "15.6", "24.6.0"),
    ("darwin", "26.0", "25.0.0"),
    ("linux", "Ubuntu 24.04.4 LTS", "6.8.0-139-generic"),
)
_REGIONS = (
    ("Asia/Singapore", "zh-SG", ("chen", "ming", "lin", "wei")),
    ("Asia/Tokyo", "ja-JP", ("yuki", "haru", "sora", "kenta")),
    ("America/Los_Angeles", "en-US", ("alex", "ryan", "daniel", "chris")),
    ("America/New_York", "en-US", ("james", "sarah", "michael", "kevin")),
    ("Europe/London", "en-GB", ("oliver", "emma", "jack", "sophie")),
    ("Europe/Berlin", "de-DE", ("felix", "lena", "jonas", "max")),
)
FORBIDDEN_TIME_ZONES = {
    "Asia/Shanghai", "Asia/Chongqing", "Asia/Chungking", "Asia/Harbin", "Asia/Urumqi",
    "Asia/Kashgar", "PRC", "Asia/Hong_Kong", "Hongkong", "Asia/Macau", "Asia/Macao",
}
FALLBACK_TIME_ZONE = "Asia/Singapore"
_HOSTNAME_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


@dataclass(frozen=True)
class MachineProfile:
    hostname: str
    os: str
    os_version: str
    kernel_version: str
    arch: str
    cpu_model: str
    cpu_cores: int
    mem_total_mb: int
    dmi_vendor: str
    dmi_product: str
    os_username: str
    timezone: str
    locale: str
    machine_id: str   # uuid5 派生,设备流/请求头共用

    def headers(self, cli_version: str = "1.1.16") -> dict[str, str]:
        """qodercli 风格请求头。"""
        return {
            "user-agent": f"qoder/{cli_version}",
            "x-machine-id": self.machine_id,
        }


def _seed_of(identifier: str) -> bytes:
    return hashlib.sha256(identifier.strip().lower().encode()).digest()


def machine_profile(account: str) -> MachineProfile:
    """按账号种子确定性生成虚拟机器(同账号恒定,跨账号互异)。"""
    seed = _seed_of(account)

    def number(field: str) -> int:
        return int.from_bytes(hashlib.sha256(seed + b"\0" + field.encode()).digest()[:4], "big")

    def choose(field: str, values: Any) -> Any:
        return values[number(field) % len(values)]

    os_name, os_version, kernel = choose("system", _SYSTEMS)
    hw = choose("hardware", _MAC_HARDWARE if os_name == "darwin" else _PC_HARDWARE)
    tz, locale, usernames = choose("region", _REGIONS)
    username = choose("username", usernames)
    if os_name == "windows":
        code = "".join(choose(f"host{i}", _HOSTNAME_ALPHABET) for i in range(7))
        hostname = f"{'LAPTOP' if hw[6] else 'DESKTOP'}-{code}"
    elif os_name == "darwin":
        hostname = f"{username}s-{hw[5]}.local"
    else:
        hostname = f"{username}-{hw[5]}"
    installed = choose("memory", hw[2]) * 1024
    mem = installed if os_name == "darwin" else installed - (128 + number("reserved") % 257)
    if tz in FORBIDDEN_TIME_ZONES:
        tz = FALLBACK_TIME_ZONE
    return MachineProfile(
        hostname=hostname, os=os_name, os_version=os_version, kernel_version=kernel,
        arch="arm64" if os_name == "darwin" else "amd64",
        cpu_model=hw[0], cpu_cores=hw[1], mem_total_mb=mem,
        dmi_vendor=hw[3], dmi_product=hw[4],
        os_username=username, timezone=tz, locale=locale,
        machine_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"q2a:{account}")),
    )


def derive_id(account_uid: str, kind: str, conversation: str = "") -> str:
    """稳定派生 32 hex ID:同(账号,会话)恒定 → 上游前缀缓存命中;跨账号互异 → 防折叠。"""
    return hashlib.sha256(f"q2a:{kind}:{account_uid}:{conversation}".encode()).hexdigest()[:32]


def redact(text: str) -> str:
    """日志脱敏:dt-/drt-/sk_live- 令牌打码。"""
    import re
    return re.sub(r"\b(dt-|drt-|sk_live[-_])[A-Za-z0-9_\-]{6,}", r"\1***", text)
