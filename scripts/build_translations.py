"""Build locale/kn/LC_MESSAGES/django.po and django.mo from locale/kn_translations.py.

    pip install polib   # development only
    python scripts/build_translations.py

The compiled .mo file is committed, so production needs neither polib nor gettext.
"""
import datetime
import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

import django  # noqa: E402

django.setup()

import polib  # noqa: E402

from core.i18n_catalog import all_messages  # noqa: E402


def load_source():
    spec = importlib.util.spec_from_file_location("kn_translations", ROOT / "locale" / "kn_translations.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.KN, module.PLURALS, getattr(module, "DJANGO_TIME", [])


def main():
    kn, plurals, django_time = load_source()
    singular, plural_ids = all_messages()
    missing = sorted(m for m in singular if m not in kn and m != "---------")
    missing_plural = sorted(p for p in plural_ids if p not in plurals)

    po = polib.POFile()
    po.metadata = {
        "Project-Id-Version": "Bangarpet Property Hub",
        "PO-Revision-Date": datetime.date.today().isoformat(),
        "Language": "kn",
        "MIME-Version": "1.0",
        "Content-Type": "text/plain; charset=UTF-8",
        "Content-Transfer-Encoding": "8bit",
        "Plural-Forms": "nplurals=2; plural=(n != 1);",
    }
    for msgid in sorted(kn):
        po.append(polib.POEntry(msgid=msgid, msgstr=kn[msgid]))
    for (one, many), (kn_one, kn_many) in sorted(plurals.items()):
        po.append(polib.POEntry(msgid=one, msgid_plural=many, msgstr_plural={0: kn_one, 1: kn_many}))

    for ctx, msgid, msgid_plural, text in django_time:
        if msgid_plural:
            po.append(polib.POEntry(msgctxt=ctx, msgid=msgid, msgid_plural=msgid_plural,
                                    msgstr_plural={0: text[0], 1: text[1]}))
        else:
            po.append(polib.POEntry(msgctxt=ctx, msgid=msgid, msgstr=text))

    out = ROOT / "locale" / "kn" / "LC_MESSAGES"
    out.mkdir(parents=True, exist_ok=True)
    po.save(str(out / "django.po"))
    po.save_as_mofile(str(out / "django.mo"))
    print(f"Wrote {len(kn)} messages and {len(plurals)} plurals to {out}")
    if missing or missing_plural:
        print("\nStill in English (add these to locale/kn_translations.py):")
        for m in missing:
            print("  ", repr(m))
        for p in missing_plural:
            print("  ", repr(p))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
