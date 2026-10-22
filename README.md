# Release Pipeline Dashboard

Import JUnit XML reports into SQLite and inspect build history in a local browser dashboard. The page shows pass rates by build, the ten slowest tests, and tests that failed in at least three of the latest ten builds. The JSON API is available at `/api/builds`.

```sh
python dashboard.py import pipeline.db my-project build-001 examples/junit.xml
python dashboard.py serve pipeline.db --port 8080
python -m unittest discover -s tests -v
```

Imports are idempotent for a project and build ID. The server binds to `127.0.0.1`. A build is marked red when the latest two builds for that project contain failures.

