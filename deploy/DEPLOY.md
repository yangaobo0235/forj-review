# Server release

Upload ALL files in this release directory to `/home/ubuntu/review-agent-deploy/`.
Keep the existing server credentials at `/opt/review-assistant/.env`.
For the first installation, create `/opt/review-assistant`, give the deployment
user ownership, and create its `.env` on the server before running the scripts.

```bash
cd /home/ubuntu/review-agent-deploy
chmod +x *-review.sh
./start-review.sh
```

Routine updates do not require stopping first. To enter maintenance mode:

```bash
./stop-review.sh
```

To restore the previous healthy image AND its Compose configuration:

```bash
./rollback-review.sh
```

Application logs: `logs/review-agent.log` and bounded daily/size archives.
Deployment log: `deploy.log` (plus one size-bounded archive).
Health: `curl --fail http://127.0.0.1:18110/health`.

Do not delete the TAR while using `start-review.sh`; it verifies `SHA256SUMS`.
Repeated installation of the same image/configuration preserves the prior rollback.
Never upload `.env`, source images, local logs, or the entire repository temp folder.
