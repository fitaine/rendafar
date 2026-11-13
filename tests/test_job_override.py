"""Exercise _apply_job_override in the real render script, with bpy stubbed out."""
import importlib.util, json, os, sys, types, tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

# Prefer the master script when it is checked out next door; otherwise test the
# pinned snapshot, which carries the same override block. Keeps the test
# runnable for anyone who cloned only this repo.
SCRIPT = (config.MASTER_SCRIPT if os.path.isfile(config.MASTER_SCRIPT)
          else config.DEFAULT_TILED_SCRIPT)

# Minimal bpy stub — the module only *imports* bpy at load time.
bpy = types.ModuleType("bpy")
bpy.ops = types.SimpleNamespace()
bpy.context = types.SimpleNamespace()
sys.modules["bpy"] = bpy
sys.modules["mathutils"] = types.ModuleType("mathutils")

tmp = tempfile.mkdtemp()

def load():
    """Fresh import of the script under the current environment."""
    spec = importlib.util.spec_from_file_location("rtu_%d" % len(sys.modules), SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

def write_job(name, payload):
    p = os.path.join(tmp, name)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(payload, f)
    return p

def case(label, env, expect_exc=None, checks=None):
    if env is None:
        os.environ.pop("RENDERFARM_JOB", None)
    else:
        os.environ["RENDERFARM_JOB"] = env
    try:
        mod = load()
    except BaseException as e:
        # Contract: any bad job must abort the script with exit status 1, so the
        # queue never records a broken job as done.  The module-level handler
        # converts the original exception into SystemExit(1) after printing it.
        if expect_exc and isinstance(e, SystemExit) and e.code == 1:
            print(f"  PASS  {label}\n          SystemExit(1) as required")
            return True
        print(f"  FAIL  {label}\n          unexpected {type(e).__name__}: {e}")
        return False
    if expect_exc:
        print(f"  FAIL  {label}\n          expected SystemExit(1), nothing raised")
        return False
    for attr, want in (checks or {}).items():
        got = getattr(mod, attr)
        if got != want or type(got) is not type(want):
            print(f"  FAIL  {label}\n          {attr}: want {want!r}, got {got!r}")
            return False
    print(f"  PASS  {label}")
    return True

results = []

print("\n--- 1. no env var: CONFIG block untouched (existing launch paths) ---")
results.append(case("defaults preserved", None, checks={
    "LONG_EDGE": 120_000, "FILE_FORMAT": "WEBP", "SKIP_EXISTING": True,
    "PREVIEW_MODE": False, "TILE_LONG": 4096,
    # Compositing defaults OFF: Glare/Denoise read neighbouring pixels, which
    # do not exist past a tile edge, so per-tile compositing seams at every
    # boundary.  A job may opt back in, but it must do so deliberately.
    "USE_COMPOSITING": False,
}))

print("\n--- 2. valid job overrides ---")
job = write_job("ok.json", {
    "id": "job_0007", "name": "Ecrins preview",
    "config": {"LONG_EDGE": 40000, "PREVIEW_MODE": True, "PREVIEW_GRID": 5,
               "FILE_FORMAT": "JPEG", "SKIP_EXISTING": False,
               "OUTPUT_DIR": r"D:\out"},
})
results.append(case("overrides applied", job, checks={
    "LONG_EDGE": 40000, "PREVIEW_MODE": True, "PREVIEW_GRID": 5,
    "FILE_FORMAT": "JPEG", "SKIP_EXISTING": False, "OUTPUT_DIR": r"D:\out",
    "TILE_LONG": 4096,          # untouched key keeps its CONFIG value
}))

print("\n--- 2b. compositing is opt-in per job ---")
results.append(case("job may re-enable compositing", write_job("comp.json",
    {"config": {"USE_COMPOSITING": True}}), checks={"USE_COMPOSITING": True}))
results.append(case("compositing rejects a non-bool", write_job("comp_bad.json",
    {"config": {"USE_COMPOSITING": 1}}), expect_exc=TypeError))

print("\n--- 3. defaults bound into render_tiles() signature ---")
os.environ["RENDERFARM_JOB"] = job
m = load()
import inspect
d = inspect.signature(m.render_tiles).parameters
ok = (d["long_edge"].default == 40000 and d["preview_mode"].default is True
      and d["use_compositing"].default is False)
print(f"  {'PASS' if ok else 'FAIL'}  render_tiles() defaults follow the override "
      f"(long_edge={d['long_edge'].default}, preview_mode={d['preview_mode'].default})")
results.append(ok)

print("\n--- 4. failure modes must raise, never silently fall back ---")
results.append(case("unknown key", write_job("bad_key.json",
    {"config": {"LONG_EGDE": 40000}}), expect_exc=KeyError))
results.append(case("bool given an int", write_job("bad_type.json",
    {"config": {"SKIP_EXISTING": 3}}), expect_exc=TypeError))
results.append(case("int given a string", write_job("bad_type2.json",
    {"config": {"LONG_EDGE": "40000"}}), expect_exc=TypeError))
results.append(case("config not an object", write_job("bad_cfg.json",
    {"config": [1, 2]}), expect_exc=TypeError))
results.append(case("missing file",
    os.path.join(tmp, "nope.json"), expect_exc=FileNotFoundError))
malformed = os.path.join(tmp, "malformed.json")
open(malformed, "w").write("{not json")
results.append(case("malformed JSON", malformed, expect_exc=json.JSONDecodeError))

print("\n--- 5. edge cases ---")
results.append(case("empty config", write_job("empty.json", {"config": {}}),
                    checks={"LONG_EDGE": 120_000}))
results.append(case("no config key at all", write_job("nocfg.json", {"id": "x"}),
                    checks={"LONG_EDGE": 120_000}))
results.append(case("quoted path in env var", '"%s"' % job,
                    checks={"LONG_EDGE": 40000}))
results.append(case("empty env var = absent", "   ", checks={"LONG_EDGE": 120_000}))

print(f"\n{'=' * 60}\n  {sum(results)}/{len(results)} passed\n{'=' * 60}")
sys.exit(0 if all(results) else 1)
