# Screenshot Assets

This directory now contains two kinds of documentation visuals:

1. `*.svg` illustrative previews committed to the repository, and
2. `runtime/*.png` real screenshots captured from a running PyTaskForge instance.

## Real screenshot capture flow

The runtime capture flow is implemented in:

- `frontend/scripts/capture-screenshots.mjs`
- `frontend/package.json` scripts:
  - `npm run capture:screenshots:install`
  - `npm run capture:screenshots`

## Default assumptions

The capture script assumes:

- backend API is reachable at `http://127.0.0.1:8000`
- frontend UI is reachable at `http://127.0.0.1:5173`
- dev mode is enabled, or `/api/auth/token` accepts the provided credentials
- the repository `jobs/hello_world.py` script exists

## Environment overrides

You can override the defaults with:

- `PTF_SCREENSHOT_UI_BASE_URL`
- `PTF_SCREENSHOT_API_BASE_URL`
- `PTF_SCREENSHOT_USERNAME`
- `PTF_SCREENSHOT_PASSWORD`
- `PTF_SCREENSHOT_DEV_MODE`

## Example

```bash
cd /Users/mustafasahin/PycharmProjects/PythonProject1/pytaskforge/frontend
npm install
npm run capture:screenshots:install
npm run capture:screenshots
```

