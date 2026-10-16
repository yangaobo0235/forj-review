# Server release

Local builds produce one daily upload: `.tmp/server-release/forj-review-server.tar.gz`.
It includes the Docker image, release metadata and checksums. Do not extract it manually.
The fixed server scripts and Compose are in `.tmp/server-deploy/` after packaging;
upload those files once to `/home/ubuntu/review-agent-deploy/` (binary transfer,
no newline conversion). On later updates, upload them only if the deployment
scripts or Compose actually changed. Application-only changes need just the package.
Keep `recover-legacy-compose.py` beside `start-review.sh`. For a first migration
whose original Compose was deleted, startup can recover a protected runtime
snapshot from the existing review container before loading the new image.
The snapshot contains its original environment; do not share it or add it to Git.
Keep the existing server credentials at `/opt/review-assistant/.env`.
For the first installation, create `/opt/review-assistant`, give the deployment
user ownership, and create its `.env` on the server before running the scripts.

```bash
cd /home/ubuntu/review-agent-deploy
chmod +x *-review.sh
./stop-review.sh
# Manually upload/replace forj-review-server.tar.gz here, then:
./start-review.sh
```

Routine workflow: local build -> server stop -> manual package upload -> server start.
For first installation, skip stop because there is no running service yet.

To restore the previous healthy image AND its Compose configuration:

```bash
./rollback-review.sh
```

Application logs: `logs/review-agent.log` and bounded daily/size archives.
Deployment log: `deploy.log` (plus one size-bounded archive).
Health: `curl --fail http://127.0.0.1:18110/health`.

Keep the package: `start-review.sh` extracts and verifies it automatically.
Loose TAR/release.json/SHA256SUMS files from older releases are not used.
Repeated installation of the same image/configuration preserves the prior rollback.
Never upload `.env`, source images, local logs, or the entire repository temp folder.
