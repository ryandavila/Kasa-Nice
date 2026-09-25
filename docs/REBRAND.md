# KasaBuena migration checklist

Target repository: `ryandavila/KasaBuena`; Python distribution and command:
`kasabuena`; planned container: `ghcr.io/ryandavila/kasabuena`.
First branded release: `v2.0.0` (application version `2.0.0`). The private
frontend package has its own internal version and is not published to npm.

## Local preparation

- Rebrand application metadata, UI, PWA manifest, docs, and backup downloads.
- Preserve LICENSE and upstream attribution, `KASA_*` variables, browser storage
  keys, persisted data filenames, and `logs/kasa_nice.log`.
- Keep backup schema version 1: this release changes branding, not the format.
- Document deployment migration in README.md.
- Validate with `uv sync --dev`, frozen frontend install, `just ci`, `just e2e`,
  and an isolated Docker build/start check before committing.

## GitHub migration (after committing and pushing)

1. Wait for CI to pass on the existing fork.
2. Back up all Git branches/tags and inventory repository settings and metadata.
3. If independence is still desired, use Settings → General → Danger Zone →
   Leave fork network. Detachment is permanent. GitHub documents loss of issues,
   pull requests, wikis, stars, watchers, comments, child forks, and other metadata;
   Git commit metadata is preserved. Do not assume settings survive unchanged.
   The self-service option requires a public fork under 1 GB with no child forks.
   If unavailable, reassess before any delete/recreate operation.
4. Rename the repository to `KasaBuena`, then update the local remote:
   `git remote set-url origin git@github.com:ryandavila/KasaBuena.git`.
5. Verify branches/tags and redirects; enable Issues; check Actions permissions,
   secrets, webhooks, and branch protections. Do not reuse the old repository name,
   which would break redirects. Renaming the local folder is optional.

References:
- https://docs.github.com/en/pull-requests/how-tos/work-with-forks/detaching-a-fork
- https://docs.github.com/en/repositories/creating-and-managing-repositories/renaming-a-repository

## Publishing (after the final repository name is in place)

1. Add a reviewed publishing workflow for amd64 and arm64 images, with CI gates,
   `packages: write`, GITHUB_TOKEN, repository source labels, main/latest and
   release-version tags. No publishing workflow exists yet.
2. Publish the image, make the GHCR package public, and verify an anonymous pull
   and clean startup. Repository visibility does not imply package visibility.
3. Update Compose/install docs to use the verified image.
4. Ensure application version and tag agree, then create `v2.0.0` and release notes
   describing the CLI/service rename and compatible data/backup format.
5. Upgrade the live deployment using the README migration instructions.
6. Optional follow-up: dependency-update automation.

GHCR reference:
https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry
