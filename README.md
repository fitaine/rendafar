# Rendafar

Watch and steer long Blender renders from your phone.

Rendafar was built to supervise the renders produced by
**[Blender-huge-render](https://github.com/fitaine/Blender-huge-render)** — a tiled
rendering pipeline that produces images far beyond what a GPU can hold in memory, up to
120 000 × 120 000 pixels, split into a grid of tiles and rendered one at a time. Those
renders run for **40 to 80 hours**. Sitting next to the machine for three days is not a
plan, and neither is discovering on Thursday that the render died on Tuesday.

It also runs **ordinary renders** — single stills and animations — with the same queue,
the same resume behaviour, and the same remote control.

---

## What it does

- **Queue** renders and run them back to back, unattended.
- **Stop** a render from anywhere, **resume** it later exactly where it left off.
- **See whether it's healthy**: live progress, a map of which tiles or frames have
  landed, the most recent image, GPU telemetry, and Blender's own output streaming live.
- **Survive a power cut**: on reboot, an interrupted render is detected and restarted on
  its own.

Everything is designed around one idea: **a render is expensive and must never be lost.**
Progress is read from the render's own manifest, which is flushed to disk after every
single tile or frame, so a stop, a crash, or a power cut costs at most the one tile in
flight.

---

## Requirements

| | | |
|---|---|---|
| **Windows** | required | Process control uses `tasklist` / `taskkill`. The rest is portable; nobody has ported it. |
| **Python 3.9+** | required | Standard library only — no framework, no build step, no `npm`. |
| **Blender** | required | Developed against **5.0**. Any 4.x should work; the scripts use long-stable API (`bpy.ops.render.render`, `scene.render.frame_path`). |
| **Pillow** | recommended | `pip install Pillow` — used only to downscale the preview image. Everything else works without it. |
| **NVIDIA GPU** | optional | The GPU panel reads `nvidia-smi`. Without it that panel says "unavailable"; renders are unaffected. |
| **`render_tiles_unlimited.py`** | for tiled renders | From [Blender-huge-render](https://github.com/fitaine/Blender-huge-render). Not needed for stills and animations. |
| **A tunnel** | for remote access | [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/) or a mesh VPN such as [Tailscale](https://tailscale.com). See *Remote access* below. |

That is the whole list. There is deliberately no web framework, no database and no
package manifest: this process has to start unattended at boot after a power cut, and
every dependency is one more thing that can fail there.

---

## Getting started

```bash
git clone https://github.com/fitaine/rendafar
cd rendafar
cp config.example.py config.py     # then edit BLENDER_EXE
pip install Pillow                 # optional, for the preview thumbnail
python server.py
```

Open `http://127.0.0.1:8765`. On Windows the desktop app (below) starts the server for you.

Rendafar is a normal app — run it when you want it, close the window to stop.
Optionally run `INSTALL AUTO-RESUME.bat` once: it installs a logon check that does
nothing at all, *except* when a render was interrupted by a power cut, in which case it
starts Rendafar so the render resumes and notifies you. It never starts the app just
because you logged in.

Queue a render:

```bash
# An ordinary animation — the .blend decides everything
python cli.py classic "D:\scenes\valley.blend"

# One frame
python cli.py classic "D:\scenes\valley.blend" --still 120

# A tiled render, 120 000 px on the long edge
python cli.py add "D:\scenes\valley.blend" --long-edge 120000

python cli.py list
python cli.py start
python cli.py stop
python cli.py resume job_0003
```

Jobs are plain JSON files in `queue/`. The web UI lists, starts, stops, resumes and
reorders them; it deliberately cannot create them.

---

## It is a local tool first

Nothing here requires the internet. Rendafar binds `127.0.0.1` and the tunnel is an
optional extra — plenty of people will only ever use it on the machine that renders:

- a queue, so several renders run back to back overnight instead of you starting each one
- resume after a crash, a stop, or a power cut, down to the exact tile or frame
- a Quit button, a tile map, GPU telemetry, and Blender's output in a readable window

Remote access is a bolt-on. Skip the Cloudflare section entirely and everything above
still works.

### The desktop app (Windows)

A native PySide6 application — menu bar, real file dialogs, real drag-and-drop.

```bat
pip install PySide6
python make_icon.py
INSTALL SHORTCUTS.bat
```

Puts Rendafar on the Desktop and in the Start menu with its own icon; launching it starts
the server hidden. Closing the window closes Rendafar — unless a render is running, in
which case the server stays up so the render remains supervised and reachable from your
phone.

**Qt is only needed for this window.** The server, the phone view and the power-cut
recovery are stdlib-only and keep working without it.

### Adding a render

- **Drag a `.blend` onto the window**, or onto the desktop icon
- **`+ New render`** / `Ctrl+N` → *Browse…* opens the native Windows file dialog
- **`NEW JOB.bat`** — drag a `.blend` onto it for the terminal version

This is the main reason the desktop app exists: a web page is never allowed to see a
file's real path. `<input type=file>` and dropping onto a page both hand over a filename
and the file's *contents*, never its location — no use when Blender has to open the file
where it lives. Qt gives the path.

### From the phone

Watching and steering only: start, stop, resume, reorder. Creating renders and browsing
the filesystem are refused unless the request came from the machine itself — nothing is
gained by exposing them, and a job names an arbitrary file for Blender to open.

### On Android

Open your Rendafar URL in Chrome and choose **Install app**. You get an icon in the app
drawer and a standalone window. The service worker deliberately caches nothing — a cached
"all good" for a render that died an hour ago is worse than no answer at all.

## How it works

```
NEW JOB  ──▶  queue/job_0007.json
                    │
              server.py            owns the queue, is the only thing that starts renders
                ├── runner.py      launches Blender, captures stdout, kills the tree on stop
                ├── paths.py       reads the render's manifest for progress
                ├── gpu.py         nvidia-smi
                └── static/        one HTML page, polls every 3s
```

State lives in JSON files on disk, never only in memory. After a hard power cut, "what
was I doing" must be answerable by reading the disk.

### The .blend is in charge

For classic renders, resolution, samples, engine, output path, file format and frame
range all come from the scene, untouched — what you get is what F12 would give you. Only
the frame range, output folder, camera and compute device can be overridden, and each
defaults to *leave the .blend alone*. There is no samples or resolution setting to get
wrong.

(The tiled script overrides resolution because it must: it renders 4096 px tiles of a
120 000 px image. Here there is no reason to touch anything.)

### Renders pin their script

Each job records which render script it ran, and resumes with **those exact bytes** — the
scripts live as dated, read-only snapshots in `frozen/`. Editing the master script can
never change how a render already in progress behaves, because that render is not reading
the master at all. It is the same reasoning as pinning a dependency version.

### Exit codes are not trusted

Blender exits `0` even after an exception raised at `--python` import time. A supervisor
reading only the exit code would record a broken job as finished and move on. A job is
complete only when its manifest agrees every tile or frame exists.

---

## Remote access

**Rendafar has no login, and it starts processes on the machine it runs on.** Treat it
accordingly: it binds `127.0.0.1` and must never be exposed directly to the internet or
even to a shared LAN.

Put an authenticating layer in front, running on the same machine:

- **Cloudflare Tunnel + Cloudflare Access** — an outbound-only connection, so no ports
  are opened on the router, and Cloudflare authenticates before any request reaches the
  machine. Needs no client on the phone.
- **A mesh VPN** (Tailscale, WireGuard) — nothing is exposed at all, but every device
  needs the VPN client, and phones allow only one VPN tunnel active at a time.

A commercial "privacy" VPN is **not** an option: those route traffic *out* to the
internet and isolate connected devices from each other, so your phone can never address
your render machine through one.

---

## Known limits

- **No pause.** Stop kills the render immediately; the tile in flight is discarded and
  re-rendered on resume. Given the manifest is flushed after every tile, the cost is a
  few minutes and the logic stays simple.
- **No per-sample progress.** Blender prints its `Sample 12/128` progress lines only when
  a render is driven from the command line, not when triggered from Python via
  `bpy.ops.render.render()`. Tiled renders must be driven from Python — the camera shift
  is applied between tiles — so they report tiles done, average time per tile and ETA
  instead.
- **One render at a time**, by design: two Cycles renders on one GPU thrash VRAM and both
  crawl.
- **Windows only**, for now.

---

## Licence

MIT. The LiDAR renders and photography referenced by this project are
© Tiphaine Buccino — all rights reserved.
