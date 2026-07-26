"""
Copy this to config.py and adjust the paths.  config.py is gitignored.

Nothing here points at 3D data.  Blend files and render output stay wherever
they already live on disk; jobs carry absolute paths to them.
"""

import os

# ── Locations ────────────────────────────────────────────────────────────────

APP_DIR = os.path.dirname(os.path.abspath(__file__))

# Where your checkout of Blender-huge-render lives. Only needed if you edit the
# master tiled script; jobs always run the pinned snapshot in frozen/.
RENDER_SCRIPTS_DIR = os.path.join(os.path.dirname(APP_DIR), "Blender-huge-render")

FROZEN_DIR = os.path.join(APP_DIR, "frozen")
QUEUE_DIR  = os.path.join(APP_DIR, "queue")
LOGS_DIR   = os.path.join(APP_DIR, "logs")

STATE_FILE = os.path.join(QUEUE_DIR, "state.json")

# The living tiled-render script — edited freely, never launched directly.
# From https://github.com/fitaine/Blender-huge-render
MASTER_SCRIPT = os.path.join(RENDER_SCRIPTS_DIR, "render_tiles_unlimited.py")

# Read-only snapshots that jobs pin to. Bump these when a new snapshot is cut;
# jobs already created keep pointing at whichever one they recorded.
DEFAULT_TILED_SCRIPT = os.path.join(
    FROZEN_DIR, "render_tiles_unlimited_2026-07-26.py"
)

MASTER_CLASSIC_SCRIPT = os.path.join(APP_DIR, "render_classic.py")
DEFAULT_CLASSIC_SCRIPT = os.path.join(
    FROZEN_DIR, "render_classic_2026-07-26.py"
)

DEFAULT_SCRIPTS = {"tiled": DEFAULT_TILED_SCRIPT,
                   "classic": DEFAULT_CLASSIC_SCRIPT}

# ── Adjust this ──────────────────────────────────────────────────────────────

BLENDER_EXE = r"C:\Program Files\Blender Foundation\Blender 5.0\blender.exe"


# ── Behaviour ────────────────────────────────────────────────────────────────

LOG_BUFFER_LINES = 1500     # lines of stdout kept in memory for the live view
AUTO_START_NEXT  = True     # queue advances on its own when a job finishes

# Preview limits. Building the thumbnail decodes the image on the machine that
# is rendering, so past these the preview is skipped rather than stealing CPU
# and memory from the render.
PREVIEW_MAX_MP = 80         # megapixels
PREVIEW_MAX_MB = 200        # file size on disk
PREVIEW_MAX_PX = 1000       # long edge of the generated thumbnail

PORT = 8765

# Bind address — localhost only.
#
# Remote access is expected to go through a tunnel (Cloudflare Tunnel, or a
# mesh VPN) running on this same machine and connecting over 127.0.0.1, so the
# app itself never listens on a network interface.
#
# THIS APP HAS NO LOGIN. It starts processes on the machine it runs on. Only
# change this if you have deliberately put an authenticating proxy in front.
HOST = "127.0.0.1"

# Public hostname served by your Cloudflare Tunnel (e.g. "render.example.com").
# When set, requests arriving with that Host header must carry Cloudflare
# Access's Cf-Access-Jwt-Assertion header or they are rejected — a fail-closed
# guard against the tunnel being live with the Access policy missing.
# Leave empty if you are not using Cloudflare.
PUBLIC_HOSTNAME = ""


def ensure_dirs():
    for d in (FROZEN_DIR, QUEUE_DIR, LOGS_DIR):
        os.makedirs(d, exist_ok=True)
