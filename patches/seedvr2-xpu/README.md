# SeedVR2 on Intel Arc (XPU)

`seedvr2_videoupscaler` 2.5.24 only knows CUDA and Apple MPS. These four files add XPU: device list, memory info, cache clearing and moving results off the GPU.

Apply: `python apply.py <pack>/src <out>` writes patched copies, or copy the four `.py` files over the pack's `src/` (keep the originals). Updating the pack in the Manager overwrites the patch.
