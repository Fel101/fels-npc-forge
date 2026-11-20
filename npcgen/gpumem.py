"""Who holds GPU memory, per process, from Windows' own counters (the ones Task Manager and GPU-Z read).

Why: ComfyUI's /system_stats cannot see other processes on every system. On an AMD card under Windows (ROCm, 2026-09-24) it
read 0.8 GB in use while a Qwen3-TTS voice worker held 6.8 GB of the same card, so a check through ComfyUI alone would load
a model on top of another program's. These counters see every process: "\\GPU Process Memory(*)\\Dedicated Usage" has one
instance per process and adapter, named "pid_<pid>_luid_<adapter>_phys_<n>". Read through pdh.dll with ctypes, so NPC Forge
still needs no extra package.

Elsewhere (Linux, macOS) this returns None and the gate relies on ComfyUI's reading, which on CUDA covers the whole device.
"""
import ctypes
import os
import re
import subprocess

PDH_FMT_LARGE = 0x00000400
PDH_MORE_DATA = 0x800007D2
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_INSTANCE = re.compile(r"pid_(\d+)_luid_(0x[0-9a-fA-F]+_0x[0-9a-fA-F]+)_phys_\d+")


class _Value(ctypes.Structure):                     # PDH_FMT_COUNTERVALUE with the LONGLONG member of its union
    _fields_ = [("CStatus", ctypes.c_ulong), ("largeValue", ctypes.c_longlong)]


class _Item(ctypes.Structure):                      # PDH_FMT_COUNTERVALUE_ITEM_W
    _fields_ = [("szName", ctypes.c_wchar_p), ("FmtValue", _Value)]


def _pdh():
    pdh = ctypes.WinDLL("pdh.dll")
    handle = ctypes.c_void_p
    pdh.PdhOpenQueryW.argtypes = [ctypes.c_wchar_p, ctypes.c_size_t, ctypes.POINTER(handle)]
    pdh.PdhAddEnglishCounterW.argtypes = [handle, ctypes.c_wchar_p, ctypes.c_size_t, ctypes.POINTER(handle)]
    pdh.PdhCollectQueryData.argtypes = [handle]
    pdh.PdhGetFormattedCounterArrayW.argtypes = [handle, ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong),
                                                 ctypes.POINTER(ctypes.c_ulong), ctypes.c_void_p]
    pdh.PdhCloseQuery.argtypes = [handle]
    for fn in (pdh.PdhOpenQueryW, pdh.PdhAddEnglishCounterW, pdh.PdhCollectQueryData, pdh.PdhGetFormattedCounterArrayW,
               pdh.PdhCloseQuery):
        fn.restype = ctypes.c_ulong
    return pdh


def _counter(path):
    """{instance name: value} for a wildcard counter path, or None if it cannot be read."""
    pdh = _pdh()
    query, counter = ctypes.c_void_p(), ctypes.c_void_p()
    if pdh.PdhOpenQueryW(None, 0, ctypes.byref(query)) != 0:
        return None
    try:
        if pdh.PdhAddEnglishCounterW(query, path, 0, ctypes.byref(counter)) != 0:
            return None
        if pdh.PdhCollectQueryData(query) != 0:
            return None
        size, count = ctypes.c_ulong(0), ctypes.c_ulong(0)
        rc = pdh.PdhGetFormattedCounterArrayW(counter, PDH_FMT_LARGE, ctypes.byref(size), ctypes.byref(count), None)
        if rc not in (0, PDH_MORE_DATA) or not size.value:
            return {} if rc == 0 else None
        buf = ctypes.create_string_buffer(size.value)
        if pdh.PdhGetFormattedCounterArrayW(counter, PDH_FMT_LARGE, ctypes.byref(size), ctypes.byref(count), buf) != 0:
            return None
        items = ctypes.cast(buf, ctypes.POINTER(_Item * count.value)).contents
        return {item.szName: int(item.FmtValue.largeValue) for item in items}
    finally:
        pdh.PdhCloseQuery(query)


def process_name(pid):
    """The file name of a process ("LM Studio.exe"; for a Python, the end of its path and its pid), or "process <pid>"."""
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenProcess.restype = ctypes.c_void_p
        k32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        k32.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_wchar_p,
                                                   ctypes.POINTER(ctypes.c_ulong)]
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, int(pid))
        if h:
            try:
                buf, n = ctypes.create_unicode_buffer(1024), ctypes.c_ulong(1024)
                if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
                    parts = buf.value.replace("/", "\\").split("\\")
                    if parts[-1].lower().startswith("python"):       # a venv's python.exe runs the base one: add the pid
                        return f"{chr(92).join(parts[-4:])} (process {pid})"
                    return parts[-1]
            finally:
                k32.CloseHandle(h)
    except Exception:
        pass
    return f"process {pid}"


