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
ASSET_WAIT_ATTEMPTS = 12
ASSET_WAIT_SECONDS = 10
PUBLIC_RELEASE_TAG_RE = re.compile(
    r"^LHandPro-v\d+\.\d+\.\d+-\d{4}-\d{2}-\d{2}-\d{2}-\d{2}$"
)
# 源码包不镜像:GitHub 的 "Source code (zip/tar.gz)" 不在 assets 里,这里兜底挡住
# 手工上传的源码压缩包(dist 里的 src-*.zip 之类)。
SOURCE_ARCHIVE_NAME_RE = re.compile(r"^(src-.*|source.?code.*)\.(zip|tar\.gz|tgz)$", re.IGNORECASE)


def log(message):
    print(message, flush=True)


def require_env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


class GitHubClient:
    def __init__(self, token):
        self.token = token

    def _send(
        self,
        method,
        url,
        data=None,
        headers=None,
        accept="application/vnd.github+json",
        raw=False,
        tolerate=(),
    ):
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
                if exc.code in tolerate:
                    return None
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

    def get(self, path, tolerate=()):
        return self._send("GET", f"{API_ROOT}{path}", tolerate=tolerate)

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


def validate_release_tag(tag_name):
    if not PUBLIC_RELEASE_TAG_RE.fullmatch(tag_name):
        raise RuntimeError(f"Release tag is not allowed for the public mirror: {tag_name}")


def get_release_by_tag(client, repo_full_name, tag_name, tolerate_missing=False):
    encoded_tag = parse.quote(tag_name, safe="")
    tolerate = (404,) if tolerate_missing else ()
    return client.get(f"/repos/{repo_full_name}/releases/tags/{encoded_tag}", tolerate=tolerate)


def select_mirrored_assets(source_release):
    """镜像 Release 里的**全部**资产(各平台安装包、绿色包、校验和等)。

    GitHub 页面上的 "Source code (zip/tar.gz)" 两项不是 Release 资产,API 也不
    会返回,所以这里不需要白名单;只做三项防御性过滤:
      1. 名字为空的异常数据;
      2. state 不是 uploaded 的半成品(拉下来会是坏包);
      3. 命中源码包命名(src-*.zip / Source code.zip 等)的手工上传残留。
    """
    selected = {}
    for asset in source_release.get("assets", []):
        name = asset.get("name") or ""
        if not name or name in selected:
            continue
        if (asset.get("state") or "uploaded") != "uploaded":
            continue
        if SOURCE_ARCHIVE_NAME_RE.fullmatch(name):
            continue
        selected[name] = asset
    return selected


def wait_for_source_release(source_client, source_repo, tag_name):
    for attempt in range(1, ASSET_WAIT_ATTEMPTS + 1):
        source_release = get_release_by_tag(
            source_client,
            source_repo,
            tag_name,
            tolerate_missing=True,
        )
        if source_release and source_release.get("draft"):
            raise RuntimeError(f"Refusing to mirror draft release: {tag_name}")

        source_assets = select_mirrored_assets(source_release or {})
        if source_release and source_assets:
            return source_release, source_assets

        if attempt < ASSET_WAIT_ATTEMPTS:
            log(
                f"Release {tag_name} or its assets are not ready; "
                f"retrying in {ASSET_WAIT_SECONDS}s ({attempt}/{ASSET_WAIT_ATTEMPTS})"
            )
            time.sleep(ASSET_WAIT_SECONDS)

    raise RuntimeError(
        f"Release {tag_name} has no mirrorable asset after "
        f"{ASSET_WAIT_ATTEMPTS} attempts"
    )


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


def sync_assets(source_client, target_client, target_repo, source_assets, target_release):
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
    release_tag = require_env("RELEASE_TAG")

    validate_release_tag(release_tag)

    source_client = GitHubClient(source_token)
    target_client = GitHubClient(target_token)

    source_release, source_assets = wait_for_source_release(
        source_client,
        source_repo,
        release_tag,
    )
    for asset in source_release.get("assets", []):
        if asset["name"] not in source_assets:
            log(
                f"Skipping asset {release_tag}/{asset['name']} "
                "(source archive or not fully uploaded)"
            )
    log(f"Mirroring {len(source_assets)} asset(s) from {release_tag}")

    target_release = get_release_by_tag(
        target_client,
        target_repo,
        release_tag,
        tolerate_missing=True,
    )
    target_release = ensure_release(
        target_client,
        target_repo,
        source_release,
        target_release,
    )
    sync_assets(source_client, target_client, target_repo, source_assets, target_release)

    log(f"Release sync completed for {release_tag}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        log(f"ERROR: {exc}")
        sys.exit(1)
