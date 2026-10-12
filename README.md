# MovieRecapAI Server v13

Render-ready FFmpeg processing server for MovieRecapAI.

Endpoints: `/health`, `/capabilities`, `/upload/movie`, `/upload/srt`, `/upload/audio`, `/upload/logo`, `/jobs/from-uploads`, `/jobs/{job_id}`, `/outputs/{filename}`, `/thumbnail`, `/copyright/check`, `/self-test`.

The server accepts `X-API-Key` when `MOVIERECAP_API_KEY` is configured.
