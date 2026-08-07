#!/usr/bin/env python3
"""
Wires up Radarr/Sonarr/SABnzbd/NZBHydra2/Plex after `docker compose up -d`.

Configures things the compose file can't: Radarr/Sonarr's download client
(SABnzbd) and indexer (NZBHydra2), their root folders, Plex "connect"
notifications (so imports trigger an immediate Plex refresh instead of
waiting on Plex's own scan -- NFS mounts don't reliably support Plex's
real-time file watching), and NZBHydra2's own downloader (so manual
searches inside Hydra's UI can send straight to SABnzbd).

Requires each app's first-run setup already done (SABnzbd wizard + news
server, NZBHydra2 indexer added, Plex claimed) -- this only wires the
apps *together*, it doesn't do that initial per-app setup.

API keys are fetched live from each app -- never hardcoded/committed.
Safe to re-run: existing entries with the same name will error with
"Should be unique" and can be ignored, or delete the entry in the app's
UI first if you want to redo it with different settings.

Usage: python3 wire_stack.py [--host 192.168.1.8]
"""
import argparse
import json
import subprocess

import requests


def ssh(host, cmd):
    return subprocess.run(
        ["ssh", host, cmd], capture_output=True, text=True, check=True
    ).stdout.strip()


def get_radarr_key(host):
    return ssh(host, "grep -o '<ApiKey>[^<]*</ApiKey>' /opt/radarr/config/config.xml").split(
        ">"
    )[1].split("<")[0]


def get_sonarr_key(host):
    return ssh(host, "grep -o '<ApiKey>[^<]*</ApiKey>' /opt/sonarr/config/config.xml").split(
        ">"
    )[1].split("<")[0]


def get_sabnzbd_key(host):
    line = ssh(host, "grep -m1 '^api_key' /opt/sabnzbd/config/sabnzbd.ini")
    return line.split("=", 1)[1].strip()


def get_hydra_key(base):
    return requests.get(f"{base}/internalapi/config").json()["main"]["apiKey"]


def get_plex_token(host):
    out = ssh(
        host,
        'grep -o \'PlexOnlineToken="[^"]*"\' '
        '"/opt/plex/config/Library/Application Support/Plex Media Server/Preferences.xml"',
    )
    return out.split('"')[1]


def h(key):
    return {"X-Api-Key": key, "Content-Type": "application/json"}


def ensure_sabnzbd_whitelist(sab_base, sab_key, host, entries):
    """SABnzbd rejects requests whose Host header isn't whitelisted -- the
    container-name hostname other services use internally needs adding, or
    they get 403 Forbidden."""
    current = requests.get(
        f"{sab_base}/api?mode=get_config&section=misc&keyword=host_whitelist"
        f"&apikey={sab_key}&output=json"
    ).json()["config"]["misc"]["host_whitelist"]
    merged = sorted(set(current) | set(entries))
    if set(merged) != set(current):
        requests.get(
            f"{sab_base}/api?mode=set_config&section=misc&keyword=host_whitelist"
            f"&value={','.join(merged)}&apikey={sab_key}&output=json"
        )
        print("  updated SABnzbd host_whitelist:", merged)
    else:
        print("  SABnzbd host_whitelist already covers", entries)


def add_downloadclient(base, key, sab_apikey, category_field, category):
    schema = requests.get(f"{base}/api/v3/downloadclient/schema", headers=h(key)).json()
    tmpl = next(c for c in schema if c["implementation"] == "Sabnzbd")
    tmpl["name"] = "SABnzbd"
    tmpl["enable"] = True
    for f in tmpl["fields"]:
        if f["name"] == "host":
            f["value"] = "sabnzbd"
        elif f["name"] == "port":
            f["value"] = 8080
        elif f["name"] == "apiKey":
            f["value"] = sab_apikey
        elif f["name"] == category_field:
            f["value"] = category
    r = requests.post(f"{base}/api/v3/downloadclient", headers=h(key), json=tmpl)
    print("  downloadclient ->", r.status_code, "" if r.ok else r.text[:300])


def add_indexer(base, key, hydra_apikey, categories):
    schema = requests.get(f"{base}/api/v3/indexer/schema", headers=h(key)).json()
    tmpl = next(c for c in schema if c["implementation"] == "Newznab")
    tmpl["name"] = "NZBHydra2"
    tmpl["enableRss"] = True
    tmpl["enableAutomaticSearch"] = True
    tmpl["enableInteractiveSearch"] = True
    for f in tmpl["fields"]:
        if f["name"] == "baseUrl":
            f["value"] = "http://nzbhydra2:5076"
        elif f["name"] == "apiPath":
            f["value"] = "/api"
        elif f["name"] == "apiKey":
            f["value"] = hydra_apikey
        elif f["name"] == "categories":
            f["value"] = categories
    r = requests.post(f"{base}/api/v3/indexer", headers=h(key), json=tmpl)
    print("  indexer ->", r.status_code, "" if r.ok else r.text[:300])


