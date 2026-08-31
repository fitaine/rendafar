"""
Interactive job creation — what NEW JOB.bat runs when a .blend is dropped on it.

Asks the few questions that matter, shows what will happen, writes the job
JSON.  Every default is "press Enter": a tiled 120k render of a dropped .blend
should be four keystrokes.
"""

import os
import sys

import config
import paths
import queue_manager as qm


def ask(prompt, default=""):
    tail = f" [{default}]" if default != "" else ""
    val = input(f"  {prompt}{tail}: ").strip()
    return val or default


def ask_int(prompt, default):
    while True:
        raw = ask(prompt, str(default))
        try:
            return int(raw.replace("_", "").replace(" ", ""))
        except ValueError:
            print("    not a number, try again")


def warn_if_shared_output(job):
    """
    Two jobs on the same .blend with the same RENDER_NAME share one tile folder
    and manifest — the second would skip everything as already rendered.  Fine
    when resuming on purpose, a trap when a new render was intended.
    """
    out = paths.job_output_dir(job)
    if not out or not os.path.isdir(out):
        return
    tiles = [f for f in os.listdir(out) if f.startswith("tile_")]
    if tiles:
        print(f"\n  ⚠  {len(tiles)} tiles already exist in:")
        print(f"     {out}")
        print( "     This job will RESUME into that folder (existing tiles kept).")
        print( "     For a fresh, separate render, remove this job and re-create")
        print( "     it with a different render name.\n")


def main():
    if len(sys.argv) < 2 or not sys.argv[1].lower().endswith(".blend"):
        print("  Drag a .blend file onto NEW JOB.bat, or:")
        print("  python new_job.py path\\to\\scene.blend")
        return 1
    blend = os.path.abspath(paths.clean_path(sys.argv[1]))
    if not os.path.isfile(blend):
        print(f"  file not found: {blend}")
        return 1

    default_name = os.path.basename(blend).replace(".blend", "")
    print(f"\n  Rendafar — new job for  {os.path.basename(blend)}")
    print( "  ────────────────────────────────────────────────")
    print( "  Type:  1 = tiled (huge image)   2 = animation   3 = single still")
    kind = ask("type", "1")

    name = ask("job name", default_name)

    if kind == "2":
        cfg = {"MODE": "animation"}
        s = ask("first frame (Enter = the .blend's own)", "")
        e = ask("last frame  (Enter = the .blend's own)", "")
        if s: cfg["FRAME_START"] = int(s)
        if e: cfg["FRAME_END"]   = int(e)
        job = qm.create_job(blend, name=name, job_type="classic", cfg=cfg)

    elif kind == "3":
        cfg = {"MODE": "still"}
        f = ask("frame (Enter = the scene's current)", "")
        if f: cfg["FRAME"] = int(f)
        job = qm.create_job(blend, name=name, job_type="classic", cfg=cfg)

    else:
        cfg = {
            "LONG_EDGE":   ask_int("long edge in px", 120_000),
            "TILE_LONG":   ask_int("tile size", 4096),
            "FILE_FORMAT": ask("format JPEG/PNG/WEBP", "WEBP").upper(),
        }
        rn = ask("render name (subfolder; Enter = .blend name)", "")
        if rn:
            cfg["RENDER_NAME"] = rn
        if ask("preview first? y/N", "n").lower().startswith("y"):
            cfg.update({"PREVIEW_MODE": True, "PREVIEW_GRID": 5})
        job = qm.create_job(blend, name=name, cfg=cfg)
        warn_if_shared_output(job)

    print(f"\n  queued  {job['id']}  —  {job['name']}")
    print(f"  script  {os.path.basename(job['script'])}  (pinned)")
    out = paths.job_output_dir(job)
    if out:
        print(f"  output  {out}")
    print(f"\n  It will start when the queue reaches it (or press Start in the app).")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n  cancelled — nothing was created")
        sys.exit(1)
