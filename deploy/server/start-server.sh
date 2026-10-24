#!/usr/bin/env bash
set -euo pipefail
operation=start
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/server-common.sh"
release_archive="${deploy_dir}/forj-review-audit-server.tar.gz"
uploaded_compose="${deploy_dir}/docker-compose.server.yml"
[[ -s "${release_archive}" ]] || die "Upload forj-review-audit-server.tar.gz from release/server/ before starting."
[[ -s "${uploaded_compose}" ]] || die "Upload docker-compose.server.yml before starting."
for command in tar sha256sum sed; do command -v "${command}" >/dev/null || die "Missing command: ${command}"; done
# The daily upload is one archive; scripts/Compose are independent server files.
# Reject paths, duplicate entries and links before extracting into a fresh folder.
members="$(tar -tzf "${release_archive}")" || die "Cannot read release archive; upload the complete package again."
[[ "$(printf '%s\n' "${members}" | LC_ALL=C sort)" == $'SHA256SUMS\nforj-review-audit-master.tar\nrelease.json' ]] || die "Unexpected release archive contents."
details="$(tar -tvzf "${release_archive}")" || die "Cannot inspect release archive."
while IFS= read -r entry; do
  [[ "${entry}" == -* ]] || die "Release archive must contain regular files only."
done <<<"${details}"
release_stage="$(mktemp -d "${deploy_dir}/.release-XXXXXXXX")"
cleanup_release() {
  rm -f -- "${release_stage}/forj-review-audit-master.tar" "${release_stage}/release.json" "${release_stage}/SHA256SUMS"
  rmdir -- "${release_stage}"
}
trap cleanup_release EXIT
tar -xzf "${release_archive}" --no-same-owner --no-same-permissions -C "${release_stage}"
mapfile -t checksums <"${release_stage}/SHA256SUMS"
[[ ${#checksums[@]} -eq 2 ]] || die "Invalid release checksum manifest."
[[ "${checksums[0]}" =~ ^[[:xdigit:]]{64}"  forj-review-audit-master.tar"$ &&
   "${checksums[1]}" =~ ^[[:xdigit:]]{64}"  release.json"$ ]] || die "Invalid release checksum manifest."
(cd "${release_stage}" && sha256sum --check --status SHA256SUMS) || die "Release checksum verification failed; upload the package again."
expected_image="$(sed -nE 's/^[[:space:]]*"image_id":[[:space:]]*"(sha256:[[:xdigit:]]{64})",?[[:space:]]*$/\1/p' "${release_stage}/release.json")"
[[ "${expected_image}" =~ ^sha256:[[:xdigit:]]{64}$ ]] || die "Missing or invalid image ID in release metadata."
image_tar="${release_stage}/forj-review-audit-master.tar"
# Check configuration before replacing the runtime file or recreating containers.
docker compose --project-name forj-review-audit --project-directory "${deploy_dir}" --env-file "${deploy_dir}/.env" \
  -f "${uploaded_compose}" config --quiet
previous_image=""
previous_compose="${compose_file}"
if [[ -s "${runtime_dir}/.last-successful-image" ]]; then
  previous_image="$(cat "${runtime_dir}/.last-successful-image")"
  previous_compose="${runtime_dir}/.last-successful-compose.yml"
else
  previous_image="$(docker inspect --format '{{.Image}}' forj-review-audit 2>/dev/null || true)"
  if [[ -z "${previous_image}" ]]; then
    previous_image="$(docker image inspect --format '{{.Id}}' "${image_name}" 2>/dev/null || true)"
  fi
fi
if [[ -n "${previous_image}" && ! -s "${previous_compose}" ]]; then
  die "Previous image exists but its Compose file is missing; restore ${previous_compose} before upgrading so rollback stays complete."
fi
echo "Loading release image..."
docker load --input "${image_tar}"
new_image="$(docker image inspect --format '{{.Id}}' "${image_name}")"
[[ -n "${new_image}" ]] || die "Release did not provide ${image_name}."
[[ "${new_image}" == "${expected_image}" ]] || die "Loaded image does not match this release; container was not recreated."
# Validate packaged rules and references without network access before replacing the service.
echo "Checking audit data with the new image..."
docker run --rm --network none --read-only --env-file "${deploy_dir}/.env" --entrypoint python "${image_name}" \
  -c 'from forj_review_service.config.data import pages_directory; from forj_review_service.runtime.configuration.service import validated_configuration; validated_configuration(pages_directory())' \
  || die "Audit data validation failed. Existing container was not recreated."
# Re-running an identical release must not destroy the genuine previous version.
if [[ -n "${previous_image}" ]] && { [[ "${previous_image}" != "${new_image}" ]] || ! cmp -s "${uploaded_compose}" "${previous_compose}"; }; then
  docker tag "${previous_image}" "${rollback_image}"
  if [[ -s "${previous_compose}" ]]; then cp "${previous_compose}" "${runtime_dir}/rollback-compose.yml"; fi
  if [[ ! -s "${runtime_dir}/.last-successful-image" ]]; then
    printf '%s\n' "${previous_image}" >"${runtime_dir}/.last-successful-image"
    if [[ -s "${previous_compose}" ]]; then cp "${previous_compose}" "${runtime_dir}/.last-successful-compose.yml"; fi
  fi
  echo "Previous image and Compose configuration retained for rollback."
fi
prepare_logs
cp "${uploaded_compose}" "${compose_file}"
compose up -d --no-build --force-recreate "${service_name}"
wait_healthy
