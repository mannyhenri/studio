# Studio's offline sandbox

Studio normally runs inside ComfyUI on a Windows PC with an Intel Arc GPU, with the Mac Pro's two Vega II cards as extra
GPU machines. The sandbox runs it on Linux with no GPU, no models and no internet, so a change can be tried and reviewed
anywhere.

## Start

```sh
npm run sandbox
```

That is the one way to run the app. The first time (and again whenever `sandbox/requirements.txt` changes, or when a
setup stopped half-way), it runs `scripts/setup-linux.sh`: a Python environment in `.venv` (aiohttp, numpy, pillow, av,
pytest, ruff; no torch), ffmpeg (the system's, or the imageio-ffmpeg build if there is none) and the sample media. Then it
lays out a fresh ComfyUI folder tree in `sandbox/run/`, starts Studio inside the stand-in ComfyUI, seeds the demo data and
opens **http://localhost:8188/studio** in your browser by itself. Ctrl+C stops it. Only `pip` (PyPI) is contacted, and
only during setup.

The browser opens on a desktop (Linux with a display, macOS, Windows, WSL: the Windows browser). Where there's no desktop
to open it in (ssh, a container), it says so and prints the address. If a sandbox is already running on the port,
`npm run sandbox` opens that one in the browser instead of starting another; if something else holds the port, it uses the
next free one and says which.

| Command | What it does |
|---|---|
| `npm run sandbox` | Setup if needed, fresh demo data, start, open Studio in the browser |
| `npm run sandbox -- --keep` | The same, on the data left from last time (nothing is reset) |
| `npm run sandbox -- --empty` | The same, with no data at all: the Gallery empty too |
| `npm run sandbox -- --port 8190` | On another port |
| `npm run sandbox -- --no-open` | Without opening the browser (or set `SANDBOX_NO_BROWSER=1`) |
| `npm run sandbox:jobs` | The jobs Studio sent, newest first: app, status, result files and sizes |
| `npm run sandbox:last-job` | The last job: app, models, result size, prompt, node types |
| `npm run sandbox:check` | `pytest tests/unit tests/sandbox` and `ruff check .` |
| `npm run test:mcp` | The MCP server's checks, each by name (protocol, every tool called, a story to a film, HTTP, the page): about 4 min |
| `npm run test:generators` | The AI tools' checks, each by name (every tool with every setting, outputs checked): about 4 min |
| `python3 scripts/sandbox.py queue-bad` | Queues a job that needs a model the sandbox doesn't have: refused with ComfyUI's “Value not in list” |
| `python3 scripts/sandbox.py reset` | Deletes `sandbox/run/` and `sandbox/jobs/` |
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

## Trying the AI tools

**Production › AI tools** has Film Engine's six Generators (each one a page, also one click away from Home's **Your
apps**; the Gallery's viewer sends a video or a picture there with **✦ AI tools**): **AI Depth**, **AI Slow-Mo**, **AI Stems**, **AI Subtitles**, **AI Lossless Cut** and
**One-Click Master**. Give a tool a file (**From the Gallery…**, which lists the output folder's sounds too, or drop or
choose one: it's uploaded, any size), set it and **Generate**. For example: AI Slow-Mo on `diner-wide.mp4`, I at 0:00.5 and
O at 0:01.5, 4×; AI Lossless Cut on `diner-ray.mp4`, **Split at playhead** twice, Smart cut; One-Click Master on `song.wav`
(Sounds), Apple Music. The History keeps every run, with what it measured (a master's loudness and true peak, a cut
piece's real start) and its files to play, open in the Gallery or download. Here:

- **AI Slow-Mo** and **AI Depth** queue their GPU work on the stand-in ComfyUI like any render (labelled "AI Slow-Mo ·
  diner-wide.mp4" in the queue and in `npm run sandbox:jobs`), so their graphs are checked against ComfyUI 0.45's FILM and
  Depth Anything 3 nodes; the stand-in answers with grey clips, so the results are placeholders (a depth of field of a grey
  depth map blurs evenly).
- **AI Lossless Cut** and **One-Click Master** are ffmpeg only: their results are real.
- **AI Stems** and **AI Subtitles** run local models (Demucs, Whisper) that the sandbox stands in for
  (`sandbox/engine_stubs/`): a tone for each stem, a placeholder transcript, with the real engines' command line.

## Trying the MCP server (Claude and other assistants)

The sandbox prints its MCP address when it starts: **http://localhost:8188/studio/mcp**. Claude Code started in this
folder picks it up from `.mcp.json`; any other assistant that speaks MCP over HTTP takes the URL; Claude Desktop starts
`ComfyUI-Studio/studio_mcp.py` with `STUDIO_URL=http://localhost:8188`. Then ask for a film, e.g. *"Write a three-scene
screenplay about a lighthouse keeper and her grandson on a stormy night, then make the characters and the locations, with
their pictures, and the storyboard"*, with the app open on Projects: the project appears, and each page shows what the
assistant does as it does it. Without an assistant, `python3 scripts/studio-call.py <tool> '<json>'` calls the same tools
through the real connector (`--list` lists the 61 of them), for example:

```sh
python3 scripts/studio-call.py studio_project_new '{"screenplay_path": "screenplays/The Glass Harbour.pdf"}'
python3 scripts/studio-call.py studio_cast '{"project": "The Glass Harbour"}'
python3 scripts/studio-call.py studio_character_picture '{"character": "Ray Mercer"}'
python3 scripts/studio-call.py studio_storyboard_frames '{"project": "The Glass Harbour"}'
```

Here the pictures and clips are the stand-in's grey placeholders (`studio_view` shows them to the assistant all the same).

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
- **MCP** (`ComfyUI-Studio/studio_mcp.py` over stdio, `mcp_http.py` at `/studio/mcp`): the tools call Studio's HTTP API, and
  a project's steps the page does in the browser (the screenplay broken down again, the cast and the locations following it,
  the pictures' prompts and references, the takes, the sound, the film) are done on the server for them (`projectops.py`).
  What an assistant saves is marked `by: "ai"`: the page takes it in (`/studio/api/stamps`) and a page's older save is refused (409).
- **AI tools** (`ComfyUI-Studio/generators.py` and an engine per tool in `ComfyUI-Studio/ai_tools/`): each engine runs
  in its own process with the server's Python, one job at a time. `sandbox/engine_stubs/` stands in for Demucs and
  faster-whisper on the engines' path only (`STUDIO_GENERATORS_PYTHONPATH`); `sandbox/models.json` has AI Depth's Depth
  Anything 3 model (`da3_mono_large.safetensors`, from its `generator.json`) and Slow-Mo's FILM model.
- **Isolation**: the server runs with `HOME=sandbox/run/home`, so nothing Studio writes (Studio Sets, git settings,
  backups) lands outside the sandbox. Windows-only parts (keep awake, the Windows event log, GPU counters) already skip
  themselves on Linux; Topaz shows “Not installed on this computer”.

## Tests

Two suites can be run on their own, each check listed by name with PASSED or FAILED, and a last line that says whether all
of them passed:

```sh
npm run test:mcp              # Studio's MCP server (AI assistants)
npm run test:generators       # the AI tools (Film Engine's Generators)
```

They run in sandboxes of their own (nothing to set up). The checks that open Studio in a headless browser use this
computer's Chrome, Chromium, Edge or Brave, found wherever it's installed (the PATH, /Applications on a Mac, Program Files
on Windows, a Playwright download: `sandbox/find_chrome.py`); the suite says which one at the start. Without one they're
listed as skipped, and the last line says so; `STUDIO_TEST_CHROME=/path/to/chrome` picks a browser.

To check **a real Studio** (ComfyUI running on the PC, any computer on the network), give its address:

```sh
npm run test:mcp -- --url http://192.168.1.20:8188
npm run test:generators -- --url http://192.168.1.20:8188            # readiness of each tool, Lossless Cut and Master for real
npm run test:generators -- --url http://192.168.1.20:8188 --render   # and the four that use its GPU or a local model
```

Against a real Studio nothing renders unless `--render`; the MCP checks make a project "MCP check …" and delete it (with the
character made for it), the generator checks upload three small files named `studio-test-…` and leave their results in the
Gallery under Generators/. A tool that isn't ready there fails its readiness check with the reason and what to install.


| Suite | What it checks |
|---|---|
| `python -m pytest tests/unit` | The stand-in's validation against the real node definitions, result sizes from graphs, the sample media, Studio's own logic scripts that need no GPU machine, and reading screenplays (Fountain, and PDFs: the real one in `screenplays/` plus layouts written by the test), finding the cameras in a .glb (`tests/glbfile.py` writes small ones; the two sets in `3D sets/` too), Previz's framing, moves and lighting maths (`tests/unit/previz/*.test.mjs`, run with node; skipped without it), the MCP protocol (`test_mcp_protocol.py`: initialize, tools' schemas, errors, stdio with Studio down), and the AI tools: their host (manifests, readiness, the command line, jobs, Cancel: `test_generators.py`, with the fake engines in `tests/fixtures/generators/`) and their engines (Lossless Cut's keyframe and frame-exact smart cuts, One-Click Master's loudness and true peak, Depth's effects, Stems and Subtitles with the stand-ins: `test_ai_tool_engines.py`), and a project's steps for MCP (`test_projectops.py`: locations and cast following the screenplay, references, takes' values) |
| `python -m pytest tests/sandbox` | The sandbox end to end: the pack loads and every route answers, no Windows-only program starts, ComfyUI's API, uploads, refusals, websocket messages and history, every app with its default values, demo and empty data, the MCP call tool, Studio's `test_prune.py` against the stand-in, Previz sets and props (.glb upload, add by path, what was directed, Incognito, the vendored three.js), the pages and directing a shot in Previz in headless Chrome with no console errors (skipped without Chrome), the MCP server end to end (`test_mcp.py`: every tool's schema, a story to a film with nothing but MCP calls and the jobs it sends, the screenplay changed and everything following, a file imported, the AI tools and the gallery, the same tools over HTTP, a page never saving over an assistant's change and showing it by itself; `test_mcp_tools.py`: every tool the server lists, called and checked), the AI tools with every setting (`test_generators_matrix.py`: each tool's defaults on each kind of file and each choice of each option, from their generator.json, outputs fetched and checked), and the AI tools end to end on a sandbox of their own (`test_ai_tools.py`: every tool through the API and the stand-in ComfyUI, their results in the Gallery, no work file left, Cancel, the Production menu and a clip slowed from the page in Chrome) |
| `tests/sandbox/test_mcp_live.py`, `test_generators_live.py` | A real Studio, with `--url` (above); skipped otherwise |
| `python -m ruff check .` | Syntax errors, undefined names and unused imports |
| `STUDIO_LIVE=1 python -m pytest tests/live` | The live tests against the real GPU machines; skipped in the sandbox |

## Limits

Nothing renders: the results are placeholders, so judge the graphs Studio builds, the files it writes and the app, not the
pictures. The node definitions are ComfyUI 0.45.0's; the PC may have a newer ComfyUI or other packs (capture the PC's own
with `curl http://<pc>:8188/object_info > sandbox/object_info.json` to match it exactly). Topaz, Civitai, Hugging
Face and the Mac workers are not there. The AI tools' local models (Demucs, Whisper) are stand-ins.
