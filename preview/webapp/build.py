"""Assemble the single-file web app demo.

1. python manage.py shell < preview/webapp/export_data.py   (writes data.json from the demo database)
2. python preview/webapp/build.py                           (writes preview/webapp/dist/bangarpet-web-app.html)
"""
import base64
import json
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
ICONS_DIR = ROOT / "static" / "vendor" / "bootstrap-icons"

template = (HERE / "app_template.html").read_text(encoding="utf-8")
data = json.loads((HERE / "data.json").read_text(encoding="utf-8"))

# Map icon names to code points from the vendored Bootstrap Icons CSS.
css_src = (ICONS_DIR / "bootstrap-icons.min.css").read_text(encoding="utf-8")
codepoints = dict(re.findall(r"\.bi-([a-z0-9-]+)::before\{content:\"\\\\?([0-9a-f]+)\"\}", css_src))

# Icons used by the template: any "bi-name" class or quoted string that is a real icon name
# (covers icon("x"), ternaries and icon names kept in arrays), plus icons named in the data.
names = set(re.findall(r"bi-([a-z0-9-]+)", template))
names |= {n for n in re.findall(r'"([a-z0-9-]+)"', template) if n in codepoints}
for group in ("categories", "amenityOptions"):
    names |= {item["icon"] for item in data[group] if item.get("icon")}
for listing in data["listings"]:
    names |= {a["icon"] for a in listing["amenities"] if a.get("icon")}
names -= {"bi"}

missing = sorted(n for n in names if n not in codepoints)
woff2 = base64.b64encode((ICONS_DIR / "fonts" / "bootstrap-icons.woff2").read_bytes()).decode()
icon_css = [
    '@font-face{font-display:block;font-family:"bootstrap-icons";src:url("data:font/woff2;base64,%s") format("woff2")}' % woff2,
    '.bi::before{display:inline-block;font-family:bootstrap-icons!important;font-style:normal;font-weight:400!important;'
    'font-variant:normal;text-transform:none;line-height:1;vertical-align:-.125em;-webkit-font-smoothing:antialiased}',
]
icon_css += [f'.bi-{n}::before{{content:"\\{codepoints[n]}"}}' for n in sorted(names) if n in codepoints]

logo = (ROOT / "static" / "img" / "logo-mark.svg").read_text(encoding="utf-8")
logo = re.sub(r'\s*role="img"\s*aria-label="[^"]*"', ' aria-hidden="true" focusable="false"', logo).strip()

payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
html = (template.replace("/*__ICON_CSS__*/", "\n".join(icon_css))
        .replace("__LOGO_SVG__", logo)
        .replace("__DATA_JSON__", payload))

out = HERE / "dist" / "bangarpet-web-app.html"
out.parent.mkdir(exist_ok=True)
out.write_text(html, encoding="utf-8")
print(f"wrote {out} ({out.stat().st_size / 1024 / 1024:.2f} MB), {len(names)} icons", "missing:", missing)
