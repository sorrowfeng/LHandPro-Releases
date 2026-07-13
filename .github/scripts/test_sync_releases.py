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

    def test_selects_only_zip_matching_release_tag(self):
        tag_name = "LHandPro-v1.3.7-2026-07-10-15-02"
        release = {
            "assets": [
                {"name": f"{tag_name}-2c3eb024.zip"},
                {"name": "src-v1.3.7-2026-07-10-15-02-2c3eb024.zip"},
                {"name": "LHandPro.pdb"},
                {"name": f"{tag_name}-LumiBot.7z"},
            ]
        }

        selected = sync_releases.select_public_assets(release, tag_name)

        self.assertEqual([f"{tag_name}-2c3eb024.zip"], list(selected))

    def test_rejects_similar_but_noncanonical_zip_names(self):
        tag_name = "LHandPro-v1.3.7-2026-07-10-15-02"
        release = {
            "assets": [
                {"name": f"{tag_name}.zip"},
                {"name": f"{tag_name}-2c3eb02.zip"},
                {"name": f"{tag_name}-2c3eb024-extra.zip"},
                {"name": f"{tag_name}-2C3EB024.zip"},
            ]
        }

        self.assertEqual({}, sync_releases.select_public_assets(release, tag_name))


if __name__ == "__main__":
    unittest.main()
