# An in-process OCI registry behind Docker's token authentication, so the
# real pull and push pipelines -- transport, openers, redirect policy and
# all -- can be driven end to end without a network.
#
# Three servers on 127.0.0.1, each its own origin (a port of its own):
#
#   registry  The /v2/ API. The probe of /v2/ answers 401 with a Bearer
#             challenge naming the token service as its realm; every
#             other endpoint wants a Bearer token granting the action it
#             serves (pull, or push), and answers 401 with the same
#             challenge otherwise, as distribution does.
#   auth      The token service. Basic credentials, when sent, must be an
#             account's; a wrong password is a 401. An account may pull
#             and push every repository, an anonymous caller may pull the
#             public ones and nothing else -- what is not granted is
#             simply left out of the token, as real token services do.
#   cdn       Where blob GETs are redirected, the way Docker Hub sends
#             them to pre-signed storage URLs, and optionally where upload
#             sessions are handed off. Like those stores it refuses any
#             request carrying an Authorization header with HTTP 400.
#
# With scheme="basic" there is no token service to speak of: the probe's
# challenge is Basic, and the registry checks an account's password on
# every request itself, as distribution's htpasswd auth, Nexus and
# Artifactory do.
#
# Every request is recorded with its Authorization header, so a test can
# say exactly which server saw which credential.
#
# TLS is served with tests/tls/server.pem, issued for localhost and
# 127.0.0.1 by tests/tls/ca.pem: a CA made for this suite alone, valid
# 2000-2100, whose key was discarded after signing. Pointing SSL_CERT_FILE
# at ca.pem makes the program's own, unmodified certificate verification
# trust it; leaving it unset makes the certificate untrusted. Both are
# P-256 and carry what Python 3.13's VERIFY_X509_STRICT asks for (key
# identifiers, critical basicConstraints, keyUsage). To regenerate,
# sign with `openssl ca -selfsign` (CA) and `openssl ca` (server) using
# extension sections like:
#   CA:     basicConstraints = critical, CA:TRUE
#           keyUsage = critical, keyCertSign, cRLSign
#           subjectKeyIdentifier = hash
#           authorityKeyIdentifier = keyid
#   server: basicConstraints = critical, CA:FALSE
#           keyUsage = critical, digitalSignature
#           extendedKeyUsage = serverAuth
#           subjectAltName = DNS:localhost, IP:127.0.0.1
#           subjectKeyIdentifier = hash
#           authorityKeyIdentifier = keyid
# with -startdate 20000101000000Z -enddate 21000101000000Z.

import base64
import hashlib
import http.server
import json
import os
import re
import secrets
import socketserver
import ssl
import threading
import urllib.parse
from typing import NamedTuple

from _builders import make_layer_blob

TLS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tls")
CA_FILE = os.path.join(TLS_DIR, "ca.pem")
_CERT_FILE = os.path.join(TLS_DIR, "server.pem")
_KEY_FILE = os.path.join(TLS_DIR, "server.key")

OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
OCI_CONFIG = "application/vnd.oci.image.config.v1+json"
OCI_LAYER = "application/vnd.oci.image.layer.v1.tar+gzip"

_NOBODY = object()

_UPLOAD_RE = re.compile(r"^/v2/(?P<name>.+)/blobs/uploads/(?P<id>[^/]*)$")
_OBJECT_RE = re.compile(
    r"^/v2/(?P<name>.+)/(?P<kind>manifests|blobs)/(?P<ref>[^/]+)$"
)


class Seen(NamedTuple):
    """One request as a server received it."""
    role: str
    method: str
    path: str
    authorization: str


def basic(user: str, password: str) -> str:
    """The Authorization value PD_DOCKER_AUTH=user:password produces."""
    return "Basic " + base64.b64encode(
        f"{user}:{password}".encode()).decode()


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


