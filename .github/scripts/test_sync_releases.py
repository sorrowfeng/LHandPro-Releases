#!/usr/bin/env python3
import unittest

import sync_releases


class ReleaseFilterTests(unittest.TestCase):
    def test_accepts_standard_release_tag(self):
        sync_releases.validate_release_tag("LHandPro-v1.3.7-2026-07-10-15-02")

    def test_rejects_non_public_release_tag(self):
        for tag_name in (
            "v1.3.7",
            "LHandPro-v1.3.7-2026-07-10-15-02-LumiBot",
            "../LHandPro-v1.3.7-2026-07-10-15-02",
        ):
            with self.subTest(tag_name=tag_name):
                with self.assertRaises(RuntimeError):
                    sync_releases.validate_release_tag(tag_name)

    def test_selects_every_asset_of_the_release(self):
        tag_name = "LHandPro-v1.4.5-2026-10-31-10-47"
        release = {
            "assets": [
                {"name": f"{tag_name}-e1706f6f.zip", "state": "uploaded"},
                {
                    "name": "LHandPro-v1.4.5-linux-x86_64-2026-10-31-11-14-7b9d225f.deb",
                    "state": "uploaded",
                },
                {
                    "name": "LHandPro-v1.4.5-linux-x86_64-2026-10-31-11-14-7b9d225f.tar.gz",
                    "state": "uploaded",
                },
                {
                    "name": "LHandPro-v1.4.5-macos-arm64-2026-10-31-09-59-74147a03.dmg",
                    "state": "uploaded",
                },
                {"name": "SHA256SUMS.v1.4.5", "state": "uploaded"},
            ]
        }

        selected = sync_releases.select_mirrored_assets(release)

        self.assertEqual(
            [
                f"{tag_name}-e1706f6f.zip",
                "LHandPro-v1.4.5-linux-x86_64-2026-10-31-11-14-7b9d225f.deb",
                "LHandPro-v1.4.5-linux-x86_64-2026-10-31-11-14-7b9d225f.tar.gz",
                "LHandPro-v1.4.5-macos-arm64-2026-10-31-09-59-74147a03.dmg",
                "SHA256SUMS.v1.4.5",
            ],
            list(selected),
        )

    def test_skips_source_archives_and_incomplete_uploads(self):
        release = {
            "assets": [
                {
                    "name": "LHandPro-v1.4.4-2026-10-27-12-24-eb20b15c.zip",
                    "state": "uploaded",
                },
                {"name": "src-v1.4.4-2026-10-27-12-24-eb20b15c.zip", "state": "uploaded"},
                {"name": "Source code.zip", "state": "uploaded"},
                {"name": "SHA256SUMS.v1.4.4", "state": "uploaded"},
                {
                    "name": "LHandPro-v1.4.4-linux-x86_64-2026-10-27-12-24.deb",
                    "state": "starter",
                },
            ]
        }

        selected = sync_releases.select_mirrored_assets(release)

        self.assertEqual(
            ["LHandPro-v1.4.4-2026-10-27-12-24-eb20b15c.zip", "SHA256SUMS.v1.4.4"],
            list(selected),
        )

    def test_empty_release_has_no_assets(self):
        self.assertEqual({}, sync_releases.select_mirrored_assets({}))


if __name__ == "__main__":
    unittest.main()
