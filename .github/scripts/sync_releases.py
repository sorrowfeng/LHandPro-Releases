#!/usr/bin/env python3
import json
import mimetypes
import os
import re
import sys
import time
from urllib import error, parse, request


API_ROOT = "https://api.github.com"
RETRYABLE_STATUS = {429, 500, 502, 503, 504}


def log(message):
    print(message, flush=True)


def require_env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def sort_key(tag_name):
    parts = re.split(r"(\d+)", tag_name)
    key = []
    for part in parts:
        if part.isdigit():
            key.append((0, int(part)))
        else:
            key.append((1, part.lower()))
    return key


class GitHubClient:
    def __init__(self, token):
        self.token = token

    def _send(self, method, url, data=None, headers=None, accept="application/vnd.github+json", raw=False):
        attempt = 0
        headers = dict(headers or {})
        headers.setdefault("Accept", accept)
        headers.setdefault("Authorization", f"Bearer {self.token}")
        headers.setdefault("X-GitHub-Api-Version", "2022-11-28")
        body = data
        if data is not None and not isinstance(data, (bytes, bytearray)):
            body = json.dumps(data).encode("utf-8")
            headers.setdefault("Content-Type", "application/json; charset=utf-8")
        while True:
            attempt += 1
            req = request.Request(url, data=body, headers=headers, method=method)
            try:
                with request.urlopen(req, timeout=120) as response:
                    payload = response.read()
                    if raw:
                        return payload, response.headers
                    if not payload:
                        return None
                    return json.loads(payload.decode("utf-8"))
            except error.HTTPError as exc:
                payload = exc.read()
                should_retry = exc.code in RETRYABLE_STATUS and attempt < 6
                if should_retry:
                    delay = 2 ** (attempt - 1)
                    log(f"Retrying {method} {url} after HTTP {exc.code}, sleep {delay}s")
                    time.sleep(delay)
                    continue
                detail = payload.decode("utf-8", errors="replace").strip()
                raise RuntimeError(f"GitHub API error {exc.code} on {method} {url}: {detail}") from exc
            except error.URLError as exc:
                if attempt < 6:
                    delay = 2 ** (attempt - 1)
                    log(f"Retrying {method} {url} after network error, sleep {delay}s")
                    time.sleep(delay)
                    continue
                raise RuntimeError(f"Network error on {method} {url}: {exc}") from exc

    def get(self, path):
        return self._send("GET", f"{API_ROOT}{path}")

    def post(self, path, data):
        return self._send("POST", f"{API_ROOT}{path}", data=data)

    def patch(self, path, data):
        return self._send("PATCH", f"{API_ROOT}{path}", data=data)

    def delete(self, path):
        return self._send("DELETE", f"{API_ROOT}{path}")

    def download_asset(self, asset_url):
        payload, _ = self._send("GET", asset_url, accept="application/octet-stream", raw=True)
        return payload

    def upload_asset(self, upload_url, name, label, content):
        base_url = upload_url.split("{", 1)[0]
        query = parse.urlencode({"name": name, "label": label or ""})
        content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        return self._send(
            "POST",
            f"{base_url}?{query}",
            data=content,
            headers={"Content-Type": content_type},
            accept="application/vnd.github+json",
        )


def list_releases(client, repo_full_name):
    releases = []
    page = 1
    while True:
        chunk = client.get(f"/repos/{repo_full_name}/releases?per_page=100&page={page}")
        if not chunk:
            break
        releases.extend(chunk)
        if len(chunk) < 100:
            break
        page += 1
    return releases


def ensure_release(target_client, target_repo, source_release, existing_release):
    payload = {
        "tag_name": source_release["tag_name"],
        "name": source_release.get("name") or source_release["tag_name"],
        "body": source_release.get("body") or "",
        "draft": False,
        "prerelease": bool(source_release.get("prerelease")),
    }
    if existing_release:
        log(f"Updating release {source_release['tag_name']}")
        return target_client.patch(f"/repos/{target_repo}/releases/{existing_release['id']}", payload)
    log(f"Creating release {source_release['tag_name']}")
    return target_client.post(f"/repos/{target_repo}/releases", payload)


def sync_assets(source_client, target_client, target_repo, source_release, target_release):
    source_assets = {asset["name"]: asset for asset in source_release.get("assets", [])}
    target_assets = {asset["name"]: asset for asset in target_release.get("assets", [])}

    for target_name, target_asset in sorted(target_assets.items()):
        if target_name not in source_assets:
            log(f"Deleting stale asset {target_release['tag_name']}/{target_name}")
            target_client.delete(f"/repos/{target_repo}/releases/assets/{target_asset['id']}")

    refreshed_release = target_client.get(f"/repos/{target_repo}/releases/{target_release['id']}")
    target_assets = {asset["name"]: asset for asset in refreshed_release.get("assets", [])}
    upload_url = refreshed_release["upload_url"]

    for name, source_asset in sorted(source_assets.items()):
        target_asset = target_assets.get(name)
        needs_upload = (
            target_asset is None
            or (target_asset.get("label") or "") != (source_asset.get("label") or "")
            or target_asset.get("size") != source_asset.get("size")
            or target_asset.get("content_type") != source_asset.get("content_type")
        )
        if target_asset and needs_upload:
            log(f"Replacing asset {target_release['tag_name']}/{name}")
            target_client.delete(f"/repos/{target_repo}/releases/assets/{target_asset['id']}")
        if needs_upload:
            log(f"Uploading asset {target_release['tag_name']}/{name}")
            payload = source_client.download_asset(source_asset["url"])
            target_client.upload_asset(upload_url, name, source_asset.get("label") or "", payload)
        else:
            log(f"Keeping asset {target_release['tag_name']}/{name}")


def main():
    target_repo = require_env("TARGET_REPO")
    source_repo = require_env("SRC_REPO")
    target_token = require_env("GITHUB_TOKEN")
    source_token = require_env("SRC_REPO_TOKEN")

    source_client = GitHubClient(source_token)
    target_client = GitHubClient(target_token)

    source_releases = [
        release for release in list_releases(source_client, source_repo) if not release.get("draft")
    ]
    source_releases.sort(key=lambda item: sort_key(item["tag_name"]))
    target_releases = list_releases(target_client, target_repo)
    target_by_tag = {release["tag_name"]: release for release in target_releases}

    log(f"Found {len(source_releases)} source releases in {source_repo}")
    log(f"Found {len(target_releases)} target releases in {target_repo}")

    for source_release in source_releases:
        target_release = ensure_release(
            target_client,
            target_repo,
            source_release,
            target_by_tag.get(source_release["tag_name"]),
        )
        sync_assets(source_client, target_client, target_repo, source_release, target_release)

    log("Release sync completed")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log(f"ERROR: {exc}")
        sys.exit(1)
