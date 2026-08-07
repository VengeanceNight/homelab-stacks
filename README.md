# homelab-stacks

Docker Compose stacks for Ken's Portainer/Docker guest on Proxmox, deployed via
`docker compose` on the guest itself (Portainer auto-detects them as Stacks
off the Docker socket — no manual "Add stack" clicking needed).

## Host layout

- **Docker/Portainer guest:** Ubuntu 24.04 VM/LXC on Proxmox, `192.168.1.8`.
  SSH as `ken` (key-based). Portainer UI: `http://192.168.1.8:9000`.
- **Media/downloads storage:** TrueNAS at `192.168.1.2`, NFS export
  `/mnt/Alpha/Eagle1`, mounted on the guest at `/mnt/eagle1` (already in
  `/etc/fstab` there — `nfs defaults,nofail,_netdev`).
- **Container configs:** local to the guest under `/opt/<app>/config`
  (bind mount), not on the NAS. Servarr/Plex configs are SQLite-backed —
  keeping them local avoids NFS locking/corruption risk. Same convention
  already used by the guest's pre-existing `qbittorrent`/`syncthing`
  containers.
- **UID/GID:** `PUID=1000`/`PGID=1000` (user `ken` on the NAS and the
  guest) so file ownership matches across NFS.

## arrr-stack

Radarr, Sonarr, SABnzbd, NZBHydra2, Plex. Rebuilt 2026-08-07 after the
prior stack (built by hand in Portainer's UI, no compose file) was lost.

- Radarr/Sonarr/SABnzbd mount the **whole** `/mnt/eagle1` NFS share to
  `/data`, so downloads and the media library are on one filesystem —
  Radarr/Sonarr can hardlink completed downloads into the library instead
  of copying (faster, no duplicate storage).
  - Radarr root folder: `/data/Plex/Movies`
  - Sonarr root folder: `/data/Plex/TV`
  - SABnzbd download folder: `/data/downloads/usenet` (new subfolder;
    `/data/downloads` is qBittorrent's existing flat download root on a
    separate, unrelated stack — kept out of scope here)
- NZBHydra2 sits in front of SABnzbd as the indexer meta-search
  (Newznab-compatible API for Radarr/Sonarr).
- Plex runs with `network_mode: host` (needed for LAN auto-discovery/DLNA
  and cleaner remote access) and points at `/data/movies`, `/data/tv`,
  `/data/music` mapped from `/mnt/eagle1/Plex/{Movies,TV,Music}`.
- Old `Hydra/config` and `Plex/config` folders on the NAS are stale/empty
  leftovers from the lost stack — not reused. Old `sabnzbd/downloads`
  (50G) and `torrent/downloads` (155G) folders are historical data, left
  in place but not mounted into anything here.
- `Plex/TV Shows` (a smaller secondary folder with a leftover `test`
  subfolder) was deliberately left out of the Plex library for now.

### Ports

| Service | Port |
|---|---|
| Radarr | 7878 |
| Sonarr | 8989 |
| SABnzbd | 8081 (8080 is already qBittorrent's) |
| NZBHydra2 | 5076 |
| Plex | 32400 (host networking) |

### Deploy

```bash
ssh 192.168.1.8
cd /opt/arrr-stack && docker compose up -d
```

### First-run setup (manual — not scriptable)

1. SABnzbd: run the setup wizard, add Usenet provider.
2. NZBHydra2: add indexers (e.g. an existing Usenet indexer subscription).
3. Plex: claim the server, add libraries pointed at `/data/movies`,
   `/data/tv`, `/data/music`.

Each of these needs your own credentials/subscription details, so it's
manual by nature — everything *after* this step (wiring the apps
together) is scripted, see below.

### Wiring the apps together

```bash
pip install requests   # if not already available
python3 arrr-stack/wire_stack.py --host 192.168.1.8
```

Requires SSH access to the guest (reads each app's own generated API key
from its config — Radarr/Sonarr's `config.xml`, SABnzbd's `sabnzbd.ini`,
NZBHydra2's own `/internalapi/config`, Plex's `Preferences.xml`; no keys
are ever hardcoded or committed). Safe to re-run — existing entries just
error with "Should be unique," which is expected. Sets up:

- Radarr/Sonarr → SABnzbd as the download client, NZBHydra2 as the
  indexer, root folders (`/data/Plex/Movies` / `/data/Plex/TV`)
- Radarr/Sonarr → Plex "connect" notification, so a completed import
  triggers an immediate library refresh instead of waiting on Plex's own
  scan interval — needed because Plex's real-time file watching doesn't
  reliably fire over NFS mounts
- NZBHydra2 → SABnzbd as its own downloader, so manually searching and
  grabbing a result inside Hydra's UI (outside of Radarr/Sonarr) sends
  straight to SABnzbd

**Real gotcha hit doing this the first time:** SABnzbd has a
`host_whitelist` security setting that rejects requests by unrecognized
`Host` header — the container-name hostname (`sabnzbd`) that
Radarr/Sonarr/Hydra use internally isn't whitelisted by default, so
every connection attempt 403s until it's added. The script handles this
automatically now (`ensure_sabnzbd_whitelist`), but worth knowing if you
ever add another service that needs to talk to SABnzbd by container
name and see the same 403.

### Not part of this stack

- **qBittorrent** — already running as its own separate stack on this
  guest (`/opt/qbittorrent`), used mainly for audiobooks/ebooks. Ken
  wants to build that out further (Readarr or similar) as a later,
  separate project.
