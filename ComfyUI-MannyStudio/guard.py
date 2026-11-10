"""Render guard for Studio.

- Keeps Windows awake while the ComfyUI queue has work (sleep mid-render freezes the Arc job).
- Frees memory between jobs when the next job uses different model files (shared-memory GPUs
  otherwise pile models up until Windows starts paging).
- Tracks progress of the running job, so Studio can say "no progress for 12 min" (stalled).
- Reads Windows sleep / wake / crash events, to explain past freezes.
"""
import json
import os
import subprocess
import threading
import time

from aiohttp import web

H = {}
NO_WINDOW = 0x08000000 if os.name == "nt" else 0
MODEL_KEYS = ("unet_name", "ckpt_name", "clip_name", "clip_name1", "clip_name2", "vae_name", "model_name", "lora_name")
STATE = {"prompt": None, "started": 0.0, "last": 0.0, "node": None, "node_type": None, "step": None, "max": None,
         "models": [], "awake": False, "freed": 0, "last_free": None,
         "step_at": 0.0, "step_len": None, "range": None, "phase_start": 0.0}
SAMPLERS = ("KSampler", "KSamplerAdvanced", "SamplerCustom", "SamplerCustomAdvanced")
DEFAULTS = {"free_between_models": True, "keep_awake": True, "stall_minutes": 10}


def _prefs_file():
    return os.path.join(H["_data_dir"](), "guard.json")


def prefs():
    p = dict(DEFAULTS)
    try:
        with open(_prefs_file(), encoding="utf-8") as f:
            p.update({k: v for k, v in json.load(f).items() if k in DEFAULTS})
    except Exception:
        pass
    return p


def _models_of(prompt):
    out = set()
    for n in (prompt or {}).values():
        for k, v in ((n or {}).get("inputs") or {}).items():
            if k in MODEL_KEYS and isinstance(v, str):
                out.add(v)
    return out


def _queue():
    return H["PromptServer"].instance.prompt_queue


def _running_prompt(pid):
    try:
        for item in _queue().currently_running.values():
            if item[1] == pid:
                return item[2]
    except Exception:
        pass
    return None


def _next_prompt():
    try:
        q = _queue()
        with q.mutex:
            items = sorted(q.queue, key=lambda x: x[0])
        return items[0][2] if items else None
    except Exception:
        return None


def _on_event(event, data):
    now = time.time()
    data = data if isinstance(data, dict) else {}
    if event == "execution_start":
        pid = data.get("prompt_id")
        STATE.update(prompt=pid, started=now, last=now, node=None, node_type=None, step=None, max=None,
                     models=sorted(_models_of(_running_prompt(pid))), step_at=0.0, step_len=None, range=None)
    elif event in ("executing", "progress", "executed", "execution_cached", "progress_state"):
        STATE["last"] = now
        if event == "progress":
            v, mx = data.get("value"), data.get("max")
            if v != STATE["step"]:
                if STATE["step"] is not None and v and STATE["step_at"]:
                    STATE["step_len"] = now - STATE["step_at"]   # how long the last step took
                STATE["step_at"] = now
            STATE.update(step=v, max=mx)
        if event == "executing":
            node = data.get("node")
            STATE["node"] = node
            STATE["step"] = STATE["max"] = None
            if node is None:
                _done()
            else:
                try:
                    p = _running_prompt(data.get("prompt_id")) or {}
                    n = p.get(str(node)) or {}
                    STATE["node_type"] = n.get("class_type")
                    if STATE["node_type"] in SAMPLERS:
                        STATE.update(range=_sampler_range(n.get("inputs") or {}), phase_start=now, step_at=now)
                except Exception:
                    pass
    elif event in ("execution_success", "execution_error", "execution_interrupted"):
        _done()


def _done():
    """A job just ended: free memory if the next job needs different models (or none is waiting)."""
    if STATE["prompt"] is None:
        return
    cur = set(STATE["models"])
    STATE.update(prompt=None, node=None, node_type=None, step=None, max=None)
    if not prefs()["free_between_models"]:
        return
    nxt = _next_prompt()
    need = _models_of(nxt) if nxt else set()
    if nxt is None or not cur or not need or cur - need:
        try:
            q = _queue()
            q.set_flag("unload_models", True)
            q.set_flag("free_memory", True)
            STATE["freed"] += 1
            STATE["last_free"] = time.time()
        except Exception:
            pass


