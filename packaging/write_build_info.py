"""Run before PyInstaller. Stamps the release version + update-feed URL into the package and writes the exe's
Windows version resource (packaging/version_info.txt).

    python packaging/write_build_info.py --version 1.2.3 --feed https://example.com/latest.json
"""
import argparse
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
APP_NAME = "Lookout"
EXE_NAME = "Lookout"


def render_build_info(version, feed, api_key="", project="", region="europe-west2"):
    return ('"""Written by packaging/write_build_info.py for this build."""\n'
            f'VERSION = "{version}"\n'
            f'UPDATE_FEED_URL = "{feed}"\n'
            f'CLOUD_API_KEY = "{api_key}"\n'
            f'CLOUD_PROJECT = "{project}"\n'
            f'CLOUD_REGION = "{region}"\n')


def render_version_info(version):
    a, b, c = (int(x) for x in version.split("."))
    return f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers=({a}, {b}, {c}, 0), prodvers=({a}, {b}, {c}, 0), mask=0x3f, flags=0x0,
                    OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', ''),
      StringStruct('FileDescription', '{APP_NAME}'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('InternalName', '{EXE_NAME}'),
      StringStruct('OriginalFilename', '{EXE_NAME}.exe'),
      StringStruct('ProductName', '{APP_NAME}'),
      StringStruct('ProductVersion', '{version}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True, help="x.y.z")
    ap.add_argument("--feed", default="", help="URL of latest.json (empty = updates off)")
    ap.add_argument("--cloud-api-key", default="", help="Firebase web API key (empty = shared watchlist off)")
    ap.add_argument("--cloud-project", default="", help="Firebase project id")
    ap.add_argument("--cloud-region", default="europe-west2")
    a = ap.parse_args()
    for v in (a.cloud_api_key, a.cloud_project, a.cloud_region):
        if not re.fullmatch(r"[A-Za-z0-9_-]*", v):
            ap.error("cloud settings may only contain letters, digits, - and _")
    if not re.fullmatch(r"\d+\.\d+\.\d+", a.version):
        ap.error("version must look like 1.2.3")
    if a.feed and not a.feed.startswith("https://"):
        ap.error("feed must be an https:// URL")
    with open(os.path.join(ROOT, "roblox_tracker", "build_info.py"), "w", encoding="utf-8") as f:
        f.write(render_build_info(a.version, a.feed, a.cloud_api_key, a.cloud_project, a.cloud_region))
    with open(os.path.join(HERE, "version_info.txt"), "w", encoding="utf-8") as f:
        f.write(render_version_info(a.version))
    print(f"stamped version {a.version}" + (f", feed {a.feed}" if a.feed else ", no update feed"))


if __name__ == "__main__":
    main()