def processes(shared=False):
    """[{"pid", "adapter", "mb"}] of every process holding dedicated GPU memory, or None where this cannot be read. `adapter`
    is the card's id as adapters() spells it (lower case). With `shared`, the system memory a process uses through the GPU is
    added: what a GPU built into the processor mostly holds (its own dedicated memory is a small slice of RAM)."""
    if os.name != "nt":
        return None
    try:
        values = _counter(r"\GPU Process Memory(*)\Dedicated Usage")
        extra = _counter(r"\GPU Process Memory(*)\Shared Usage") if shared else {}
    except Exception:
        return None
    if values is None:
        return None
    totals = {}
    for table in (values, extra or {}):
        for name, value in table.items():
            m = _INSTANCE.fullmatch(name or "")
            if m and value > 0:
                key = (int(m.group(1)), m.group(2).lower())
                totals[key] = totals.get(key, 0) + value
    return [{"pid": pid, "adapter": adapter, "mb": value // 2 ** 20} for (pid, adapter), value in totals.items()]


# ---- the GPUs themselves (Windows' graphics kernel, gdi32.dll) ----------------------------------------------------------------
_KMT_REGISTRY_INFO, _KMT_SEGMENT_SIZE, _KMT_ADAPTER_TYPE = 8, 3, 15          # KMTQAITYPE_ADAPTERREGISTRYINFO, _GETSEGMENTSIZE, _ADAPTERTYPE
_TYPE_SOFTWARE, _TYPE_HYBRID_DISCRETE, _TYPE_HYBRID_INTEGRATED, _TYPE_INDIRECT = 1 << 2, 1 << 4, 1 << 5, 1 << 6
INTEGRATED_DEDICATED_MB = 4096        # without Windows' hybrid flags: a GPU with less memory of its own than this borrows the PC's RAM


class _Luid(ctypes.Structure):
    _fields_ = [("LowPart", ctypes.c_ulong), ("HighPart", ctypes.c_long)]


class _AdapterInfo(ctypes.Structure):                   # D3DKMT_ADAPTERINFO
    _fields_ = [("hAdapter", ctypes.c_uint), ("AdapterLuid", _Luid), ("NumOfSources", ctypes.c_ulong),
                ("bPrecisePresentRegionsPreferred", ctypes.c_int)]


class _EnumAdapters2(ctypes.Structure):                 # D3DKMT_ENUMADAPTERS2
    _fields_ = [("NumAdapters", ctypes.c_ulong), ("pAdapters", ctypes.POINTER(_AdapterInfo))]


class _QueryAdapterInfo(ctypes.Structure):              # D3DKMT_QUERYADAPTERINFO
    _fields_ = [("hAdapter", ctypes.c_uint), ("Type", ctypes.c_uint), ("pPrivateDriverData", ctypes.c_void_p),
                ("PrivateDriverDataSize", ctypes.c_uint)]


class _RegistryInfo(ctypes.Structure):                  # D3DKMT_ADAPTERREGISTRYINFO
    _fields_ = [("AdapterString", ctypes.c_wchar * 260), ("BiosString", ctypes.c_wchar * 260),
                ("DacType", ctypes.c_wchar * 260), ("ChipType", ctypes.c_wchar * 260)]


class _SegmentSize(ctypes.Structure):                   # D3DKMT_SEGMENTSIZEINFO
    _fields_ = [("DedicatedVideoMemorySize", ctypes.c_ulonglong), ("DedicatedSystemMemorySize", ctypes.c_ulonglong),
                ("SharedSystemMemorySize", ctypes.c_ulonglong)]


def adapters():
    """[{"luid", "name", "dedicated_mb", "integrated"}] of this PC's real GPUs, or None where this cannot be read. `luid` is
    the id processes() gives each row ("0x00000000_0x0000f97f"). `integrated` is a GPU built into the processor: Windows
    marks it HybridIntegrated (and the other card HybridDiscrete) when a PC has both; otherwise a GPU with little memory of
    its own counts as one. Software renderers and remote-display adapters are left out."""
    if os.name != "nt":
        return None
    try:
        gdi = ctypes.WinDLL("gdi32")
        enum = _EnumAdapters2()
        if gdi.D3DKMTEnumAdapters2(ctypes.byref(enum)) != 0 or not enum.NumAdapters:
            return None
        table = (_AdapterInfo * enum.NumAdapters)()
        enum.pAdapters = table
        if gdi.D3DKMTEnumAdapters2(ctypes.byref(enum)) != 0:
            return None
        out = []
        for info in table[:enum.NumAdapters]:
            try:
                reg, seg, kind = _RegistryInfo(), _SegmentSize(), ctypes.c_uint(0)
                named = _query_adapter(gdi, info.hAdapter, _KMT_REGISTRY_INFO, reg) and reg.AdapterString.strip()
                sized = _query_adapter(gdi, info.hAdapter, _KMT_SEGMENT_SIZE, seg)
                typed = _query_adapter(gdi, info.hAdapter, _KMT_ADAPTER_TYPE, kind)
            finally:
                gdi.D3DKMTCloseAdapter(ctypes.byref(ctypes.c_uint(info.hAdapter)))
            flags = kind.value if typed else 0
            if not named or flags & (_TYPE_SOFTWARE | _TYPE_INDIRECT):
                continue
            dedicated = int(seg.DedicatedVideoMemorySize // 2 ** 20) if sized else None
            if flags & (_TYPE_HYBRID_INTEGRATED | _TYPE_HYBRID_DISCRETE):
                integrated = bool(flags & _TYPE_HYBRID_INTEGRATED)
            else:
                integrated = dedicated is not None and dedicated < INTEGRATED_DEDICATED_MB
            luid = f"0x{info.AdapterLuid.HighPart & 0xFFFFFFFF:08x}_0x{info.AdapterLuid.LowPart & 0xFFFFFFFF:08x}"
            out.append({"luid": luid, "name": reg.AdapterString.strip(), "dedicated_mb": dedicated, "integrated": integrated})
        return out
    except Exception:
        return None


def _query_adapter(gdi, handle, kind, struct):
    query = _QueryAdapterInfo(handle, kind, ctypes.cast(ctypes.byref(struct), ctypes.c_void_p), ctypes.sizeof(struct))
    return gdi.D3DKMTQueryAdapterInfo(ctypes.byref(query)) == 0


class _ProcessEntry(ctypes.Structure):                  # PROCESSENTRY32W
    _fields_ = [("dwSize", ctypes.c_ulong), ("cntUsage", ctypes.c_ulong), ("th32ProcessID", ctypes.c_ulong),
                ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", ctypes.c_ulong), ("cntThreads", ctypes.c_ulong),
                ("th32ParentProcessID", ctypes.c_ulong), ("pcPriClassBase", ctypes.c_long), ("dwFlags", ctypes.c_ulong),
                ("szExeFile", ctypes.c_wchar * 260)]


def descendants(pid):
    """The pids of the processes `pid` started and the ones those started (a venv's python.exe starts the real Python), from
    a snapshot of Windows' process list; an empty set where this cannot be read. Windows reuses the pids of ended processes,
    so a process counts only if it was started after the one it names as its parent."""
    if os.name != "nt":
        return set()
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
        k32.CreateToolhelp32Snapshot.argtypes = [ctypes.c_ulong, ctypes.c_ulong]
        k32.Process32FirstW.argtypes = k32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(_ProcessEntry)]
        k32.OpenProcess.restype = ctypes.c_void_p
        k32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        k32.GetProcessTimes.argtypes = [ctypes.c_void_p] + [ctypes.POINTER(ctypes.c_ulonglong)] * 4
        k32.CloseHandle.argtypes = [ctypes.c_void_p]
        snap = k32.CreateToolhelp32Snapshot(0x2, 0)                      # TH32CS_SNAPPROCESS
        if snap in (None, ctypes.c_void_p(-1).value):
            return set()
        parents = {}
        try:
            entry = _ProcessEntry(dwSize=ctypes.sizeof(_ProcessEntry))
            ok = k32.Process32FirstW(snap, ctypes.byref(entry))
            while ok:
                parents[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
                ok = k32.Process32NextW(snap, ctypes.byref(entry))
        finally:
            k32.CloseHandle(snap)

        def created(p):
            h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, p)
            if not h:
                return None
            try:
                times = [ctypes.c_ulonglong() for _ in range(4)]
                return times[0].value if k32.GetProcessTimes(h, *(ctypes.byref(t) for t in times)) else None
            finally:
                k32.CloseHandle(h)
        return _tree(parents, int(pid), created)
    except Exception:
        return set()


def _tree(parents, root, created):
    """Every pid below `root` in {pid: parent pid}, a child counting only when created(child) >= created(parent) (both known)."""
    times, out, frontier = {}, set(), {root}

    def when(p):
        if p not in times:
            times[p] = created(p)
        return times[p]
    while frontier:
        found = {child for child, parent in parents.items()
                 if parent in frontier and child != parent and child != root and child not in out
                 and when(child) is not None and when(parent) is not None and when(child) >= when(parent)}
        out |= found
        frontier = found
    return out


def listening_pid(port):
    """The process listening on a local TCP port (ComfyUI's), or None. Windows only (netstat -ano)."""
    if os.name != "nt" or not port:
        return None
    try:
        text = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True, text=True, timeout=10,
                              creationflags=0x08000000).stdout
    except Exception:
        return None
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 5 and parts[3].upper() == "LISTENING" and parts[1].rsplit(":", 1)[-1] == str(port):
            try:
                return int(parts[4])
            except ValueError:
                return None
    return None
