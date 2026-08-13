"""
Assembles sportsbook.html and docs/index.html from sportsbook.template.html
+ the current odds board. Run this after updating projections in
odds_model.py:

    python3 build_sportsbook.py

Regenerates board data via generate_board_data.py's build_board() and
splices it into the template alongside the embedded (subsetted, base64)
webfonts, then writes:

  - sportsbook.html: the bare fragment used by the Claude Artifact tool
    (which supplies its own <!doctype>/<html>/<head>/<body> wrapper).
  - docs/index.html: the same content wrapped as a standalone document
    with home-screen/PWA tags, for GitHub Pages hosting.
"""

from __future__ import annotations
import argparse
import base64
import json
from pathlib import Path

from generate_board_data import build_board

ROOT = Path(__file__).parent
FONTS = ROOT / "fonts"
DOCS = ROOT / "docs"

# Set this once the always-on live-odds server (see live_server/) is
# deployed -- e.g. "https://fantasize-live-xxxx.onrender.com". Left as a
# placeholder until then; the frontend detects the placeholder and simply
# skips live polling, so pregame betting is unaffected either way.
LIVE_SERVER_URL = "https://kenan-l555.onrender.com"

STANDALONE_HEAD_EXTRA = """
<link rel="manifest" href="./manifest.json" />
<link rel="icon" href="./favicon.png" />
<link rel="apple-touch-icon" href="./icon-180.png" />
<meta name="apple-mobile-web-app-capable" content="yes" />
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />
<meta name="apple-mobile-web-app-title" content="Fantasize" />
<meta name="theme-color" content="#8b3ff0" />
"""


def b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--live", action="store_true",
        help="Pull live Sleeper projections/injury status (+ weather where in forecast range) before building.",
    )
    args = parser.parse_args()

    template = (ROOT / "sportsbook.template.html").read_text()

    template = template.replace("__BEBAS_B64__", b64(FONTS / "bebasneue.subset.woff2"))
    template = template.replace("__INTER_B64__", b64(FONTS / "inter.subset.woff2"))
    template = template.replace("__JBMONO_B64__", b64(FONTS / "jbmono.subset.woff2"))
    template = template.replace("__BOARD_DATA_JSON__", json.dumps(build_board(live=args.live)))
    template = template.replace("__LIVE_SERVER_URL__", LIVE_SERVER_URL)

    (ROOT / "sportsbook.html").write_text(template)
    print(f"Wrote sportsbook.html ({(ROOT / 'sportsbook.html').stat().st_size / 1024:.0f} KB)")

    DOCS.mkdir(exist_ok=True)
    # Reuse the fragment as-is from its <style> block onward, prefixed with a
    # real doctype/html/head open tag plus the extra home-screen tags, and
    # closed at the end. HTML5 parsing auto-inserts the </head><body>...
    # </body> boundaries at the right points either way.
    body_start = template.index("<style>")
    standalone = (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n"
        "<meta charset=\"utf-8\" />\n"
        "<title>Fantasize</title>\n"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />\n"
        + STANDALONE_HEAD_EXTRA
        + template[body_start:]
        + "\n</html>\n"
    )
    (DOCS / "index.html").write_text(standalone)
    print(f"Wrote docs/index.html ({(DOCS / 'index.html').stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