class FakeRegistry:
    """The three servers and the state they share. Close with close()."""

    def __init__(self, *, tls: bool = True, scheme: str = "bearer",
                 service: str = "fake-registry"):
        self.tls = tls
        self.scheme = scheme
        self.service = service
        self.users = {"alice": "s3cret"}
        self.public = set()
        # What the token endpoint names the token under. Registries send
        # `token`, `access_token` or both; the client takes either.
        self.grant_keys = ("token", "access_token")
        # "registry" hands upload sessions out on itself; "cdn" hands them
        # to the other origin, as a pre-signed upload URL would.
        self.upload_origin = "registry"
        # Kinds of request the registry refuses even with a good token
        # ("blob" for blob GETs), as a token revoked mid-pull would be.
        self.deny = set()
        self.blobs = {}          # (repo, digest) -> bytes
        self.manifests = {}      # (repo, tag or digest) -> (bytes, media)
        self.tokens = {}         # token -> {repo: set(actions)}
        self.uploads = {}        # upload id -> repo
        self.requests = []
        self.lock = threading.Lock()
        self._servers = {}
        self._threads = []
        context = None
        if tls:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(_CERT_FILE, _KEY_FILE)
        try:
            for role in ("registry", "auth", "cdn"):
                handler = type(f"_{role}", (_Handler,),
                               {"role": role, "state": self})
                server = _Server(("127.0.0.1", 0), handler)
                self._servers[role] = server
                if context is not None:
                    server.socket = context.wrap_socket(
                        server.socket, server_side=True)
                thread = threading.Thread(
                    target=server.serve_forever,
                    kwargs={"poll_interval": 0.05}, daemon=True,
                )
                thread.start()
                self._threads.append(thread)
        except BaseException:
            self.close()
            raise

    # ----- addresses ------------------------------------------------------

    def url(self, role: str) -> str:
        scheme = "https" if self.tls else "http"
        return f"{scheme}://{self.host(role)}"

    def host(self, role: str = "registry") -> str:
        return "127.0.0.1:%d" % self._servers[role].server_address[1]

    def close(self) -> None:
        for server in self._servers.values():
            server.shutdown()
            server.server_close()
        for thread in self._threads:
            thread.join(5)

    # ----- content --------------------------------------------------------

    def add_image(self, repo: str, tag: str, members, config: dict) -> dict:
        """Publish a one-layer image; return its manifest and documents."""
        layer, layer_digest, diff_id = make_layer_blob(members)
        config = dict(config, rootfs={"type": "layers",
                                      "diff_ids": [diff_id]})
        config_bytes = canonical(config)
        manifest = {
            "schemaVersion": 2,
            "mediaType": OCI_MANIFEST,
            "config": {"mediaType": OCI_CONFIG, "size": len(config_bytes),
                       "digest": sha256(config_bytes)},
            "layers": [{"mediaType": OCI_LAYER, "size": len(layer),
                        "digest": layer_digest}],
        }
        body = canonical(manifest)
        with self.lock:
            self.blobs[(repo, layer_digest)] = layer
            self.blobs[(repo, sha256(config_bytes))] = config_bytes
            self.manifests[(repo, tag)] = (body, OCI_MANIFEST)
            self.manifests[(repo, sha256(body))] = (body, OCI_MANIFEST)
        return {"manifest": manifest, "config": config,
                "digest": sha256(body)}

    # ----- what was seen --------------------------------------------------

    def seen(self, role: str = None) -> list:
        with self.lock:
            return [r for r in self.requests if role is None or r.role == role]

    def token_scopes(self) -> list:
        """The scope parameter of every token request, in order."""
        return [
            urllib.parse.parse_qs(urllib.parse.urlsplit(r.path).query)
            .get("scope", [""])[0]
            for r in self.seen("auth")
        ]

    def issued(self) -> set:
        with self.lock:
            return {f"Bearer {t}" for t in self.tokens}

    # ----- policy ---------------------------------------------------------

    def allowed(self, account, repo: str) -> set:
        if account is not None:
            return {"pull", "push"}
        return {"pull"} if repo in self.public else set()


class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def server_bind(self):
        # HTTPServer.server_bind() asks getfqdn() for a name nothing here
        # uses: a reverse lookup that can stall on a host with slow DNS.
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = self.server_address[:2]

    def handle_error(self, request, client_address):
        # A client that walks away mid-answer (the TLS tests make some do
        # exactly that) is no failure of the server's to print.
        pass


