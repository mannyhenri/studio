"""Studio patch: adds Intel Arc (XPU) support to SeedVR2 2.5.24."""
import re, sys, os
SRC, OUT = sys.argv[1], sys.argv[2]
TAG = "# [Studio XPU patch]"

def rd(p): return open(os.path.join(SRC, p), encoding="utf-8").read()
def wr(p, s):
    d = os.path.join(OUT, p); os.makedirs(os.path.dirname(d), exist_ok=True)
    open(d, "w", encoding="utf-8", newline="").write(s)
def sub1(s, old, new):
    assert s.count(old) == 1, ("expected once", old[:80], s.count(old)); return s.replace(old, new)
def tensors(s):
    # x.is_cuda or x.is_mps  ->  x.is_cuda or x.is_mps or x.is_xpu
    s = re.sub(r"(\b[\w\.\[\]0-9]+)\.is_cuda or \1\.is_mps\b", lambda m: f"{m.group(0)} or getattr({m.group(1)}, 'is_xpu', False)", s)
    return s

# ---- memory_manager.py
p = "optimization/memory_manager.py"; s = rd(p)
s = sub1(s, '''def is_cuda_available() -> bool:
    """Check if CUDA backend is available."""
    return torch.cuda.is_available()
''', '''def is_cuda_available() -> bool:
    """Check if CUDA backend is available."""
    return torch.cuda.is_available()


def is_xpu_available() -> bool:  ''' + TAG + '''
    """Check if Intel XPU (Arc) backend is available."""
    try:
        return hasattr(torch, 'xpu') and torch.xpu.is_available()
    except Exception:
        return False
''')
s = sub1(s, '''    if is_mps_available():
        return 'mps'
    return 'cpu'
''', '''    if is_mps_available():
        return 'mps'
    if is_xpu_available():  ''' + TAG + '''
        return 'xpu'
    return 'cpu'
''')
s = sub1(s, '''    try:
        if is_mps_available():
            devs.append("mps")  # MPS doesn't use device indices
            has_mps = True
    except Exception:
        pass
''', '''    try:
        if is_mps_available():
            devs.append("mps")  # MPS doesn't use device indices
            has_mps = True
    except Exception:
        pass

    try:  ''' + TAG + '''
        if not devs and is_xpu_available():
            devs += [f"xpu:{i}" for i in range(torch.xpu.device_count())]
    except Exception:
        pass
''')
s = sub1(s, '''            free_memory = mem.total - mem.used
            total_memory = mem.total
        else:
            return {"error": "No GPU backend available (CUDA/MPS)"}''', '''            free_memory = mem.total - mem.used
            total_memory = mem.total
        elif is_xpu_available():  ''' + TAG + '''
            if device is None:
                device = torch.device("xpu:0")
            elif not isinstance(device, torch.device):
                device = torch.device(device)
            try:
                free_memory, total_memory = torch.xpu.mem_get_info(device)
            except Exception:
                total_memory = torch.xpu.get_device_properties(device).total_memory
                free_memory = total_memory - torch.xpu.memory_reserved(device)
        else:
            return {"error": "No GPU backend available (CUDA/MPS/XPU)"}''')
s = sub1(s, '''    backend = "MPS" if is_mps_available() else "CUDA"
    print(''', '''    backend = "MPS" if is_mps_available() else ("CUDA" if is_cuda_available() else "XPU")  ''' + TAG + '''
    print(''')
s = sub1(s, '''            # MPS doesn't track peak separately
            return allocated, reserved, allocated, reserved
''', '''            # MPS doesn't track peak separately
            return allocated, reserved, allocated, reserved
        elif is_xpu_available():  ''' + TAG + '''
            if device is None:
                device = torch.device("xpu:0")
            elif not isinstance(device, torch.device):
                device = torch.device(device)
            return (torch.xpu.memory_allocated(device) / (1024**3), torch.xpu.memory_reserved(device) / (1024**3),
                    torch.xpu.max_memory_allocated(device) / (1024**3), torch.xpu.max_memory_reserved(device) / (1024**3))
''')
s = sub1(s, '''    elif is_mps_available():
        torch.mps.empty_cache()
    
    if debug:
        debug.end_timer(gpu_timer, "GPU cache clearing")''', '''    elif is_mps_available():
        torch.mps.empty_cache()
    elif is_xpu_available():  ''' + TAG + '''
        torch.xpu.empty_cache()
    
    if debug:
        debug.end_timer(gpu_timer, "GPU cache clearing")''')
s = sub1(s, '''            torch.cuda.reset_peak_memory_stats(device)
        # Note: MPS''', '''            torch.cuda.reset_peak_memory_stats(device)
        elif is_xpu_available():  ''' + TAG + '''
            torch.xpu.reset_peak_memory_stats(device if device is not None else torch.device("xpu:0"))
        # Note: MPS''')
s = s.replace("param.device.type in ['cuda', 'mps']", "param.device.type in ['cuda', 'mps', 'xpu']")
s = tensors(s)
wr(p, s)

# ---- video_upscaler.py, generation_phases.py: move results off the GPU on Intel too
for p in ("interfaces/video_upscaler.py", "core/generation_phases.py"):
    s = tensors(rd(p)); assert "is_xpu" in s, p; wr(p, s)

# ---- debug.py: show the Arc in the log and track its memory
p = "utils/debug.py"; s = rd(p)
s = sub1(s, '''        elif is_mps_available():
            gpu_str = "Apple Silicon (MPS)"
            cudnn_ver = "N/A"
''', '''        elif is_mps_available():
            gpu_str = "Apple Silicon (MPS)"
            cudnn_ver = "N/A"
        elif hasattr(torch, 'xpu') and torch.xpu.is_available():  ''' + TAG + '''
            try:
                props = torch.xpu.get_device_properties(0)
                gpu_str = f"{props.name} ({round(props.total_memory / (1024**3))}GB, XPU)"
            except Exception:
                gpu_str = "Intel XPU"
            cudnn_ver = "N/A"
''')
s = sub1(s, "        has_gpu = is_mps or is_cuda_available()\n",
         "        has_gpu = is_mps or is_cuda_available() or (hasattr(torch, 'xpu') and torch.xpu.is_available())  " + TAG + "\n")
s = sub1(s, "is_gpu = obj.is_cuda or (hasattr(obj, 'is_mps') and obj.is_mps)",
         "is_gpu = obj.is_cuda or (hasattr(obj, 'is_mps') and obj.is_mps) or getattr(obj, 'is_xpu', False)")
wr(p, s)
print("patched")
