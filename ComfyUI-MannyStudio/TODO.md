# Studio to-do: feature gaps

From "Studio: feature gaps against ten ComfyUI projects" (11 Nov 2026). We'll take these one by one.
Tick an item `[x]` when it's done, and add the Studio version it shipped in.

**Hardware notes:** this PC is an Intel Arc 140T (shared memory, no CUDA). Long full-size video jobs already reach about 80 GB. Items that need NVIDIA CUDA (9 Reshoot 4D, 36 Idol) need porting or a rented GPU. Ripple-65B MLX needs Apple Silicon. Check licences before shipping: Reshoot 4D is AGPL-3.0; Qwen-Image-2.1 and MMAudio weights are non-commercial.

**Suggested start:** 2 (SAM erase), 19 (automatic retakes), 16 (sound effects), 22 (EDL/XML export), 12 (camera wording per model).  
**Biggest projects:** 5 (video remake), 9 (reshoot real footage), 14 (music video from a song).

## A. Editing existing video

- [ ] **1. First-frame paint, carried through the shot. Paint an addition, an erase or a relight on frame 1 and it follows through the whole clip.**  
  _Source:_ Paint a plate · _Studio today:_ Partial: Inpaint works on stills; Fix a part redraws a time range but takes no painted edits.
- [ ] **2. Whole-object erase with SAM 3.1. Name an object; it is selected, tracked through the shot and filled in.**  
  _Source:_ Paint a plate, Freefield · _Studio today:_ None: no segmentation or tracking.
- [ ] **3. Relight a shot or picture from a colour, a painted light source or a text description.**  
  _Source:_ Paint a plate, Freefield · _Studio today:_ None.
- [ ] **4. Fluid effects (smoke, steam, fire) from painted colour keyframes.**  
  _Source:_ Paint a plate · _Studio today:_ None.
- [ ] **5. Remake a video from painted reference frames. Swap several characters at once, or change objects, background, weather, setting or style, keeping motion, camera and soundtrack.**  
  _Source:_ Viggle Remake · _Studio today:_ Partial: Character replace (Wan Animate 2) swaps one person.
- [ ] **6. Long videos in ~12 s windows, spliced back together with the original soundtrack.**  
  _Source:_ Viggle Remake · _Studio today:_ None.
- [ ] **7. Ready-made VFX apps: clean plate, day to night, water, SDR to HDR, mixed media, video background removal.**  
  _Source:_ Freefield · _Studio today:_ Partial: background removal on stills only.
- [ ] **8. Reframe, motion transfer and camera copy: change aspect ratio, or copy motion or a camera move from a reference clip.**  
  _Source:_ Freefield · _Studio today:_ Partial: a storyboard shot can follow a reference video's move.

## B. Camera

- [ ] **9. Reshoot real footage with new camera moves. A 4D capture of the clip, virtual camera keys on a timeline, then an LTX retake.**  
  _Source:_ Reshoot 4D, Freefield · _Studio today:_ Partial: Blender previs moves, only on sets you build.
- [ ] **10. Lens controls per camera key: aperture, rack focus, dolly zoom, vignette, grain; looks such as distortion, chromatic aberration, bloom, halation, anamorphic bokeh.**  
  _Source:_ Reshoot 4D · _Studio today:_ None.
- [ ] **11. Camera import and export as FBX, Alembic or USD (ties into Blender, C4D and After Effects).**  
  _Source:_ Reshoot 4D · _Studio today:_ None.
- [ ] **12. Camera panel with shot size, angle and movement in a 3D diagram, written in the wording each model expects (FLUX 3, LTX-2.5) as editable clauses.**  
  _Source:_ Genobi · _Studio today:_ Partial: camera-move chips use the same words for every model.

## C. Music video and sound

- [ ] **13. Song analysis: bars, sections, the drop and loudness, plus vocal and drum stems (Demucs).**  
  _Source:_ OneShot MV · _Studio today:_ Partial: beat detection only.
- [ ] **14. Song plus a one-line brief to a finished music video: LLM-written cast and locations, cut rhythm by energy, lip-synced performers, B-roll.**  
  _Source:_ OneShot MV · _Studio today:_ Partial: Reel cuts existing clips to the beat.
- [ ] **15. Lip sync, talking heads and dubbing.**  
  _Source:_ Freefield, OneShot MV · _Studio today:_ None as a dedicated tool.
- [ ] **16. Sound effects and Foley (Stable Audio 3, MMAudio).**  
  _Source:_ Freefield, Imagination Studio · _Studio today:_ None (music and voices exist).
- [ ] **17. Room acoustics on clips with the AKUSPACE LoRA.**  
  _Source:_ Genobi · _Studio today:_ None.
- [ ] **18. Effects driven by the music (drum hits), and re-cutting a finished run without rendering again.**  
  _Source:_ OneShot MV · _Studio today:_ None for music-driven effects.