class _Handler(http.server.BaseHTTPRequestHandler):
    role = ""
    state: FakeRegistry = None

    def log_message(self, *args):
        pass

    def _dispatch(self):
        # The body is read before anything is answered: a refusal sent
        # while the client is still writing a PUT would reach it as a
        # reset connection, which it retries as a network failure.
        length = int(self.headers.get("Content-Length") or 0)
        self.body = self.rfile.read(length) if length else b""
        self.url = urllib.parse.urlsplit(self.path)
        self.query = urllib.parse.parse_qs(self.url.query)
        with self.state.lock:
            self.state.requests.append(Seen(
                self.role, self.command, self.path,
                self.headers.get("Authorization") or "",
            ))
        getattr(self, "_" + self.role)()

    do_GET = do_HEAD = do_POST = do_PUT = do_PATCH = _dispatch

    def _reply(self, code, body=b"", headers=None, length=None):
        self.send_response(code)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length",
                         str(len(body) if length is None else length))
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)

    def _error(self, code, err, headers=None):
        body = canonical({"errors": [{"code": err, "message": err.lower()}]})
        self._reply(code, body, {"Content-Type": "application/json",
                                 **(headers or {})})

    # ----- auth -----------------------------------------------------------

    def _account(self):
        """The account the Basic credentials name; None if none were sent.

        Credentials that are not an account's are _NOBODY.
        """
        header = self.headers.get("Authorization")
        if not header:
            return None
        scheme, _, cred = header.partition(" ")
        try:
            user, _, password = base64.b64decode(
                cred, validate=True).decode().partition(":")
        except ValueError:
            return _NOBODY
        if scheme != "Basic" or not user \
                or self.state.users.get(user) != password:
            return _NOBODY
        return user

    def _auth(self):
        st = self.state
        if self.url.path != "/token" or self.command != "GET":
            return self._error(404, "NOT_FOUND")
        account = self._account()
        if account is _NOBODY:
            return self._error(401, "UNAUTHORIZED")
        access = {}
        for scope in self.query.get("scope", []):
            kind, _, rest = scope.partition(":")
            repo, _, actions = rest.rpartition(":")
            if kind == "repository" and repo:
                access[repo] = st.allowed(account, repo) \
                    & set(actions.split(","))
        token = secrets.token_hex(16)
        with st.lock:
            st.tokens[token] = access
        self._reply(200, canonical({k: token for k in st.grant_keys}),
                    {"Content-Type": "application/json"})

    # ----- registry -------------------------------------------------------

    def _challenge(self, repo="", action=""):
        st = self.state
        if st.scheme == "basic":
            return self._error(401, "UNAUTHORIZED", {
                "WWW-Authenticate": f'Basic realm="{st.service}"'})
        value = f'Bearer realm="{st.url("auth")}/token",service="{st.service}"'
        if repo:
            value += f',scope="repository:{repo}:{action}"'
        return self._error(401, "UNAUTHORIZED", {"WWW-Authenticate": value})

    def _granted(self, repo, action, kind=""):
        """Whether the request may *action* on *repo*; answers 401 if not.

        A Bearer token grants what it was issued with; Basic credentials,
        on a scheme="basic" registry, what their account may do.
        """
        st = self.state
        if st.scheme == "basic":
            account = self._account()
            with st.lock:
                ok = account is not _NOBODY and kind not in st.deny \
                    and action in st.allowed(account, repo)
            if not ok:
                self._challenge(repo, action)
            return ok
        scheme, _, token = (self.headers.get("Authorization") or "") \
            .partition(" ")
        with st.lock:
            access = st.tokens.get(token) if scheme == "Bearer" else None
            ok = access is not None and action in access.get(repo, ()) \
                and kind not in st.deny
        if not ok:
            self._challenge(repo, action)
        return ok

    def _registry(self):
        st = self.state
        path = self.url.path
        if path == "/v2/":
            # Asked without a token by every client, to learn the realm.
            if self.headers.get("Authorization"):
                return self._reply(200, b"{}")
            return self._challenge()
        m = _UPLOAD_RE.match(path)
        if m:
            repo, upload = m.group("name"), m.group("id")
            if not self._granted(repo, "push"):
                return None
            if self.command == "POST" and not upload:
                upload = secrets.token_hex(8)
                with st.lock:
                    st.uploads[upload] = repo
                if st.upload_origin == "cdn":
                    location = f"{st.url('cdn')}/upload/{upload}?sig=x"
                else:
                    location = f"/v2/{repo}/blobs/uploads/{upload}"
                return self._reply(202, headers={"Location": location})
            if self.command == "PUT" and st.uploads.get(upload) == repo:
                return self._finish_upload(upload)
            return self._error(404, "BLOB_UPLOAD_UNKNOWN")
        m = _OBJECT_RE.match(path)
        if not m:
            return self._error(404, "NOT_FOUND")
        repo, kind, ref = m.group("name", "kind", "ref")
        if kind == "manifests":
            if self.command == "PUT":
                return self._put_manifest(repo, ref)
            if not self._granted(repo, "pull"):
                return None
            with st.lock:
                found = st.manifests.get((repo, ref))
            if found is None:
                return self._error(404, "MANIFEST_UNKNOWN")
            body, media = found
            return self._reply(200, body, {
                "Content-Type": media,
                "Docker-Content-Digest": sha256(body),
            })
        if not self._granted(repo, "pull", "blob"
                             if self.command == "GET" else ""):
            return None
        with st.lock:
            blob = st.blobs.get((repo, ref))
        if blob is None:
            return self._error(404, "BLOB_UNKNOWN")
        if self.command == "HEAD":
            return self._reply(200, length=len(blob),
                               headers={"Docker-Content-Digest": ref})
        return self._reply(307, headers={
            "Location": f"{st.url('cdn')}/blob/{ref}?sig=x",
        })

    def _put_manifest(self, repo, tag):
        st = self.state
        if not self._granted(repo, "push"):
            return None
        try:
            doc = json.loads(self.body)
            wanted = [doc["config"]["digest"]] \
                + [layer["digest"] for layer in doc["layers"]]
        except (ValueError, KeyError, TypeError):
            return self._error(400, "MANIFEST_INVALID")
        with st.lock:
            if any((repo, d) not in st.blobs for d in wanted):
                missing = True
            else:
                missing = False
                media = self.headers.get("Content-Type", "")
                st.manifests[(repo, tag)] = (self.body, media)
                st.manifests[(repo, sha256(self.body))] = (self.body, media)
        if missing:
            return self._error(400, "MANIFEST_BLOB_UNKNOWN")
        return self._reply(201, headers={
            "Docker-Content-Digest": sha256(self.body),
            "Location": f"/v2/{repo}/manifests/{sha256(self.body)}",
        })

    def _finish_upload(self, upload):
        st = self.state
        digest = self.query.get("digest", [""])[0]
        if digest != sha256(self.body):
            return self._error(400, "DIGEST_INVALID")
        with st.lock:
            repo = st.uploads.pop(upload)
            st.blobs[(repo, digest)] = self.body
        return self._reply(201, headers={
            "Docker-Content-Digest": digest,
            "Location": f"/v2/{repo}/blobs/{digest}",
        })

    # ----- cdn ------------------------------------------------------------

    def _cdn(self):
        st = self.state
        if self.headers.get("Authorization"):
            # What S3 and friends answer to a credential on a pre-signed
            # URL: one authentication mechanism only.
            return self._error(400, "INVALID_ARGUMENT")
        parts = self.url.path.split("/")
        if len(parts) == 3 and parts[1] == "blob" and self.command == "GET":
            with st.lock:
                blob = next((b for (_r, d), b in st.blobs.items()
                             if d == parts[2]), None)
            if blob is None:
                return self._error(404, "NOT_FOUND")
            return self._reply(200, blob,
                               {"Content-Type": "application/octet-stream"})
        if len(parts) == 3 and parts[1] == "upload" \
                and self.command == "PUT" and parts[2] in st.uploads:
            return self._finish_upload(parts[2])
        return self._error(404, "NOT_FOUND")
