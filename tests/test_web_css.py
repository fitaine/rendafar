"""
Guards against two real bugs found from a phone screenshot, both invisible to
every other test because they are pure CSS.

    python tests\test_web_css.py
"""

import os
import sys

HTML = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "static", "index.html")
src = open(HTML, encoding="utf-8").read()
# Both sides of every comparison below go through this, or a space present on
# only one side (as originally shipped in this very test) silently fails a
# check that is actually passing.
flat = src.replace(" ", "").replace("\n", "")


def norm(s):
    return s.replace(" ", "").replace("\n", "")


results = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"\n          {detail}" if detail else ""))
    results.append(bool(ok))


print("\n=== the [hidden] specificity bug ===")
# .actions{display:flex} has the same specificity as the browser's own
# [hidden]{display:none} and, being an author rule, wins regardless of order —
# which silently showed the New Render button on the phone (it is `hidden`
# there, but .actions overrode it). A global !important rule is the only fix
# that survives every future class added to a hidden element, not just today's.
check("a global [hidden] override exists",
      norm("[hidden]{display:none !important}") in flat,
      "without it, any class setting display:* on a hidden element wins the "
      "cascade over the browser's own default")

print("\n=== the CSS Grid overflow trap ===")
# A bare `1fr` track still grows past 100% of its container if anything inside
# cannot shrink below its own min-content width — a long unbroken path, a wide
# row. That clipped every card's right edge and right-hand corners on the
# phone, invisibly, because overflow-x:hidden was hiding the very overflow
# that would otherwise have made the bug obvious as a horizontal scrollbar.
check("mobile .cols uses minmax(0, …), not a bare 1fr",
      norm("grid-template-columns:minmax(0,1fr)") in flat)
check("desktop .cols uses minmax(0, …) on both tracks",
      norm("grid-template-columns:minmax(0,1.15fr) minmax(0,.85fr)") in flat)
check(".card can shrink to its track (min-width:0)",
      norm("padding:14px; min-width:0") in flat)

print(f"\n{'=' * 62}\n  {sum(results)}/{len(results)} passed\n{'=' * 62}")
sys.exit(0 if all(results) else 1)
