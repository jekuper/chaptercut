#!/usr/bin/env bash
# Generate the TLS material for filexchange.
#
# You have no domain, so the certificate is issued for an IP address via a
# SAN entry. Two modes:
#
#   ./gen-cert.sh 203.0.113.5
#       One self-signed certificate. Works immediately; clients must be told
#       to trust server.crt explicitly, and a browser will warn every time.
#
#   ./gen-cert.sh --ca 203.0.113.5
#       A private CA plus a leaf certificate signed by it. Install ca.crt once
#       on each device and everything verifies normally, with no warnings and
#       real protection against an active attacker.
#
# Either way the bot trusts one PEM file: server.crt in the first mode,
# ca.crt in the second. Switching later is a config change, not a code change.
set -euo pipefail

# No-op on Linux. On Git Bash / MSYS, stops the shell rewriting "/CN=..." into
# a Windows path before openssl ever sees it.
export MSYS2_ARG_CONV_EXCL='*'

DAYS="${DAYS:-825}"
OUT_DIR="${OUT_DIR:-certs}"

usage() {
    echo "usage: $0 [--ca] <ip-or-hostname> [more-ips-or-hostnames...]" >&2
    exit 2
}

MODE="selfsigned"
if [[ "${1:-}" == "--ca" ]]; then
    MODE="ca"
    shift
fi
[[ $# -ge 1 ]] || usage

# Build the SAN list. An IP has to go in as IP:, a name as DNS:, or clients
# will not match it.
SAN=""
for host in "$@"; do
    if [[ "$host" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
        SAN+="IP:${host},"
    else
        SAN+="DNS:${host},"
    fi
done
SAN="${SAN%,}"
PRIMARY="$1"

mkdir -p "$OUT_DIR"
cd "$OUT_DIR"
umask 077

echo "mode:  $MODE"
echo "SAN:   $SAN"
echo "days:  $DAYS"
echo

if [[ "$MODE" == "ca" ]]; then
    if [[ ! -f ca.key ]]; then
        echo "==> creating private CA"
        openssl req -x509 -newkey rsa:4096 -sha256 -days 3650 -nodes \
            -keyout ca.key -out ca.crt \
            -subj "/CN=chaptercut private CA" \
            -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
            -addext "keyUsage=critical,keyCertSign,cRLSign"
    else
        echo "==> reusing existing ca.key / ca.crt"
    fi

    echo "==> creating leaf certificate for $PRIMARY"
    openssl req -newkey rsa:2048 -sha256 -nodes \
        -keyout server.key -out server.csr \
        -subj "/CN=${PRIMARY}"

    # A real file next to the output rather than process substitution or a
    # temp path: openssl cannot always read /dev/fd, and on Git Bash it cannot
    # see the shell's virtual /tmp either. A relative path works everywhere.
    EXT_FILE="leaf.ext"
    trap 'rm -f "$EXT_FILE"' EXIT
    {
        echo "subjectAltName=${SAN}"
        echo "basicConstraints=critical,CA:FALSE"
        echo "keyUsage=critical,digitalSignature,keyEncipherment"
        echo "extendedKeyUsage=serverAuth"
    } >"$EXT_FILE"

    openssl x509 -req -in server.csr -days "$DAYS" -sha256 \
        -CA ca.crt -CAkey ca.key -CAcreateserial \
        -out server.crt -extfile "$EXT_FILE"
    rm -f server.csr

    TRUST="ca.crt"
else
    echo "==> creating self-signed certificate for $PRIMARY"
    openssl req -x509 -newkey rsa:2048 -sha256 -days "$DAYS" -nodes \
        -keyout server.key -out server.crt \
        -subj "/CN=${PRIMARY}" \
        -addext "subjectAltName=${SAN}" \
        -addext "basicConstraints=critical,CA:FALSE" \
        -addext "keyUsage=critical,digitalSignature,keyEncipherment" \
        -addext "extendedKeyUsage=serverAuth"

    TRUST="server.crt"
fi

chmod 600 ./*.key
chmod 644 ./*.crt

echo
echo "wrote $(pwd)/server.crt and server.key"
echo
echo "Server:  FX_TLS_CERT=/certs/server.crt  FX_TLS_KEY=/certs/server.key"
echo "Bot:     CC_FILESERVER_CA=/certs/${TRUST}"
echo
echo "Copy ${TRUST} to the bot machine. It is a public certificate, not a secret."
echo "Never copy any .key file off this machine."
if [[ "$MODE" == "ca" ]]; then
    echo
    echo "To stop browser warnings, install ca.crt as a trusted root on your devices."
fi
echo
echo "Verify with:"
echo "  openssl x509 -in server.crt -noout -text | grep -A1 'Subject Alternative Name'"