def ensure_rootfolder(base, key, path):
    existing = requests.get(f"{base}/api/v3/rootfolder", headers=h(key)).json()
    if any(rf["path"] == path for rf in existing):
        print(f"  rootfolder {path} already exists")
        return
    r = requests.post(f"{base}/api/v3/rootfolder", headers=h(key), json={"path": path})
    print("  rootfolder ->", r.status_code, "" if r.ok else r.text[:300])


def add_plex_connect(base, key, plex_host, plex_token):
    schema = requests.get(f"{base}/api/v3/notification/schema", headers=h(key)).json()
    tmpl = next(c for c in schema if c["implementation"] == "PlexServer")
    tmpl["name"] = "Plex Media Server"
    tmpl["onGrab"] = False
    tmpl["onDownload"] = True
    tmpl["onUpgrade"] = True
    tmpl["onRename"] = True
    for f in tmpl["fields"]:
        if f["name"] == "host":
            f["value"] = plex_host
        elif f["name"] == "port":
            f["value"] = 32400
        elif f["name"] == "useSsl":
            f["value"] = False
        elif f["name"] == "authToken":
            f["value"] = plex_token
    tmpl = {k: v for k, v in tmpl.items() if v is not None}
    r = requests.post(f"{base}/api/v3/notification", headers=h(key), json=tmpl)
    print("  Plex connect ->", r.status_code, "" if r.ok else r.text[:300])


def wire_hydra_downloader(hydra_base, sab_apikey):
    cfg = requests.get(f"{hydra_base}/internalapi/config").json()
    cfg["downloading"]["downloaders"] = [
        {
            "name": "SABnzbd",
            "downloaderType": "SABNZBD",
            # Required -- NZBHydra2's SafeDownloaderConfig calls
            # downloadType.name() unconditionally when rendering *any*
            # page (not just downloader settings), so leaving this unset
            # doesn't just fail validation, it 500s the entire web UI
            # (NullPointerException in SafeDownloaderConfig.java) until
            # fixed. Learned this the hard way on 2026-08-07.
            "downloadType": "NZB",
            "enabled": True,
            "url": "http://sabnzbd:8080",
            "apiKey": sab_apikey,
            "defaultCategory": "",
            "nzbAddingType": "UPLOAD",
            "addPaused": False,
        }
    ]
    cfg["downloading"]["primaryDownloader"] = "SABnzbd"
    r = requests.put(f"{hydra_base}/internalapi/config", json=cfg)
    resp = r.json()
    print("  Hydra downloader -> ok:", resp.get("ok"), "errors:", resp.get("errorMessages"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="192.168.1.8", help="Docker/Portainer guest address")
    args = ap.parse_args()
    host = args.host

    radarr = f"http://{host}:7878"
    sonarr = f"http://{host}:8989"
    sabnzbd = f"http://{host}:8081"
    hydra = f"http://{host}:5076"

    radarr_key = get_radarr_key(host)
    sonarr_key = get_sonarr_key(host)
    sab_key = get_sabnzbd_key(host)
    hydra_key = get_hydra_key(hydra)
    plex_token = get_plex_token(host)

    print("=== SABnzbd host whitelist ===")
    ensure_sabnzbd_whitelist(sabnzbd, sab_key, host, ["sabnzbd"])

    print("=== Radarr ===")
    add_downloadclient(radarr, radarr_key, sab_key, "movieCategory", "movies")
    add_indexer(radarr, radarr_key, hydra_key, "2000,2010,2020,2030,2040,2045,2050,2060")
    ensure_rootfolder(radarr, radarr_key, "/data/Plex/Movies")
    add_plex_connect(radarr, radarr_key, host, plex_token)

    print("=== Sonarr ===")
    add_downloadclient(sonarr, sonarr_key, sab_key, "tvCategory", "tv")
    add_indexer(sonarr, sonarr_key, hydra_key, "5000,5010,5020,5030,5040,5045,5050,5060")
    ensure_rootfolder(sonarr, sonarr_key, "/data/Plex/TV")
    add_plex_connect(sonarr, sonarr_key, host, plex_token)

    print("=== NZBHydra2 -> SABnzbd (for manual searches inside Hydra's UI) ===")
    wire_hydra_downloader(hydra, sab_key)


if __name__ == "__main__":
    main()
