# NOVA landing source preservation

This branch is a source checkpoint only. It is based on
`dbf7be6ae5e0a05deff3968624f8786ec6cdcb1d` and must never be used as the
runtime integration base.

The eventual integration branch must start from the final tested and deployed
Paper CAS/idempotent-exit SHA. Landing files should then be copied selectively
from this branch.

## Preserved source map

- `frontend/src/landing/LandingPage.tsx`
  - imports `PlanetaryEcosystem`
  - imports `components/CinematicHero`
  - imports `components/Marquee`
  - imports the 17 reviewed images from `frontend/src/assets/landing`
  - expects the separately deployed product video at
    `/media/NOVA_Signal_Route_product_animation.mp4`
- `frontend/src/landing/PlanetaryEcosystem.tsx`
  - imports `gsap`
- `frontend/src/landing/components/CinematicHero.tsx`
  - imports `gsap`, `gsap/ScrollTrigger`, `utils`, and three reviewed images
- `frontend/src/landing/components/Marquee.tsx`
  - imports React types and `utils`
- `frontend/src/landing/utils.ts`
  - imports the existing `clsx` dependency
- `frontend/src/landing/landing.css`
  - contains landing-scoped utilities and system-safe font fallbacks

The only new package required by the preserved source is:

```text
gsap ^3.12.7
```

`react` and `clsx` already exist on the old source base and the current runtime.
The integration must add GSAP to the final runtime `package.json` and regenerate
the lockfile there. The old package manifest and lockfile were deliberately not
copied.

## Reviewed images

All included images are referenced by the landing source, use Linux-safe exact
case, and are stored under `frontend/src/assets/landing` so Vite can emit hashed
production assets.

The following unreferenced files were deliberately omitted:

- `frontend/public/footer.png`
- `frontend/public/rayanimation.png`
- `frontend/public/hoveranimation/www.slash.com_.png`

## Separately preserved video

The video is intentionally not committed to the frontend repository.

```text
Local source:
C:\Users\anubh\OneDrive\Desktop\Layman-nova-signal-route\frontend\public\video\NOVA_Signal_Route_product_animation.mp4

Size:
10,287,893 bytes

SHA-256:
AE48FB54FD5F764444100950448AB5BF32D1A9CB2160EBF2BCEA12B8E82971BE

Future VPS destination:
/var/www/nova-media/NOVA_Signal_Route_product_animation.mp4

Future public URL:
/media/NOVA_Signal_Route_product_animation.mp4
```

The MP4 should be optimized for fast start before deployment. The future Nginx
location must return strict 404 responses for missing media, use `video/mp4`,
support byte ranges, and must not fall through to the SPA HTML page.

## Font licensing decision

No font binaries or font archives are included.

The source archive for PP Editorial states `Personal Use Only`, so those files
are not suitable for NOVA's public commercial landing page. The Neue Montreal
files also lack verified production licensing in the inspected source package.
The preserved CSS therefore uses Georgia and Arial/Helvetica fallbacks.

Production integration may replace these fallbacks only after suitable webfont
licenses and optimized webfont files are available.

## Deliberately excluded shared files

No changes were copied from:

- `frontend/src/App.tsx`
- `frontend/src/components/Header.tsx`
- `frontend/src/components/MobileNavBar.tsx`
- `frontend/src/types.ts`
- `frontend/src/index.css`
- `frontend/index.html`
- `frontend/package.json`
- `frontend/package-lock.json`
- `frontend/vite.config.ts`
- `frontend/tsconfig.app.json`
- runtime, chart, setup, strategy, authentication, chatbot, or backend files

The original Windows/Gemini asset-copy helpers, nodemon configuration, font
archives, and remote-font HTML links are also excluded. No `file://`,
`localhost`, Gemini temporary path, blob URL, or private attachment URL is
required to render the preserved landing source. The only intentional
root-relative external asset is the future `/media/` video URL.

## Preservation validation

Validation was performed without adding the landing page to the old runtime
`App.tsx` and without writing a production `dist` directory.

- TypeScript project compilation passed.
- A Vite production build executed in memory with `write: false`.
- Vite transformed 34 modules and emitted the landing JavaScript, scoped CSS,
  and exactly 17 content-hashed image assets in memory.
- All local image imports resolved with exact filename case.
- The source has no imports from runtime, chart, setup, strategy,
  authentication, chatbot, API, WebSocket, or backend modules.
- The source requires no Windows-only or Gemini asset-copy step.
- Rendering the public component itself has no authentication or backend side
  effect. The optional `onEnterApp` callback remains an integration concern.

This validates the preserved source in isolation. It does not claim that the
landing page has been integrated with or deployed alongside NOVA.
