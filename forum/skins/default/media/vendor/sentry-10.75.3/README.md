# Sentry browser SDK 10.75.3 (errors only)

`sentry.bundle.min.js` exposes `window.Sentry` with `init`, `captureException`,
`captureMessage`, `setUser`, `setTag`, `setContext`, `addBreadcrumb` and
`withScope`. It is used by `media/js/osqa.sentry.js` when `SENTRY_FRONTEND_DSN`
is set. Sentry's CDN bundles are not on npm, so it was built from the npm package:

```bash
npm install --ignore-scripts --save-exact @sentry/browser@10.75.3 esbuild@0.25.10
npx esbuild entry.js --bundle --minify --format=iife --global-name=Sentry \
    --target=es2017 --legal-comments=eof --outfile=sentry.bundle.min.js
```
