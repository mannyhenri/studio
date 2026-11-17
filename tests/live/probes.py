"""Small real input files (PNG, MP4, WAV) the remote tests upload to the workers."""
import os


def png64():
    """A valid 64x64 grey PNG, no imaging library needed."""
    import struct, zlib

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    raw = b"".join(b"\x00" + b"\x80" * (64 * 3) for _ in range(64))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 64, 64, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def make_inputs(dirs):
    d = os.path.join(dirs["input"], "studio_test")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "probe.png"), "wb") as f:
        f.write(png64())
    with open(os.path.join(d, "in.png"), "wb") as f:
        f.write(png64())
    import subprocess, wave
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=128x128:rate=24:duration=1",
                    "-pix_fmt", "yuv420p", os.path.join(d, "probe.mp4")], check=True)
    with wave.open(os.path.join(d, "probe.wav"), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b"\0\0" * 16000)
