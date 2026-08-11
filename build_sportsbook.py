"""
Assembles sportsbook.html from sportsbook.template.html + the current
odds board. Run this after updating projections in odds_model.py:

    python3 build_sportsbook.py

Regenerates board data via generate_board_data.py's build_board() and
splices it into the template alongside the embedded (subsetted, base64)
webfonts, then writes sportsbook.html.
"""

from __future__ import annotations
import base64
import json
from pathlib import Path

from generate_board_data import build_board

ROOT = Path(__file__).parent
FONTS = ROOT / "fonts"


def b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def main() -> None:
    template = (ROOT / "sportsbook.template.html").read_text()

    template = template.replace("__ARCHIVO_B64__", b64(FONTS / "archivoblack.subset.woff2"))
    template = template.replace("__INTER_B64__", b64(FONTS / "inter.subset.woff2"))
    template = template.replace("__JBMONO_B64__", b64(FONTS / "jbmono.subset.woff2"))
    template = template.replace("__BOARD_DATA_JSON__", json.dumps(build_board()))

    (ROOT / "sportsbook.html").write_text(template)
    print(f"Wrote sportsbook.html ({(ROOT / 'sportsbook.html').stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
