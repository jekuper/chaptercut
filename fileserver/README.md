# filexchange

A small authenticated file drop. The bot uploads a finished file, the server
returns a one-off HTTPS link, and the file is deleted after a retention window.

It exists because Telegram's download speed is the bottleneck, not its size
limit — the local Bot API server already solved the size limit. This is a
*delivery destination*, chosen per job, not a workaround.

Runs as a plain systemd service. No container.

## What it does

- `POST /upload` — bearer-authenticated, raw streamed body, size-capped.
- `GET /d/{token}/{filename}` — the link. The 256-bit token *is* the
  credential, so no login is needed to download.
- `GET /admin/stats`, `GET /admin/files`, `DELETE /admin/files`,
  `DELETE /admin/files/{token}` — bearer-authenticated; these back the bot's
  `/files` command.
- `GET /healthz` — open, returns `{"ok": true}` and nothing else.

Uploads land in a staging directory and are published by an atomic rename, so
a link can never point at a half-written file. Anything left staged by an
interrupted upload is swept.

## Install

On the server machine, as root unless noted.

**1. User, directories, code**

```bash
useradd --system --home /opt/filexchange --shell /usr/sbin/nologin filexchange
mkdir -p /opt/filexchange /etc/filexchange/certs /var/lib/filexchange
chown filexchange:filexchange /var/lib/filexchange
chmod 700 /var/lib/filexchange

git clone https://github.com/jekuper/chaptercut /opt/filexchange
cd /opt/filexchange
```

**2. Dependencies**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
~/.local/bin/uv sync --no-default-groups --group fileserver
chown -R filexchange:filexchange /opt/filexchange
```

Only the server's dependencies are installed; the bot's are not.

**3. TLS**

You have no domain, so the certificate is issued for this machine's IP.

```bash
./fileserver/gen-cert.sh <this-machine-ip>          # self-signed
./fileserver/gen-cert.sh --ca <this-machine-ip>     # private CA + leaf

install -o filexchange -g filexchange -m 600 certs/server.key /etc/filexchange/certs/
install -o filexchange -g filexchange -m 644 certs/server.crt /etc/filexchange/certs/
```

Copy the trust anchor to the **bot** machine — `server.crt` in self-signed
mode, `ca.crt` in CA mode — and point `CC_FILESERVER_CA` at it. It is a public
certificate, not a secret. Never copy a `.key` anywhere.

**4. Configuration**

```bash
openssl rand -base64 48        # the upload token; put the same value on the bot

install -m 600 -o root -g root fileserver/.env.example \
    /etc/filexchange/filexchange.env
editor /etc/filexchange/filexchange.env
```

`FX_PUBLIC_URL` must be the address clients can actually reach, including the
port, e.g. `https://203.0.113.5:8443`. Links are built from it.

The file stays `600 root:root`: systemd reads it as root and drops privileges
afterwards, so the service user never has access to the token.

**5. Start**

```bash
cp fileserver/filexchange.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now filexchange
systemctl status filexchange
journalctl -u filexchange -f
```

**6. Open the port**

```bash
ufw allow 8443/tcp
```

## Check it works

```bash
curl -k https://127.0.0.1:8443/healthz
# {"ok":true}

TOKEN=$(grep FX_UPLOAD_TOKEN /etc/filexchange/filexchange.env | cut -d= -f2-)
echo hello > /tmp/probe.txt
curl -k -X POST https://127.0.0.1:8443/upload \
     -H "Authorization: Bearer $TOKEN" \
     -H "X-Filename: probe.txt" \
     --data-binary @/tmp/probe.txt
```

`-k` skips verification, which is fine for a local probe against your own
certificate. The bot does **not** skip it.

## Bot side

On the bot machine, in its `.env`:

```
CC_FILESERVER_URL=https://203.0.113.5:8443
CC_FILESERVER_TOKEN=<the same token>
CC_FILESERVER_CA=/data/fileserver.crt
```

These are paths **inside the bot's container**, not host paths. A host path
like `/home/you/server.crt` does not exist in there, and the bot will report it
as missing even though you can `cat` it from your shell.

So copy the trust anchor from step 3 into the bot's `data/` directory, which is
owned by uid 10001 and therefore needs sudo:

```bash
cd ~/chaptercut
sudo cp ~/server.crt data/fileserver.crt
sudo chown 10001:10001 data/fileserver.crt
sudo chmod 644 data/fileserver.crt
docker compose up -d bot     # up, not restart: .env changed
``` With all three set, jobs gain a
`Telegram` / `Direct link` choice, and anything too big for Telegram
automatically reroutes here. Leave `CC_FILESERVER_URL` empty and the whole
feature disappears, including the extra button.

## Operating it from the bot

Admin-only:

- `/files` — count, total size, retention, and the stored files
- `/files purge <token>` — delete one
- `/files purge all` — flush everything
- `/status` — includes whether the server is reachable

## Security notes

- The upload token is the only credential and lives in a `600 root:root`
  environment file. It is never logged.
- The bot verifies the server's certificate against a pinned PEM. There is no
  option to disable that: without verification the TLS would be decoration and
  the token would go to whoever answered the connection.
- Download links are 256-bit capability URLs. Anyone holding one can fetch the
  file until it expires, so treat a link as the file itself.
- Filenames are flattened to a single safe segment, and resolved paths are
  checked against the uploads root, so nothing a caller sends can escape it.
- Uploads are capped by a byte counter while streaming, not by the
  `Content-Length` header, which is only a claim.
- The systemd unit runs unprivileged with `ProtectSystem=strict` and one
  writable path.

## A self-signed certificate is not the same as a verified one

In self-signed mode a browser warns on every visit and you click through,
which means passive eavesdroppers are blocked but an active attacker on the
path is not. The bot is safe either way, because it pins the certificate.

For the browser to be safe too, use `--ca` mode and install `ca.crt` as a
trusted root on your phone and laptop. Same script, one flag, no code change.
