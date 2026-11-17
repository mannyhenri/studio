"""The stand-in's result files: kind, size and length read from the job's own graph."""
import os
import subprocess

import results


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration",
                          "-of", "default=nw=1", path], capture_output=True, text=True).stdout
    return dict(line.split("=", 1) for line in out.split() if "=" in line), out


def dirs(tmp_path):
    d = {k: str(tmp_path / k) for k in ("input", "output", "temp")}
    for v in d.values():
        os.makedirs(v, exist_ok=True)
    return d


def test_picture_size_from_a_math_node(tmp_path):
    p = {"1": {"class_type": "ComfyMathExpression", "inputs": {"expression": "a * 2", "values.a": 512}},
         "2": {"class_type": "EmptySD3LatentImage", "inputs": {"width": ["1", 1], "height": 768, "batch_size": 1}},
         "3": {"class_type": "VAEDecode", "inputs": {"samples": ["2", 0]}},
         "4": {"class_type": "StringConcatenate", "inputs": {"string_a": "Images", "delimiter": "/", "string_b": "Test"}},
         "5": {"class_type": "SaveImage", "inputs": {"images": ["3", 0], "filename_prefix": ["4", 0]}}}
    ui = results.make_output(p, "5", dirs(tmp_path))
    f = ui["images"][0]
    assert f["subfolder"] == "Images" and f["filename"] == "Test_00001_.png" and f["type"] == "output"
    from PIL import Image
    with Image.open(tmp_path / "output" / "Images" / f["filename"]) as im:
        assert im.size == (1024, 768) and im.getpixel((5, 5)) == (128, 128, 128)


def test_video_length_and_latent_upsampler(tmp_path):
    p = {"1": {"class_type": "EmptyLTXVLatentVideo", "inputs": {"width": 640, "height": 352, "length": 121, "batch_size": 1}},
         "2": {"class_type": "LTXVLatentUpsampler", "inputs": {"samples": ["1", 0]}},
         "3": {"class_type": "VAEDecode", "inputs": {"samples": ["2", 0]}},
         "4": {"class_type": "CreateVideo", "inputs": {"images": ["3", 0], "fps": 24}},
         "5": {"class_type": "SaveVideo", "inputs": {"video": ["4", 0], "filename_prefix": "Videos/shot"}}}
    ui = results.make_output(p, "5", dirs(tmp_path))
    assert ui["animated"] == [True]
    info, raw = probe(str(tmp_path / "output" / "Videos" / ui["images"][0]["filename"]))
    assert "width=1280" in raw and "height=704" in raw and "codec_type=audio" in raw
    assert abs(float(info["duration"]) - 121 / 24) < 0.15


def test_upscale_follows_the_resize_node(tmp_path):
    d = dirs(tmp_path)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "color=gray:s=1280x720:d=1", "-y",
                    os.path.join(d["input"], "clip.mp4")], check=True)
    p = {"1": {"class_type": "LoadVideo", "inputs": {"file": "clip.mp4"}},
         "2": {"class_type": "ResizeImageMaskNode", "inputs": {"input": ["1", 0], "resize_type": "scale shorter dimension",
                                                                "resize_type.shorter_size": 1080, "scale_method": "lanczos"}},
         "3": {"class_type": "SeedVR2Preprocess", "inputs": {"resized_images": ["2", 0]}},
         "4": {"class_type": "SaveVideo", "inputs": {"video": ["3", 0], "filename_prefix": "Videos/up"}}}
    assert results.size_of(p, "4", d["input"], video=True) == (1920, 1080)


def test_sound_and_text(tmp_path):
    p = {"1": {"class_type": "EmptyAceStepLatentAudio", "inputs": {"seconds": 3.0}},
         "2": {"class_type": "SaveAudio", "inputs": {"audio": ["1", 0], "filename_prefix": "Audio/song"}}}
    ui = results.make_output(p, "2", dirs(tmp_path))
    info, _raw = probe(str(tmp_path / "output" / "Audio" / ui["audio"][0]["filename"]))
    assert abs(float(info["duration"]) - 3.0) < 0.1
    t = {"1": {"class_type": "TextGenerateV2", "inputs": {"prompt": 'Idea: "a night market"\nNumber of shots: exactly 4\n"shots"'}},
         "2": {"class_type": "PreviewAny", "inputs": {"source": ["1", 0]}}}
    import json
    plan = json.loads(results.make_output(t, "2", dirs(tmp_path))["text"][0])
    assert len(plan["shots"]) == 4 and plan["title"]