def _patch_send():
    ps = H["PromptServer"].instance
    if getattr(ps, "_studio_guard", False):
        return
    orig = ps.send_sync

    def send_sync(event, data, sid=None):
        try:
            _on_event(event, data)
        except Exception:
            pass
        return orig(event, data, sid)
    ps.send_sync = send_sync
    ps._studio_guard = True


def _awake_loop():
    """Windows: ask the system to stay awake while there is work. The flag lives on this thread."""
    if os.name != "nt":
        return
    import ctypes
    ES_CONTINUOUS, ES_SYSTEM_REQUIRED, ES_AWAYMODE_REQUIRED = 0x80000000, 0x00000001, 0x00000040
    k32 = ctypes.windll.kernel32
    while True:
        try:
            busy = _queue().get_tasks_remaining() > 0 and prefs()["keep_awake"]
            if busy:
                k32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED)
            elif STATE["awake"]:
                k32.SetThreadExecutionState(ES_CONTINUOUS)
            STATE["awake"] = busy
        except Exception:
            pass
        time.sleep(5)


def _sampler_range(ins):
    """KSamplerAdvanced runs only part of a schedule (Wan 2.2: steps 0-2 on one model, 2-4 on the other).
    Returns (start, end, total) so progress can be shown for the whole shot, not per half."""
    try:
        total = int(ins.get("steps")) if not isinstance(ins.get("steps"), list) else None
        if total and "start_at_step" in ins:
            a = ins.get("start_at_step"); b = ins.get("end_at_step")
            a = int(a) if not isinstance(a, list) else 0
            b = min(int(b), total) if not isinstance(b, list) else total
            return (a, b, total)
    except Exception:
        pass
    return None


def _overall():
    """0..1 for the running job, moving smoothly between steps using how long the last step took."""
    step, mx = STATE["step"], STATE["max"]
    if not mx:
        return None, None
    now = time.time()
    frac = 0.0
    ln = STATE["step_len"]
    since = now - (STATE["step_at"] or now)
    if ln and ln > 0:
        frac = min(0.95, since / ln)
    done_steps = min(mx, (step or 0) + (frac if (step or 0) < mx else 0))
    rng = STATE["range"]
    if rng and rng[1] - rng[0] == mx:
        a, b, total = rng
        overall = (a + done_steps) / total
        left_steps = total - a - done_steps
    else:
        overall = done_steps / mx
        left_steps = mx - done_steps
    eta = int(left_steps * ln) if ln else None
    return max(0.0, min(0.99, overall)), eta


def job_status():
    if STATE["prompt"] is None:
        return None
    now = time.time()
    idle = now - (STATE["last"] or now)
    overall, eta = _overall()
    rng = STATE["range"]
    return {"prompt_id": STATE["prompt"], "node_type": STATE["node_type"], "step": STATE["step"], "max": STATE["max"],
            "overall": overall, "eta_s": eta, "step_s": int(STATE["step_len"]) if STATE["step_len"] else None,
            "phase": (f"steps {rng[0] + 1}-{rng[1]} of {rng[2]}" if rng and rng[0] > 0 or (rng and rng[1] < rng[2]) else None),
            "running_s": int(now - STATE["started"]), "idle_s": int(idle),
            "stalled": idle > prefs()["stall_minutes"] * 60}


