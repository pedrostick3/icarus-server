"""Latest published build of the Icarus dedicated server on Steam."""

import json
import urllib.request

APP_ID = 2089300
API_URL = f"https://api.steamcmd.net/v1/info/{APP_ID}"


def latest_buildid(branch="public", timeout=20):
    req = urllib.request.Request(API_URL, headers={"User-Agent": "icarus-manager"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.load(resp)
    branches = data["data"][str(APP_ID)]["depots"]["branches"]
    return str(branches[branch or "public"]["buildid"])
