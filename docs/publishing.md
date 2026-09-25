# Publishing KasaBuena containers

The Publish container workflow builds Linux amd64 and arm64 images at
`ghcr.io/ryandavila/kasabuena`. It runs both CI jobs on the exact revision before
building. Only pushed stable release tags publish; ordinary main/PR pushes do
not upload images. Manual workflow runs validate both builds without uploading.

## First publication

1. Commit and push the workflows to main. In Actions, run **Publish container →
   Run workflow** on main for a dry run. Wait for both CI and the build to pass.
2. Confirm `pyproject.toml` and `uv.lock` contain the intended version (`1.0.0`
   for the first release). The workflow rejects tags that do not match the
   application version and rejects prerelease versions.
3. Tag the verified commit and push that specific tag:

   ```bash
   git tag v1.0.0
   git push origin v1.0.0
   ```

4. Watch Publish container complete. It publishes tags `1.0.0`, `1.0`, and
   `latest`. The latest tag follows the most recently published release, so do
   not republish an older release if you intend to keep latest on a newer one.
5. Open your GitHub profile → Packages → kasabuena → Package settings. Change
   package visibility to **Public**, confirm its source repository is KasaBuena,
   and check that this repository has Actions access to the package.
6. Verify an anonymous pull using an empty Docker configuration (or a machine
   without GHCR credentials), then start the image and check `/api/health` and
   the UI. Verify both architectures appear in the image manifest:

   ```bash
   docker buildx imagetools inspect ghcr.io/ryandavila/kasabuena:1.0.0
   ```

7. Create the GitHub release for the existing tag with migration notes. Upgrade
   existing installations using the README instructions and GHCR Compose override.

No personal access token or custom repository secret is required: the workflow
uses GitHub's automatic `GITHUB_TOKEN` with `packages: write` only in the publish
job. Actions must be enabled and repository policies must permit the Docker
Actions used by the workflow. Newly published packages default to private.

If publishing reports permission denied, check package Actions access and any
repository/organization restrictions on package creation. An existing package
created outside Actions may need to be linked explicitly.

## Later releases

Update the application version and lockfile, validate, commit, push, then tag
that commit as `vX.Y.Z`. Update the default image version in `compose.ghcr.yml`
and the installation docs for the release. Do not move existing release tags.

Reference: [GitHub Container registry documentation](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry).
