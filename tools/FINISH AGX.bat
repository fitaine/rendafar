@echo off
rem Drop an EXR (stitched.exr, stitched_photoshop.exr, or your glared EXR) on this file.
rem Optionally drop the .blend too: its Look / Exposure / Gamma are then applied.
setlocal
set "FINISH_EXR="
set "FINISH_BLEND="
for %%F in (%*) do (
  if /I "%%~xF"==".blend" (set "FINISH_BLEND=%%~fF") else (set "FINISH_EXR=%%~fF")
)
"C:\Program Files\Blender Foundation\Blender 5.0\blender.exe" -b --factory-startup --python "%~dp0finish_agx.py"
pause
