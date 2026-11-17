"""Stand-in for comfy.model_management: a CPU with 16 GB, nothing loaded."""
TOTAL = 16 * 1024 ** 3


def get_torch_device():
    return "cpu"


def get_total_memory(dev=None, torch_total_too=False):
    return (TOTAL, TOTAL) if torch_total_too else TOTAL


def get_free_memory(dev=None, torch_free_too=False):
    return (TOTAL, TOTAL) if torch_free_too else TOTAL


def unload_all_models():
    pass


def soft_empty_cache(force=False):
    pass


def interrupt_current_processing(value=True):
    pass


def processing_interrupted():
    return False
