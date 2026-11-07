# volatilitylab.in — Official Website & GitHub Pages Bundle

## Contents
- `index.html`: Complete self-contained interactive website for https://volatilitylab.in
- `CNAME`: Custom domain configuration file (`volatilitylab.in`) for GitHub Pages
- `docs/index.html` & `docs/CNAME`: Drop-in `/docs` folder for your `pankagsaini/volatility-lab` repository

## 2-Minute Deployment on GoDaddy + GitHub Pages
1. Copy the `docs/` folder (containing `index.html` and `CNAME`) into the root of your `pankagsaini/volatility-lab` GitHub repository and push to `master`/`main`.
2. In GitHub (`https://github.com/pankagsaini/volatility-lab/settings/pages`), set Source to **Deploy from a branch**, Branch: `master` (or `main`), Folder: `/docs`, and click **Save**.
3. In GoDaddy DNS (`https://dcc.godaddy.com/control/portfolio/volatilitylab.in/settings?tab=dns`), add:
   - `A` record `@` -> `185.199.108.153`
   - `A` record `@` -> `185.199.109.153`
   - `A` record `@` -> `185.199.110.153`
   - `A` record `@` -> `185.199.111.153`
   - `CNAME` record `www` -> `pankagsaini.github.io`
4. In GitHub Pages settings, enter `volatilitylab.in` as the Custom domain and check **Enforce HTTPS**.
