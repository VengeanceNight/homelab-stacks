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
2. NZBHydra2: add indexers, connect SABnzbd as the downloader.
3. Radarr/Sonarr: add NZBHydra2 as a Newznab indexer, add SABnzbd as the
   download client, set root folders as above.
4. Plex: claim the server, add libraries.

### Not part of this stack

- **qBittorrent** — already running as its own separate stack on this
  guest (`/opt/qbittorrent`), used mainly for audiobooks/ebooks. Ken
  wants to build that out further (Readarr or similar) as a later,
  separate project.
