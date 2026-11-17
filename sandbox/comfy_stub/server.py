"""Stand-in for ComfyUI's server module: PromptServer and its queue, as far as custom nodes see them.

The real HTTP routes, validation and the render loop live in sandbox/fake_comfy.py; this module only holds the objects a
custom node pack reaches through ``from server import PromptServer``."""
import asyncio
import copy
import heapq
import json
import threading
import uuid

from aiohttp import web


class PromptQueue:
    """The same shape as ComfyUI's queue: items are (number, prompt_id, prompt, extra_data, outputs_to_execute, sensitive)."""

    def __init__(self, server):
        self.server = server
        self.mutex = threading.RLock()
        self.not_empty = threading.Condition(self.mutex)
        self.queue = []
        self.currently_running = {}
        self.history = {}
        self.flags = {}
        self._task_counter = 0

    def put(self, item):
        with self.mutex:
            heapq.heappush(self.queue, item)
            self.server.queue_updated()
            self.not_empty.notify()

    def take(self):
        """The next item to run, or None (fake_comfy's render loop polls this)."""
        with self.mutex:
            if not self.queue:
                return None, None
            item = heapq.heappop(self.queue)
            i = self._task_counter
            self.currently_running[i] = copy.deepcopy(item)
            self._task_counter += 1
            self.server.queue_updated()
            return item, i

    def task_done(self, item_id, history_result, status):
        with self.mutex:
            prompt = self.currently_running.pop(item_id)
            self.history[prompt[1]] = {"prompt": list(prompt[:5]), "outputs": {}, "status": status}
            self.history[prompt[1]].update(history_result)
            self.server.queue_updated()

    def get_current_queue(self):
        with self.mutex:
            return list(self.currently_running.values()), copy.deepcopy(self.queue)

    def get_current_queue_volatile(self):
        with self.mutex:
            return list(self.currently_running.values()), copy.copy(self.queue)

    def get_tasks_remaining(self):
        with self.mutex:
            return len(self.queue) + len(self.currently_running)

    def wipe_queue(self):
        with self.mutex:
            self.queue = []
            self.server.queue_updated()

    def delete_queue_item(self, function):
        with self.mutex:
            for x in range(len(self.queue)):
                if function(self.queue[x]):
                    self.queue.pop(x)
                    heapq.heapify(self.queue)
                    self.server.queue_updated()
                    return True
        return False

    def interrupt_if_running(self, prompt_id):
        with self.mutex:
            for item in self.currently_running.values():
                if item[1] == prompt_id:
                    self.server.interrupted.add(prompt_id)
                    return True
        return False

    def get_history(self, prompt_id=None, max_items=None, offset=-1):
        with self.mutex:
            if prompt_id is None:
                items = list(self.history.items())
                if offset < 0 and max_items is not None:
                    offset = len(items) - max_items
                items = items[max(0, offset):]
                if max_items is not None:
                    items = items[:max_items]
                return copy.deepcopy(dict(items))
            if prompt_id in self.history:
                return {prompt_id: copy.deepcopy(self.history[prompt_id])}
            return {}

    def wipe_history(self):
        with self.mutex:
            self.history = {}

    def delete_history_item(self, id_to_delete):
        with self.mutex:
            self.history.pop(id_to_delete, None)

    def set_flag(self, name, data):
        with self.mutex:
            self.flags[name] = data

    def get_flags(self, reset=True):
        with self.mutex:
            f = self.flags
            if reset:
                self.flags = {}
            return copy.copy(f)


class PromptServer:
    instance = None

    def __init__(self, loop):
        PromptServer.instance = self
        self.loop = loop
        self.routes = web.RouteTableDef()
        self.app = web.Application(client_max_size=1024 ** 3, middlewares=[])
        self.prompt_queue = PromptQueue(self)
        self.sockets = {}
        self.client_id = None
        self.number = 0
        self.port = 8188
        self.address = "127.0.0.1"
        self.last_node_id = None
        self.last_prompt_id = None
        self.interrupted = set()
        self.on_prompt_handlers = []
        self.supports = ["custom_nodes_from_web"]

    # ---- messages to browsers (same JSON as ComfyUI: {"type", "data"})
    async def send_json(self, event, data, sid=None):
        msg = json.dumps({"type": event, "data": data})
        targets = [self.sockets[sid]] if sid in self.sockets else ([] if sid else list(self.sockets.values()))
        for ws in targets:
            try:
                await ws.send_str(msg)
            except Exception:
                pass

    async def send_bytes(self, event, data, sid=None):
        payload = event.to_bytes(4, "big") + data
        targets = [self.sockets[sid]] if sid in self.sockets else ([] if sid else list(self.sockets.values()))
        for ws in targets:
            try:
                await ws.send_bytes(payload)
            except Exception:
                pass

    def send_sync(self, event, data, sid=None):
        coro = self.send_bytes(event, data, sid) if isinstance(data, (bytes, bytearray)) else self.send_json(event, data, sid)
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is self.loop:
            self.loop.create_task(coro)
        else:
            asyncio.run_coroutine_threadsafe(coro, self.loop)

    def get_queue_info(self):
        return {"exec_info": {"queue_remaining": self.prompt_queue.get_tasks_remaining()}}

    def queue_updated(self):
        try:
            self.send_sync("status", {"status": self.get_queue_info()})
        except Exception:
            pass

    def add_on_prompt_handler(self, handler):
        self.on_prompt_handlers.append(handler)

    def trigger_on_prompt(self, json_data):
        for handler in self.on_prompt_handlers:
            json_data = handler(json_data)
        return json_data

    @staticmethod
    def new_id():
        return str(uuid.uuid4())
