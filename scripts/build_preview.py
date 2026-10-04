"""Build preview/bangarpet-demo-preview.html: a single self-contained HTML snapshot of the
homepage (CSS, fonts, icons, images and scripts inlined) that opens without a server.

Usage: start the site (./run_local.sh), then in another terminal:
    .venv/bin/python scripts/build_preview.py
"""
import base64
import mimetypes
import os
import re
from pathlib import Path
import requests

BASE = os.environ.get("PREVIEW_BASE_URL", "http://127.0.0.1:8000")
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "preview" / "bangarpet-demo-preview.html"
cache = {}

def fetch(url):
    if url not in cache:
        r = requests.get(BASE + url if url.startswith("/") else url, timeout=20)
        r.raise_for_status()
        cache[url] = r
    return cache[url]

def data_uri(url):
    r = fetch(url)
    mime = r.headers.get("Content-Type", "").split(";")[0] or mimetypes.guess_type(url)[0] or "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(r.content).decode()}"

def inline_css(url):
    css = fetch(url).text
    css = re.sub(r"/\*# sourceMappingURL=.*?\*/", "", css)
    folder = url.rsplit("/", 1)[0]
    def repl(m):
        ref = m.group(2).split("?")[0].split("#")[0]
        if ref.startswith("data:"):
            return m.group(0)
        parts = (folder + "/" + ref).split("/")
        stack = []
        for p in parts:
            if p == "..": stack.pop()
            elif p != ".": stack.append(p)
        return f'url("{data_uri("/".join(stack))}")'
    # Only woff2 for icon fonts (drop the woff fallback to save space)
    css = re.sub(r',\s*url\("[^"]*bootstrap-icons\.woff\?[^"]*"\)\s*format\("woff"\)', "", css)
    return re.sub(r'url\((["\']?)([^)"\']+)\1\)', repl, css)

html = fetch("/").text

# Stylesheets -> inline <style>
def css_tag(m):
    return f"<style>\n{inline_css(m.group(1))}\n</style>"
html = re.sub(r'<link rel="stylesheet" href="([^"]+)">', css_tag, html)
# Drop preload/icon/canonical links that point at the server
html = re.sub(r'<link rel="(preload|icon|apple-touch-icon|canonical)"[^>]*>\n?', "", html)
html = re.sub(r'<meta property="og:[^>]*>\n?|<meta name="twitter:[^>]*>\n?', "", html)

# Scripts -> inline
def js_tag(m):
    js = re.sub(r"//# sourceMappingURL=.*", "", fetch(m.group(1)).text)
    return f"<script>\n{js}\n</script>"
html = re.sub(r'<script src="(/static/[^"]+)" defer></script>', js_tag, html)

# Images (static + media) -> data URIs
html = re.sub(r'(src)="(/(?:static|media)/[^"]+)"', lambda m: f'{m.group(1)}="{data_uri(m.group(2))}"', html)

# CSRF tokens are meaningless offline
html = re.sub(r'<input type="hidden" name="csrfmiddlewaretoken"[^>]*>', "", html)

# Internal links / form actions: keep in-page anchors, disable the rest
html = re.sub(r'href="/(?!/)[^"]*"', 'href="#" data-preview-link', html)
html = re.sub(r'action="/[^"]*"', 'action="#"', html)

banner = (
    '<div style="position:sticky;top:0;z-index:2000;background:#FFD600;color:#172B4D;font:600 14px/1.4 Inter,system-ui,sans-serif;'
    'text-align:center;padding:8px 12px">Static preview of the Bangarpet Property Hub homepage with [DEMO] data. '
    'Links and forms are disabled here &mdash; run <code style="color:inherit">run_local.bat</code> / <code style="color:inherit">./run_local.sh</code> for the full working site.</div>'
)
html = html.replace('<a class="skip-link"', banner + '\n  <a class="skip-link"', 1)

guard = """<script>
document.addEventListener('click', function (e) {
  var a = e.target.closest('a[data-preview-link]');
  if (a) { e.preventDefault(); previewNotice(); }
}, true);
document.addEventListener('submit', function (e) { e.preventDefault(); e.stopImmediatePropagation(); previewNotice(); }, true);
function previewNotice() {
  var t = document.getElementById('preview-toast');
  if (!t) {
    t = document.createElement('div'); t.id = 'preview-toast'; t.setAttribute('role', 'status');
    t.style.cssText = 'position:fixed;left:50%;bottom:90px;transform:translateX(-50%);z-index:3000;background:#172B4D;color:#fff;padding:12px 18px;border-radius:12px;font:500 14px Inter,system-ui,sans-serif;box-shadow:0 10px 30px rgba(0,0,0,.25);max-width:90vw;text-align:center';
    document.body.appendChild(t);
  }
  t.textContent = 'This is a static preview. Run the demo locally to use links, search and forms.';
  t.style.display = 'block'; clearTimeout(t._h); t._h = setTimeout(function () { t.style.display = 'none'; }, 3000);
}
</script>"""
html = html.replace("</body>", guard + "\n</body>", 1)
html = html.replace("<title>", "<title>[Preview] ", 1)

OUT.write_text(html, encoding="utf-8")
print(OUT, f"{OUT.stat().st_size/1024/1024:.2f} MB", "assets inlined:", len(cache))
leftover = re.findall(r'(?:src|href)="/(?:static|media)/[^"]+"', html)
print("server references left:", len(leftover), leftover[:3])
