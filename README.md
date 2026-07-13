# LHandPro Releases Mirror

This repository is a release-only mirror for `sorrowfeng/LHandPro`.

- It intentionally contains no private source code.
- The source repository triggers the sync workflow after a Release is published.
- The mirror does not poll the private source repository on a schedule.
- Each dispatch carries one Release tag; only the matching `LHandPro-v*.zip` asset is mirrored.
- Drafts, customer-specific tags, source archives, debug symbols, and unrelated assets are rejected.
- End users should download packages from the GitHub Releases page.
