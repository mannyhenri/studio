# Studio's offline sandbox

Studio normally runs inside ComfyUI on a Windows PC with an Intel Arc GPU, with the Mac Pro's two Vega II cards as extra
GPU machines. The sandbox runs it on Linux with no GPU, no models and no internet, so a change can be tried and reviewed
anywhere.

## Start

```sh
npm run sandbox
```

The first time, this runs `scripts/setup-linux.sh`: a Python environment in `.venv` (aiohttp, numpy, pillow, av, pytest,
ruff; no torch), ffmpeg (the system's, or the imageio-ffmpeg build if there is none) and the sample media. Then it lays out
a fresh ComfyUI folder tree in `sandbox/run/`, starts Studio inside the stand-in ComfyUI, seeds the demo data and opens
**http://localhost:8188/studio**. Ctrl+C stops it. Only `pip` (PyPI) is contacted, and only during setup.

| Command | What it does |
|---|---|
| `npm run sandbox` | Setup if needed, fresh demo data, start, open the browser (`python3 scripts/sandbox.py demo --open`) |
| `python3 scripts/sandbox.py demo` | Fresh demo data and start (add `--open` to open the browser, `--port` for another port) |
| `python3 scripts/sandbox.py empty` | Start with no data at all: the Gallery empty too |
| `python3 scripts/sandbox.py serve` | Start on the data already in `sandbox/run/` |
| `python3 scripts/sandbox.py jobs` | The jobs Studio sent, newest first: app, status, result files and sizes |
| `python3 scripts/sandbox.py last-job` | The last job: app, models, result size, prompt, node types |
| `python3 scripts/sandbox.py queue-bad` | Queues a job that needs a model the sandbox doesn't have: refused with ComfyUI's “Value not in list” |
| `python3 scripts/sandbox.py reset` | Deletes `sandbox/run/` and `sandbox/jobs/` |
| `python3 scripts/sandbox.py check` | `pytest tests/unit tests/sandbox` and `ruff check .` |
| `python3 scripts/studio-call.py <tool> '<json>'` | Calls one of Studio's MCP tools through the real connector (`--list` lists them) |

## The demo data

The demo is the pictures and clips of a small piece of a real Studio film, **The Glass Harbour** (a seaside diner at
sunset), in `sandbox/samples/`: about 1 MB, small copies with all metadata removed. Nothing else is seeded: no characters,
Previz sets, storyboards or projects, so a project starts from a blank slate with its screenplay.

| What | Details |
|---|---|
| Gallery | 3 clips (`diner-wide.mp4`, `diner-ray.mp4`, `diner-counter.mp4`: 4 s, 854×480, 24 fps, with sound), 3 storyboard frames, `diner.jpg`, `salt-shaker.jpg` |
| Inputs for Create | the same files, the faces of June and Ray (`studio/june.jpg`, `studio/ray.jpg`, for a character's face), plus drawn test media: `street.mp4` (8 s, 1280×720), `portrait.png`, `chair.png`, `song.wav` (40 s, 120 BPM: quiet 0–16 s, loud 16–32 s, quiet 32–40 s), and the example files the apps open with |
| Screenplays | `screenplays/The Glass Harbour.pdf` and `screenplays/the_glass_harbour.fountain` (the same script) |

To start a project: **Projects › Import a screenplay** (or drop the file on the page) and pick either screenplay. The project
opens on its steps:

1. **Screenplay**: the script as a page, edited in place like a screenwriting app (Enter goes to the next element; Tab
   picks the one the place calls for: after a speech the next speaker, at the end of a cue a parenthetical; SmartType offers
   characters, headings, places and times; ⌘/Ctrl+/ lists every shortcut), saved as Fountain. The title page is a page of its own, in a dialog (▤ Title page; a new screenplay starts
   there). A Fountain file is read as its own PDF prints it: an action paragraph on each line, even with no blank line
   between (The Glass Harbour's file and its PDF import as the same screenplay). The page is the Hollywood standard one (Riley's *The Hollywood Standard*): US Letter,
   Courier 12 at 10 characters and 6 lines an inch, 55 lines a page; scene headings and action from 1.5" to 7.5" (59
   characters, text kept inside the margin), dialogue 2.5" to 6.0" (34), parentheticals 3.1" (24), character cues 3.7",
   transitions 6.0". The marks across the page show where its pages break; **⤓ PDF** exports it on that page: title page,
   scene numbers in both margins, page numbers from page 2, (MORE) and NAME (CONT'D) when a speech breaks over a page
   (between sentences), no scene heading or cue left at the foot of a page. Laid out this way, the screenplay read from
   `The Glass Harbour.pdf` falls on the same 7 pages as that PDF, line for line.
2. **Scenes & shots**: the breakdown. A scene per scene heading with its breakdown-sheet fields (number, INT/EXT, set,
   Day/Night, length in eighths of a page from the layout, cast numbers, OMITTED scenes, one continuous take), and a shot
   per camera setup. A new setup starts where the screenplay calls for one: a shot heading (CLOSE ON, ANGLE ON, INSERT,
   POV, WIDE…), a cut (a transition), or a camera direction that moves or re-frames the camera ("The camera eases toward
   Ray"). Action and dialogue play inside the current setup, and so does a camera that holds ("holds on him", "stays
   close"); the scene's first camera direction is its opening shot, with the set's description. Shots are numbered by
   scene (1A, 1B… no I or O; 12A-A for a scene 12A), the camera is the first move described, the screen time a page a
   minute. The Glass Harbour comes to 8 shots: its one continuous take and the seven moves after the opening dolly.
   The breakdown is made again from the screenplay every time it's saved, so writing a new screenplay fills it as you go;
   what you decide in it stays: ✂ splits and ⤒ merges of setups, a camera or length you set, keyframes, shots you add (↺
   goes back to the screenplay's own setups). The action and the lines shown per shot are the screenplay's text: editing
   them there edits the screenplay. Cameras, lengths and storyboard prompts are production choices and stay out of it.
3. **Cast**: the screenplay's characters, numbered as on a cast list, created as Studio characters with the description
   from their introduction (the age goes in its field), as the screenplay is written: a new speaking part is cast at the
   next save (a Studio character of that name, or a new one; one made this way that nobody touched goes again with its
   part, a typo, and with the project). A cast part points at the Studio character, so what changes on the
   character (face, sheet, LoRA) is the project's at once. The character sheet describes them field by field (age,
   gender, build, skin, face, eyes, hair, facial hair, distinguishing marks, wardrobe, accessories, anything else); the
   sheet is composed into one description ("What the generator gets") that goes with every render of them: pictures in
   Create (pick them, or write @Ray), the project's keyframes, the storyboard's prompts and reference pictures.
   ✦ Make a reference picture renders one from the sheet (with the face, if there is one, to keep it); click a picture to
   make it the face. Any picture in the Gallery can be made a character's face too. A character's LoRA (a LoRA trained
   on them) is loaded with every render of them, its trigger word first in the description, when it's made for the model
   in use.
4. **Locations**: every place the scene headings name, made from the screenplay as it's written (its scenes, INT/EXT,
   the description of the first scene set there), each with a form: name, description, what the generator gets (written
   into every shot set there), lighting, time of day, atmosphere, sound, a reference picture (✦ made from its look). A
   place renamed in its form is renamed in the scene headings; one gone from the screenplay goes if untouched, else stays,
   marked. 5. **Storyboard** (Pre-production, as Film Engine's): a frame per shot with its card (Sc / shot code, camera,
   speaks ×N, who, where, length, description, lines; a click on a card's text shows what doesn't fit), search, scene and frame filters, Generate all, versions, Import,
   + Shot, Direct, a frame viewer, Print, Lock board; a frame made before the screenplay changed its shot says so. The
   frames are the takes' first frames. 6. **Takes** (Production): exactly the breakdown's shots, in its order, with its numbers, cameras and lengths
   (each take renders at the Video app's nearest length and the cut is trimmed to the shot's; a setup longer than one take
   is made in parts with Continue →, and in a continuous take each move starts on the last frame of the one before);
   removing, moving or adding shots on the board changes the breakdown. 7. **Music & dialogue**, 8. **Film edit**.

The drawn media are made with numpy and ffmpeg at setup (`sandbox/make_media.py`). The demo media are placed by `sandbox/demo.py`.

## Trying Previz

**Previz › Quick scene** gives a blank stage with a stand-in to direct from scratch. **Previz › Add a .glb** and pick `3D sets/Squared V2.glb` (85 MB, or give its path), then open it: 53 cameras, 19 lights, eight
characters and 3:42 of animation. For example: camera `CAM_03_Chamber_35mm` (the timeline goes to its move, 1:08–1:40; VIX stands in the chamber), Subject VIX, then Framing › MCU, Height › Low angle, Moves › Push in › ▶ Play the move, Light › Rembrandt, and
Objects › Person to give her someone to talk to (then Framing › With: Person 1 › Over the shoulder). 📷 Snapshot makes a
storyboard shot's first frame or a location shot; 🎬 Movie renders the camera's move into the Gallery. `Squared Sets.glb` is
the same set exported without cameras (24 camera rigs, 23 cameras listed as missing). The viewer needs WebGL: in a browser
without a GPU it runs on the software renderer, slowly.

## How it works

`sandbox/fake_comfy.py` is the stand-in ComfyUI. It is not a mock inside Studio: Studio's node pack loads into it unchanged
and talks to it over HTTP on port 8188, like inside ComfyUI.

- **The parts of ComfyUI that Studio imports** are in `sandbox/comfy_stub/`: `server.PromptServer` (routes, websocket
  messages, the queue), `folder_paths` (the `sandbox/run/` tree), `comfy.model_management` (a CPU with 16 GB) and `nodes`.
- **ComfyUI's API** as Studio and the web app use it: `/object_info`, `/prompt`, `/queue`, `/history`, `/view`,
  `/upload/image`, `/interrupt`, `/free`, `/system_stats`, `/models` and the `/ws` progress websocket, also under `/api/`.
- **Node definitions**: `sandbox/object_info.json` is the `/object_info` of a real ComfyUI 0.45.0 (every core node,
  including LTX-2.5, MiniMax H3, Qwen Image 2.1, Wan Animate 2, SeedVR2 and the frame-interpolation and background-removal
  nodes). The model and file pickers (`sandbox/file_inputs.json`) are filled from the sandbox folders on each request, as
  ComfyUI does. `sandbox/custom_nodes.json` adds the custom packs Studio's code builds jobs for (the H3 multishot and face
  refine packs, Chatterbox), with their inputs left open. Studio's own nodes (Studio Keep, Studio Recall) come from the pack.
- **Models**: `sandbox/models.json` lists every model file the workflows and Studio's code name (`python
  sandbox/collect_models.py` rebuilds it); each becomes an empty file in `sandbox/run/models/`, so the pickers list what the
  PC has. A job naming any other model is refused. The files are empty, so Studio's own model lists (Models, Settings › Models on this PC) show none of them: they list only files really downloaded.
- **Checking jobs** (`sandbox/validate.py`): ComfyUI's validation with its error types and messages: unknown node, no
  outputs, required input missing, bad link, return type mismatch, value not in list, out of range, ComfyUI's dynamic inputs
  (autogrow, dynamic combos, match types), nodes that check their own inputs (`sandbox/custom_validation.json`, read from
  ComfyUI's source) and the file loaders (the file must exist). Like ComfyUI, the outputs that pass still run; a job is
  refused (400) only when none does.
- **Rendering** (`sandbox/results.py`): each output node gets a file of the right kind: a grey picture, a grey clip with a
  moving bar and a quiet tone, a tone for sound, a fixed answer from the stand-in LLM for text (a valid script for Projects,
  a valid review for AI review). Sizes and lengths come from the job's own graph (empty latents, resize nodes, upscalers,
  math nodes, LTX's latent upsampler). A job takes about a second (`SANDBOX_RENDER_SECONDS`).
- **Every job** is saved to `sandbox/jobs/` as JSON: its graph, app, label, outputs and status (refused jobs as
  `refused-*.json` with ComfyUI's error).
- **Isolation**: the server runs with `HOME=sandbox/run/home`, so nothing Studio writes (Studio Sets, git settings,
  backups) lands outside the sandbox. Windows-only parts (keep awake, the Windows event log, GPU counters) already skip
  themselves on Linux; Topaz shows “Not installed on this computer”.

## Tests

| Suite | What it checks |
|---|---|
| `python -m pytest tests/unit` | The stand-in's validation against the real node definitions, result sizes from graphs, the sample media, Studio's own logic scripts that need no GPU machine, and reading screenplays (Fountain, and PDFs: the real one in `screenplays/` plus layouts written by the test), finding the cameras in a .glb (`tests/glbfile.py` writes small ones; the two sets in `3D sets/` too), and Previz's framing, moves and lighting maths (`tests/unit/previz/*.test.mjs`, run with node; skipped without it) |
| `python -m pytest tests/sandbox` | The sandbox end to end: the pack loads and every route answers, no Windows-only program starts, ComfyUI's API, uploads, refusals, websocket messages and history, every app with its default values, demo and empty data, the MCP call tool, Studio's `test_prune.py` against the stand-in, Previz sets and props (.glb upload, add by path, what was directed, Incognito, the vendored three.js), and the pages and directing a shot in Previz in headless Chrome with no console errors (skipped without Chrome) |
| `python -m ruff check .` | Syntax errors, undefined names and unused imports |
| `STUDIO_LIVE=1 python -m pytest tests/live` | The live tests against the real GPU machines; skipped in the sandbox |

## Limits

Nothing renders: the results are placeholders, so judge the graphs Studio builds, the files it writes and the app, not the
pictures. The node definitions are ComfyUI 0.45.0's; the PC may have a newer ComfyUI or other packs (capture the PC's own
with `curl http://<pc>:8188/object_info > sandbox/object_info.json` to match it exactly). Topaz, Civitai, Hugging
Face and the Mac workers are not there.
