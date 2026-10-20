# Vercel Streamlit Compatibility POC

This is an isolated compatibility test, not a production deployment. It uses the existing
Streamlit app, disables the embedded FPL scheduler, and points SQLite, ChromaDB, raw caches,
and the Hugging Face model cache at temporary `/tmp/fpl-vercel-poc` paths. Vercel container
storage is ephemeral. Do not use this POC to migrate or persist the production database, vector
collection, or squad.

The root `Dockerfile.vercel` uses the documented Vercel Container Runtime convention. For a
single container Vercel automatically detects the file and routes traffic to it, so this POC
does not require a `vercel.json`. The image listens on `0.0.0.0` and uses Vercel's `PORT`
environment variable (defaulting to port 80 for local container checks). Existing `.dockerignore`
rules exclude `.env`, virtual environments, and the `data/` directory from the build context.

## Local container check

From the repository root, with Docker Engine available:

```sh
docker build -f Dockerfile.vercel -t fpl-rag-vercel-poc .
docker run --rm -e PORT=8080 -p 8080:8080 fpl-rag-vercel-poc
```

In another terminal, check `http://127.0.0.1:8080` and
`http://127.0.0.1:8080/_stcore/health`. This only checks the local image; it is not proof
of Vercel routing, WebSocket, scale-down, or cold-start behavior.

## Isolated Vercel preview

1. Push the `vercel-poc` branch to the GitHub repository.
2. In Vercel, import/connect that repository as a **separate POC project** (do not change
   production project settings). Use the repository root as the project root and the
   `vercel-poc` branch for the test deployment.
3. Leave app secrets unset and use only temporary local storage.
   Do not configure production database/vector credentials or the production data mount.
4. Deploy a preview using the Vercel Container Runtime. If the project is configured to deploy
   the selected branch as production, stop and configure an isolated Vercel project/preview
   before proceeding.
5. Check startup logs, HTTP response, and Streamlit WebSocket connection in browser developer
   tools. Exercise page navigation, controls, and a session long enough to observe reconnect
   behavior. Leave the preview idle beyond the documented preview scale-down period, then
   revisit it and measure cold start. Repeat after a controlled redeployment/restart.
6. Record actual build/image size, startup time, peak memory, browser events, and service logs.
   A successful HTTP response alone does not prove Streamlit compatibility.

Vercel's current documentation describes WebSockets and the custom Container Runtime as beta;
containers scale down after inactivity and connections can close at the function maximum
duration. Compatibility, the Hobby resource envelope, and this application's interactive
Streamlit behavior must be established by the actual isolated preview before considering any
production architecture.

This POC deliberately does not configure a Vercel Daemon or cron job. The existing worker
uses SQLite and a local filesystem lock, and Daemons do not provide persistent local storage.
Do not run it against production data. A future worker test requires external durable data and
lock services, and a separately authenticated preview/production design.
