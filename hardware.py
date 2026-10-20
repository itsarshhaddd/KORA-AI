"""Hardware detection + automatic AI settings for KORA.

Pure logic (no imports from the rest of the app) so it can be tested alone.
Detects CPU cores, RAM and GPUs (NVIDIA / AMD / Intel) and turns that into
a Profile: GPU offload, context size, threads, batch size, whether to load
the vision/audio projector. The goal is "fast on a good PC, still works on
a laptop, never eats the whole machine".
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field

log = logging.getLogger("kora.hardware")

GB = 1024 ** 3
KV_GB_PER_1K_CTX = 0.08      # conservative KV-cache estimate for a 2B-class model
EST_CHAT_LAYERS = 35         # only used for partial GPU offload on small cards
_SKIP_ADAPTERS = ("basic display", "remote display", "virtual", "parsec", "indirect", "displaylink", "mirage", "hyper-v")


@dataclass
class GPU:
    name: str
    vendor: str            # nvidia | amd | intel | other
    vram_gb: float         # dedicated memory (0 = unknown)
    free_gb: float | None  # currently free, when the driver tells us (nvidia-smi)
    integrated: bool

    @property
    def usable_gb(self) -> float:
        total = self.vram_gb if self.vram_gb > 0 else 4.0   # unknown discrete card: assume 4 GB
        if self.free_gb is not None:
            total = min(total, self.free_gb)
        return total * 0.92


@dataclass
class Hardware:
    cpu_logical: int = 4
    cpu_cores: int = 2
    ram_total_gb: float = 8.0
    ram_avail_gb: float = 4.0
    gpus: list[GPU] = field(default_factory=list)

    def best_gpu(self, allow_integrated: bool = False) -> GPU | None:
        pool = [g for g in self.gpus if allow_integrated or not g.integrated]
        return max(pool, key=lambda g: (not g.integrated, g.usable_gb), default=None)


@dataclass
class Profile:
    tier: str = "cpu"                 # gpu-high | gpu | hybrid | cpu | cpu-lite
    mode: str = "cpu"                 # gpu | hybrid | cpu
    backends: tuple[str, ...] = ("cpu",)   # runtime sub-folders to try, best first
    chat_gpu_layers: int = 0
    embed_gpu_layers: int = 0
    chat_ctx: int = 4096
    embed_ctx: int = 4096
    chat_threads: int = 2
    embed_threads: int = 2
    chat_ubatch: int | None = None
    use_mmproj: bool = True
    mmproj_offload: bool = False
    gpu_name: str = ""
    summary: str = ""
    warnings: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- detection

def _hidden() -> dict:
    if os.name != "nt":
        return {}
    return {"creationflags": subprocess.CREATE_NO_WINDOW}


def _ram() -> tuple[float, float]:
    try:
        if os.name == "nt":
            import ctypes

            class MEM(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            m = MEM()
            m.dwLength = ctypes.sizeof(MEM)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            return m.ullTotalPhys / GB, m.ullAvailPhys / GB
        info = {}
        with open("/proc/meminfo") as fh:
            for line in fh:
                k, v = line.split(":", 1)
                info[k] = int(v.split()[0]) * 1024
        return info["MemTotal"] / GB, info.get("MemAvailable", info["MemTotal"] / 2) / GB
    except Exception:  # noqa: BLE001
        log.warning("Could not read RAM size; assuming 8 GB", exc_info=True)
        return 8.0, 4.0


def _physical_cores(logical: int) -> int:
    if os.name == "nt":
        try:
            import ctypes
            from ctypes import wintypes

            k32 = ctypes.windll.kernel32
            size = wintypes.DWORD(0)
            k32.GetLogicalProcessorInformation(None, ctypes.byref(size))
            buf = ctypes.create_string_buffer(size.value)
            if k32.GetLogicalProcessorInformation(buf, ctypes.byref(size)):
                ptr = ctypes.sizeof(ctypes.c_void_p)
                entry = 32 if ptr == 8 else 24
                count = sum(1 for off in range(0, size.value, entry)
                            if int.from_bytes(buf.raw[off + ptr: off + ptr + 4], "little") == 0)
                if count:
                    return count
        except Exception:  # noqa: BLE001
            pass
    return logical // 2 if logical >= 4 else logical   # assume SMT/hyper-threading


_DISCRETE_RE = re.compile(r"\brx\b|\brx ?\d|pro w\d|pro v\d|radeon pro|\bvii\b|instinct|arc\(?tm\)? ?(pro )?[ab]\d{3}|arc [ab]\d{3}", re.I)


def _classify(name: str, vram_gb: float) -> tuple[str, bool]:
    n = name.lower()
    if any(x in n for x in ("nvidia", "geforce", "quadro", "rtx", "gtx", "tesla")):
        return "nvidia", False
    vendor = "amd" if ("amd" in n or "radeon" in n) else "intel" if "intel" in n else "other"
    discrete = bool(_DISCRETE_RE.search(name)) or (vendor == "other" and vram_gb >= 3)
    return vendor, not discrete


def _windows_gpus() -> list[GPU]:
    gpus: list[GPU] = []
    try:
        import winreg

        base = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as root:
            for i in range(64):
                try:
                    sub = winreg.EnumKey(root, i)
                except OSError:
                    break
                if not sub.isdigit():
                    continue
                try:
                    with winreg.OpenKey(root, sub) as key:
                        name = str(winreg.QueryValueEx(key, "DriverDesc")[0])
                        mem = 0
                        for value in ("HardwareInformation.qwMemorySize", "HardwareInformation.MemorySize"):
                            try:
                                raw = winreg.QueryValueEx(key, value)[0]
                                mem = int.from_bytes(raw, "little") if isinstance(raw, (bytes, bytearray)) else int(raw)
                                if mem:
                                    break
                            except OSError:
                                continue
                except OSError:
                    continue
                if any(s in name.lower() for s in _SKIP_ADAPTERS) or any(g.name == name for g in gpus):
                    continue
                vram = mem / GB
                vendor, integrated = _classify(name, vram)
                gpus.append(GPU(name, vendor, vram, None, integrated))
    except Exception:  # noqa: BLE001
        log.info("Registry GPU query failed", exc_info=True)

    if not gpus:   # fallback: PowerShell (AdapterRAM is capped at 4 GB, but better than nothing)
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "Get-CimInstance Win32_VideoController | Select-Object Name,AdapterRAM | ConvertTo-Json"],
                capture_output=True, text=True, timeout=8, **_hidden()).stdout
            data = json.loads(out or "[]")
            for item in data if isinstance(data, list) else [data]:
                name = str(item.get("Name") or "")
                if not name or any(s in name.lower() for s in _SKIP_ADAPTERS):
                    continue
                vram = float(item.get("AdapterRAM") or 0) / GB
                vendor, integrated = _classify(name, vram)
                gpus.append(GPU(name, vendor, vram, None, integrated))
        except Exception:  # noqa: BLE001
            log.info("PowerShell GPU query failed", exc_info=True)
    return gpus


def _nvidia_smi() -> list[GPU]:
    exe = shutil.which("nvidia-smi")
    if not exe:
        for cand in (r"C:\Windows\System32\nvidia-smi.exe", r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe"):
            if os.path.exists(cand):
                exe = cand
                break
    if not exe:
        return []
    try:
        out = subprocess.run([exe, "--query-gpu=name,memory.total,memory.free", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=8, **_hidden()).stdout
        found = []
        for line in out.strip().splitlines():
            name, total, free = [p.strip() for p in line.split(",")]
            found.append(GPU(name, "nvidia", float(total) / 1024, float(free) / 1024, False))
        return found
    except Exception:  # noqa: BLE001
        log.info("nvidia-smi failed", exc_info=True)
        return []


def detect_hardware() -> Hardware:
    logical = os.cpu_count() or 4
    total, avail = _ram()
    gpus = _windows_gpus() if os.name == "nt" else []
    smi = _nvidia_smi()
    if smi:   # accurate numbers for NVIDIA cards replace the registry guess
        gpus = [g for g in gpus if g.vendor != "nvidia"] + smi
    return Hardware(logical, max(1, _physical_cores(logical)), total, avail, gpus)


# --------------------------------------------------------------------------- tuning

def _pick_ctx(headroom_gb: float, cap: int) -> int:
    for ctx in (16384, 8192, 4096):
        if ctx <= cap and (ctx / 1024) * KV_GB_PER_1K_CTX <= headroom_gb * 0.8:
            return ctx
    return 4096   # floor: the agent prompt + retrieved passages need at least this


def _cpu_threads(cores: int) -> int:
    if cores <= 2:
        return max(1, cores)
    if cores <= 4:
        return cores - 1
    return min(8, cores - 2)       # always leave a couple of cores for Windows + the UI


def build_profile(hw: Hardware, chat_gb: float, embed_gb: float, mmproj_gb: float,
                  mode_override: str = "auto", force_gpu: bool = False) -> Profile:
    p = Profile()
    core = chat_gb + embed_gb
    gpu = None if mode_override == "cpu" else hw.best_gpu(allow_integrated=force_gpu)

    gpu_threads = max(2, min(4, hw.cpu_cores))
    if gpu:
        budget = gpu.usable_gb
        if gpu.integrated:
            budget = min(budget if gpu.vram_gb else 99, hw.ram_avail_gb * 0.5)
        need = core * 1.12 + 0.7
        p.gpu_name = gpu.name
        p.backends = {"nvidia": ("cuda", "vulkan"), "intel": ("vulkan", "sycl")}.get(gpu.vendor, ("vulkan",))
        if budget >= need + 0.3:
            left = budget - need
            p.mode, p.chat_gpu_layers, p.embed_gpu_layers = "gpu", 999, 999
            if mmproj_gb and left >= mmproj_gb * 1.1 + 0.4:
                p.mmproj_offload, left = True, left - mmproj_gb * 1.1
            p.chat_ctx = _pick_ctx(left, 16384)
            p.embed_ctx = 8192 if left >= 2 else 4096
            p.chat_threads, p.embed_threads = gpu_threads, min(2, gpu_threads)
            p.tier = "gpu-high" if left >= 6 else "gpu"
        elif budget >= need * 0.5:
            avail_chat = budget - 0.7 - embed_gb * 1.12 - KV_GB_PER_1K_CTX * 4
            frac = max(0.1, min(0.95, avail_chat / (chat_gb * 1.12)))
            p.mode, p.tier = "hybrid", "hybrid"
            p.chat_gpu_layers, p.embed_gpu_layers = max(1, int(frac * EST_CHAT_LAYERS)), 999
            p.chat_ctx, p.embed_ctx = 4096, 4096
            p.chat_threads, p.embed_threads = _cpu_threads(hw.cpu_cores), 2
            p.warnings.append(f"GPU memory is limited ({budget:.1f} GB usable); offloading only part of the model.")
        else:
            gpu = None
            p.warnings.append(f"{p.gpu_name or 'GPU'} has too little memory for the model; using the CPU instead.")

    if not gpu:
        p.mode, p.backends = "cpu", ("cpu",)
        p.chat_gpu_layers = p.embed_gpu_layers = 0
        reserve = max(2.5, hw.ram_total_gb * 0.35)
        usable = min(hw.ram_avail_gb, hw.ram_total_gb - reserve)
        left = usable - core * 1.1
        p.use_mmproj = bool(mmproj_gb) and left >= mmproj_gb * 1.1 + 0.8
        if p.use_mmproj:
            left -= mmproj_gb * 1.1
        p.chat_ctx = _pick_ctx(left, 8192)
        p.embed_ctx = 4096
        p.chat_threads = _cpu_threads(hw.cpu_cores)
        p.embed_threads = max(1, min(p.chat_threads, max(2, p.chat_threads // 2)))
        lite = hw.ram_total_gb < 10 or hw.cpu_cores <= 4
        p.tier = "cpu-lite" if lite else "cpu"
        if hw.ram_total_gb < 10:
            p.chat_ubatch = 256
        if left < 0.3:
            p.warnings.append(f"Only {hw.ram_total_gb:.0f} GB RAM: KORA will work but may be slow. Close other apps for best results.")

    if p.mode == "hybrid" and hw.ram_total_gb < 8:
        p.use_mmproj = False
    p.summary = (f"{p.tier} | {p.mode}{' on ' + p.gpu_name if p.gpu_name and p.mode != 'cpu' else ''} | ctx {p.chat_ctx} | "
                 f"threads {p.chat_threads} | gpu layers {p.chat_gpu_layers} | vision/audio {'on' if p.use_mmproj else 'off'}")
    return p