POWER_PS = r"""
$since = (Get-Date).AddHours(-HOURS)
$sys = Get-WinEvent -FilterHashtable @{LogName='System'; StartTime=$since} -ErrorAction SilentlyContinue |
  Where-Object { ($_.ProviderName -eq 'Microsoft-Windows-Kernel-Power' -and $_.Id -in 41,42,105,107,109,506,507,566) -or
                 ($_.ProviderName -eq 'Microsoft-Windows-Power-Troubleshooter' -and $_.Id -eq 1) -or
                 ($_.ProviderName -eq 'Display') -or
                 ($_.ProviderName -eq 'EventLog' -and $_.Id -in 6006,6008) -or
                 ($_.ProviderName -eq 'Microsoft-Windows-WER-SystemErrorReporting') -or
                 ($_.ProviderName -eq 'Microsoft-Windows-WHEA-Logger') -or
                 ($_.ProviderName -eq 'Microsoft-Windows-Resource-Exhaustion-Detector') -or
                 ($_.ProviderName -eq 'Application Popup' -and $_.Id -eq 26) -or
                 ($_.ProviderName -like '*igfx*' -or $_.ProviderName -like '*Intel*Graphics*' -or $_.ProviderName -like '*dxgkrnl*') }
$app = Get-WinEvent -FilterHashtable @{LogName='Application'; StartTime=$since; ProviderName='Windows Error Reporting'} -ErrorAction SilentlyContinue |
  Where-Object { $_.Message -match 'LiveKernelEvent|BlueScreen|python|ComfyUI' }
$out = @($sys) + @($app) | Sort-Object TimeCreated -Descending | Select-Object -First 80 | ForEach-Object {
  $m = ($_.Message -replace "\s+", ' ').Trim(); if ($m.Length -gt 300) { $m = $m.Substring(0,300) }
  $extra = ''; if ($_.ProviderName -eq 'Microsoft-Windows-Kernel-Power' -and $_.Id -eq 41) { $extra = ' [BugcheckCode=' + $_.Properties[0].Value + ']' }
  [pscustomobject]@{ t = $_.TimeCreated.ToString('yyyy-MM-dd HH:mm:ss'); id = $_.Id; src = $_.ProviderName; msg = $m + $extra } }
$dumps = Get-ChildItem 'C:\Windows\LiveKernelReports','C:\Windows\Minidump' -Recurse -File -ErrorAction SilentlyContinue | Where-Object { $_.LastWriteTime -gt $since } |
  ForEach-Object { [pscustomobject]@{ t = $_.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss'); id = 0; src = 'dump file'; msg = $_.FullName } }
@($out) + @($dumps) | ConvertTo-Json -Compress
"""


def setup(ns):
    H.update(ns)
    routes = ns["routes"]
    try:
        _patch_send()
    except Exception as e:
        print("[Studio] guard: couldn't watch progress:", e)
    threading.Thread(target=_awake_loop, name="studio-awake", daemon=True).start()

    @routes.get("/manny/api/guard")
    async def guard_status(request):
        return web.json_response({"prefs": prefs(), "job": job_status(), "awake": STATE["awake"], "freed": STATE["freed"],
                                  "last_free": STATE["last_free"]})

    @routes.post("/manny/api/guard/prefs")
    async def guard_prefs(request):
        b = await request.json()
        p = prefs()
        for k, v in b.items():
            if k in DEFAULTS:
                p[k] = int(v) if k == "stall_minutes" else bool(v)
        with open(_prefs_file(), "w", encoding="utf-8") as f:
            json.dump(p, f, indent=1)
        return web.json_response(p)

    @routes.post("/manny/api/guard/free")
    async def guard_free(request):
        q = _queue()
        q.set_flag("unload_models", True)
        q.set_flag("free_memory", True)
        return web.json_response({"ok": True})

    @routes.get("/manny/api/diag/power")
    async def diag_power(request):
        """Sleep, wake, unexpected shutdowns, low-memory and graphics-driver events from the Windows log."""
        if os.name != "nt":
            return web.json_response([])
        hours = max(1, min(72, int(request.query.get("hours", "12"))))
        import asyncio

        def run():
            r = subprocess.run(["powershell", "-NoProfile", "-Command", POWER_PS.replace("HOURS", str(hours))],
                               capture_output=True, timeout=60, creationflags=NO_WINDOW)
            txt = (r.stdout or b"").decode("utf-8", "ignore").strip()
            if not txt:
                return []
            d = json.loads(txt)
            return d if isinstance(d, list) else [d]
        try:
            return web.json_response(await asyncio.to_thread(run))
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)
