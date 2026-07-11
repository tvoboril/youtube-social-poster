# YouTube → Facebook live stream auto-poster

When the **St. George Melkite** YouTube channel goes live, this service automatically
publishes a post on the parish **Facebook Page** that links directly to the live video.

It listens for YouTube's free WebSub (PubSubHubbub) push notifications, confirms the video
is actually live via the YouTube Data API, then posts to the Page with the Graph API. Video
IDs are de-duplicated in SQLite so you never get double posts.

```
YouTube goes live ──push──▶ /youtube/webhook ──is it live?──▶ post to Facebook Page
                                    │                              (links to the video)
                              dedupe (SQLite)
```

## 1. Get your credentials

You need four secrets. Put them in a `.env` file (copy `.env.example`).

### YouTube channel ID (`YT_CHANNEL_ID`)
Resolve the `@saintgeorgemelkite` handle to a `UC...` id once:
```bash
curl "https://www.googleapis.com/youtube/v3/channels?part=id&forHandle=saintgeorgemelkite&key=YOUR_API_KEY"
```

### YouTube Data API key (`YT_API_KEY`)
1. Go to https://console.cloud.google.com/ → create/select a project.
2. **APIs & Services → Enable APIs** → enable **YouTube Data API v3**.
3. **Credentials → Create credentials → API key**. Copy it.

### Facebook Page ID + token (`FB_PAGE_ID`, `FB_PAGE_TOKEN`)
Because the parish administers its own Page, you can post **without full App Review** by
keeping the app in Development Mode and using a token for your own Page.

1. Go to https://developers.facebook.com/ → **My Apps → Create App** → type **Business**.
2. In the app, add yourself (the parish admin account) as an app **Admin/Developer**
   (Roles → Roles). Keep the app in **Development** mode.
3. Open **Graph API Explorer** (Tools menu). Select your app, and request permissions
   `pages_manage_posts` and `pages_read_engagement`. Generate a **User** token.
4. Get your **Page ID** and a **Page token**:
   ```
   GET /me/accounts        # returns your Pages, each with "id" and "access_token"
   ```
   The `id` is `FB_PAGE_ID`; the `access_token` is a Page token.
5. Make the token **long-lived** so it doesn't expire:
   - Exchange the short-lived user token for a long-lived one:
     `GET /oauth/access_token?grant_type=fb_exchange_token&client_id=APP_ID&client_secret=APP_SECRET&fb_exchange_token=SHORT_USER_TOKEN`
   - Call `/me/accounts` again **with the long-lived user token**. The Page token you get
     back does **not** expire. Use that as `FB_PAGE_TOKEN`.

### WebSub secret (`WEBSUB_SECRET`)
Any long random string, e.g. `openssl rand -hex 32`. Used to verify that pushes really
come from YouTube's hub.

## 2. Expose the service publicly

YouTube's hub must reach `<PUBLIC_BASE_URL>/youtube/webhook` over HTTPS with a **stable**
URL (the app registers this callback on startup). Two supported options:

### Option A: Tailscale Funnel (no domain, no port-forwarding)
The app publishes on `127.0.0.1:8000` (see `docker-compose.yml`). Run Tailscale on the
host and funnel that port:
```bash
tailscale funnel --bg 8000
```
This serves the app at a stable `https://<machine>.<tailnet>.ts.net`. Set
`PUBLIC_BASE_URL` to that URL. Requires Funnel enabled for your tailnet (HTTPS + MagicDNS,
and the `funnel` node attribute in your ACL). In an unprivileged LXC, run tailscaled with
`--tun=userspace-networking` (Funnel/Serve work in userspace mode).

### Option B: Cloudflare Tunnel
Requires the domain's DNS to be hosted at Cloudflare.
1. Cloudflare Zero Trust → **Networks → Tunnels → Create a tunnel**; copy the token to
   `CLOUDFLARE_TUNNEL_TOKEN` in `.env`.
2. Add a **Public Hostname** route: `your-name.yourdomain.com` → `http://app:8000`.
3. Set `PUBLIC_BASE_URL=https://your-name.yourdomain.com`.
4. Start the extra service: `docker compose --profile cloudflare up -d`.

## 3. Run it

With Docker installed and `.env` filled in:
```bash
docker compose --profile cloudflare up -d --build   # omit --profile for Tailscale/other
docker compose logs -f app
```
Later updates: `./update.sh` (git pull + rebuild + restart).

On startup the app subscribes to the WebSub hub and re-subscribes automatically every
~4 days (the hub lease is ~5–10 days). SQLite lives in `./data` (mounted volume).

> **Note:** the hub verifies the subscription by calling your public URL back. If DNS/TLS
> for `PUBLIC_BASE_URL` isn't live yet (e.g. a brand-new domain still propagating),
> restart the app once it is so the subscription completes:
> `docker compose restart app`.

### Run locally without Docker
```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### Deploy on a Proxmox LXC (reusable pattern)
This is the setup used in production; repeat it per service.
1. **Create an unprivileged Debian 12 LXC** with Docker's nesting flags:
   ```bash
   pct create <vmid> local:vztmpl/debian-12-standard_12.12-1_amd64.tar.zst \
     --hostname youtube-social-poster --cores 1 --memory 512 --swap 512 \
     --rootfs <pool>:8 --net0 name=eth0,bridge=vmbr0,ip=dhcp,type=veth \
     --features nesting=1,keyctl=1 --unprivileged 1 --onboot 1 \
     --ssh-public-keys yourkey.pub
   pct start <vmid>
   ```
2. **Install Docker + git** inside it: `curl -fsSL https://get.docker.com | sh`.
3. **Clone this private repo** with a read-only deploy key: generate a key in the
   container (`ssh-keygen -t ed25519`), add the public key to the repo's
   *Settings → Deploy keys* (read-only), then
   `git clone git@github.com:tvoboril/youtube-social-poster /opt/youtube-social-poster`.
4. **Copy `.env`** (never committed) into `/opt/youtube-social-poster/`.
5. **Create a Cloudflare Tunnel** for the service (see Option B) and
   `docker compose --profile cloudflare up -d --build`.

## 4. Verify it works

- **Health:** `curl http://localhost:8000/health` → `{"status":"ok"}`.
- **Facebook post in isolation** (do this first):
  ```bash
  . .venv/bin/activate
  python -c "import asyncio; from app.facebook import post_to_page; \
    print(asyncio.run(post_to_page('Test post from the auto-poster')))"
  ```
  Confirm the post appears on the Page, then delete it.
- **Subscription:** check `docker compose logs app` for `WebSub verification ok` shortly
  after startup — that's YouTube confirming the subscription.
- **Real dry run:** start a short **unlisted** live stream on the channel. Within ~1 minute
  a Facebook post should appear linking to that video. (Unlisted streams still generate a
  feed push.)
- **Dedupe:** editing that video's title should **not** create a second post.

## Configuration reference

See `.env.example`. Notable options:
- `POST_TEMPLATE` — message text; `{url}` is replaced with the live video URL.
- `RENEW_INTERVAL_SECONDS` — how often to renew the WebSub lease (default ~4 days).
- `DB_PATH` — SQLite dedupe database location.

## Tests
```bash
pip install -r requirements-dev.txt
pytest
```

## Notes & limitations
- Posts only when a video's `liveBroadcastContent == "live"`. If you *schedule* streams
  ahead of time, the app re-checks "upcoming" videos periodically until they go live.
- Only publishes new posts; it does not edit or delete the post when the stream ends.
- Posting to Pages you don't administer would require full Meta App Review — not needed here.

## License

[MIT](LICENSE) © Tomas Voboril