## D. Automatic quality checks

- [ ] **19. Automatic reject and re-shoot. A vision check rejects bad portraits; take QC re-shoots frozen, blown-out, drifting or badly framed takes and weak lip sync (SyncNet).**  
  _Source:_ OneShot MV · _Studio today:_ Partial: AI review of a take, run by hand.
- [ ] **20. Fixed-seed test lab across every app, with "mediocre" marks on example results.**  
  _Source:_ Freefield · _Studio today:_ Partial: tests cover routing and remote GPUs, not output quality.
- [ ] **21. Per-machine feature checks: test renders that prove a feature works on that machine.**  
  _Source:_ Imagination Studio · _Studio today:_ Partial: live worker check of nodes, models and inputs before a remote job.

## E. Finishing and handoff

- [ ] **22. Export to an editor as EDL or Final Cut Pro XML.**  
  _Source:_ OneShot MV, Imagination Studio · _Studio today:_ None: MP4 only.
- [ ] **23. Colour grade presets, grain and vignette as a Finish step.**  
  _Source:_ OneShot MV, Imagination Studio · _Studio today:_ Partial: Finish does upscale, frame rate and sound.
- [ ] **24. Plate-faithful export: EXR, original frame numbers, timecode, OCIO colour, untouched pixels identical, plus mattes.**  
  _Source:_ Paint a plate · _Studio today:_ None.
- [ ] **25. Motion graphics and an audio mix in Finish.**  
  _Source:_ Imagination Studio · _Studio today:_ Partial: editor titles, captions, music bed.

## F. App structure

- [ ] **26. One manifest per app listing models, VRAM per phase and licences, which drives a VRAM map, model downloads and a "how it works" page.**  
  _Source:_ Freefield · _Studio today:_ Partial: *.app.json apps are listed automatically, without that metadata.
- [ ] **27. Template apps: about 70 presets (poster, headshot, try-on, bullet time, product shot, moodboard…) with an "extra direction" field.**  
  _Source:_ Freefield · _Studio today:_ Partial: style presets and prompt help.
- [ ] **28. Import any ComfyUI workflow as an app; export a whole scene as a runnable graph.**  
  _Source:_ Freefield, Paint a plate · _Studio today:_ Partial: workflows saved with App builder appear in Create.
- [ ] **29. Editable history with undo and redo, where later edits update; identical jobs are skipped, even across sessions.**  
  _Source:_ Paint a plate · _Studio today:_ None.
- [ ] **30. Previews in stages, from a 640 px live preview (taehv) to full size.**  
  _Source:_ Paint a plate · _Studio today:_ None.
- [ ] **31. Compare 2 to 4 results with shared playback and exact-frame export; Recreate restores a past run's full setup.**  
  _Source:_ Genobi · _Studio today:_ Partial: Compare and wipe exist.
- [ ] **32. Director templates (Character intro, Outfit reveal, Poster in motion), up to 10 references, Klein bounding-box layouts.**  
  _Source:_ Genobi · _Studio today:_ None.
- [ ] **33. Voice mode with local speech recognition, a taste reference wiki and project memory.**  
  _Source:_ Genobi · _Studio today:_ None.
- [ ] **34. LoRA training inside Studio.**  
  _Source:_ Imagination Studio (planned in Freefield) · _Studio today:_ None: Civitai LoRA browser only.
- [ ] **35. English and Spanish interface.**  
  _Source:_ Freefield · _Studio today:_ None.

## G. 3D and the planning brain

- [ ] **36. One picture to a 3D Gaussian splat (.ply) for props or characters in Blender sets.**  
  _Source:_ Idol · _Studio today:_ None.
- [ ] **37. Plug-in local LLM for script and planning, over an Ollama or OpenAI-style endpoint.**  
  _Source:_ Kimi, Ripple · _Studio today:_ Partial: Qwen3-VL 4B/8B inside ComfyUI.

## Sources

- Idol · https://huggingface.co/speridlabs/idol
- Kimi K3.1 Pro SuperExpert 12GB · https://huggingface.co/shashanth-27xA9/Kimi-K3.1-Pro-SuperExpert-12GB
- Ripple-65B Preview MLX 4-bit · https://huggingface.co/frogggias/Ripple-65B-Preview-MLX-4bit
- Viggle Remake · https://huggingface.co/Viggle/Viggle-Remake
- Imagination Studio · https://github.com/drowzeys/Imagination-Studio-by-keys-Wasserman
- Paint a plate · https://github.com/AInVFX/ainvfx-paintaplate
- Reshoot 4D · https://itsmetamike.github.io/reshoot-4d/
- OneShot MV · https://github.com/VisualFrisson/ComfyUI-OneShotMV
- Freefield · https://github.com/neuregex/freefield
- Genobi · https://github.com/koshimazaki/genobi
