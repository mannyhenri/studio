"""The stand-in's prompt validation against ComfyUI 0.45's real node definitions."""
import json
import os

import pytest
import validate

HERE = os.path.dirname(os.path.abspath(__file__))
OI_PATH = os.path.join(HERE, "..", "..", "sandbox", "object_info.json")


@pytest.fixture(scope="module")
def oi():
    with open(OI_PATH, encoding="utf-8") as f:
        d = json.load(f)
    d["UNETLoader"]["input"]["required"]["unet_name"][0] = ["z_image_turbo_int8_convrot.safetensors"]
    return d


def picture(unet="z_image_turbo_int8_convrot.safetensors", width=1024):
    return {"1": {"class_type": "UNETLoader", "inputs": {"unet_name": unet, "weight_dtype": "default"}},
            "2": {"class_type": "EmptySD3LatentImage", "inputs": {"width": width, "height": 1024, "batch_size": 1}},
            "3": {"class_type": "VAELoader", "inputs": {"vae_name": "pixel_space"}},
            "4": {"class_type": "VAEDecode", "inputs": {"samples": ["2", 0], "vae": ["3", 0]}},
            "5": {"class_type": "SaveImage", "inputs": {"images": ["4", 0], "filename_prefix": "Images/test"}},
            "6": {"class_type": "ModelSamplingAuraFlow", "inputs": {"model": ["1", 0], "shift": 3.0}},
            "7": {"class_type": "PreviewAny", "inputs": {"source": ["6", 0]}}}


def test_a_valid_job_passes(oi):
    ok, err, good, node_errors = validate.validate_prompt(picture(), oi)
    assert ok and err is None and sorted(good) == ["5", "7"] and node_errors == {}


def test_a_missing_model_is_value_not_in_list(oi):
    # like ComfyUI, the outputs that pass still run (5); the one that needs the model (7) is reported
    ok, err, good, node_errors = validate.validate_prompt(picture(unet="nope.safetensors"), oi)
    assert ok and good == ["5"]
    e = node_errors["1"]["errors"][0]
    assert e["type"] == "value_not_in_list" and e["message"] == "Value not in list"
    assert e["details"] == "unet_name: 'nope.safetensors' not in ['z_image_turbo_int8_convrot.safetensors']"
    assert node_errors["1"]["dependent_outputs"] == ["7"]
    only = {k: v for k, v in picture(unet="nope.safetensors").items() if k in ("1", "6", "7")}
    ok, err, good, node_errors = validate.validate_prompt(only, oi)       # nothing left to run: refused
    assert not ok and good == [] and err["type"] == "prompt_outputs_failed_validation" and err["message"] == "Prompt outputs failed validation"
    assert err["details"].startswith("Value not in list: unet_name: 'nope.safetensors'")


def test_an_unknown_node_is_missing_node_type(oi):
    p = picture()
    p["8"] = {"class_type": "NoSuchNode", "inputs": {}}
    ok, err, _g, _n = validate.validate_prompt(p, oi)
    assert not ok and err["type"] == "missing_node_type" and "NoSuchNode" in err["message"]


def test_no_output_node(oi):
    ok, err, _g, _n = validate.validate_prompt({"1": picture()["2"]}, oi)
    assert not ok and err["type"] == "prompt_no_outputs"


def test_range_missing_input_and_bad_link(oi):
    p = picture(width=99999)
    ok, _e, good, ne = validate.validate_prompt(p, oi)
    assert good == ["7"] and ne["2"]["errors"][0]["type"] == "value_bigger_than_max"
    p = picture()
    del p["2"]["inputs"]["height"]
    ok, _e, _g, ne = validate.validate_prompt(p, oi)
    assert ne["2"]["errors"][0] == {"type": "required_input_missing", "message": "Required input is missing", "details": "height",
                                    "extra_info": {"input_name": "height"}}
    p = picture()
    p["4"]["inputs"]["samples"] = ["99", 0]
    ok, _e, _g, ne = validate.validate_prompt(p, oi)
    assert ne["4"]["errors"][0]["type"] == "bad_linked_input"


def test_type_mismatch(oi):
    p = picture()
    p["4"]["inputs"]["samples"] = ["3", 0]          # a VAE into a LATENT input
    ok, _e, good, ne = validate.validate_prompt(p, oi)
    assert good == ["7"] and ne["4"]["errors"][0]["type"] == "return_type_mismatch"


def test_dynamic_inputs(oi):
    p = picture()
    p["9"] = {"class_type": "ComfyMathExpression", "inputs": {"expression": "a * 2", "values.a": 512}}
    p["2"]["inputs"]["width"] = ["9", 1]
    assert validate.validate_prompt(p, oi)[0]
    del p["9"]["inputs"]["values.a"]                 # autogrow needs at least one value
    ok, _e, good, ne = validate.validate_prompt(p, oi)
    assert good == ["7"] and ne["9"]["errors"][0]["type"] == "required_input_missing"


def test_custom_combo_and_file_loaders(oi):
    p = picture()
    p["10"] = {"class_type": "CustomCombo", "inputs": {"choice": "anything the user typed", "index": 0}}
    p["11"] = {"class_type": "PreviewAny", "inputs": {"source": ["10", 0]}}
    assert validate.validate_prompt(p, oi)[0]
    p["12"] = {"class_type": "LoadImage", "inputs": {"image": "missing.png"}}
    p["13"] = {"class_type": "PreviewAny", "inputs": {"source": ["12", 1]}}
    ok, _e, good, ne = validate.validate_prompt(p, oi, file_exists=lambda kind, name: name == "here.png")
    assert ne["12"]["errors"][0]["details"] == "image - Invalid image file: missing.png" and "13" not in good
    p["12"]["inputs"]["image"] = "here.png"
    assert validate.validate_prompt(p, oi, file_exists=lambda kind, name: name == "here.png")[0]
