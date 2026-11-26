# Configuration and deployment

## Local research

Install the package and run `python -m volatility_lab --health`. Preview the static website with `python -m http.server 8000 --directory docs`. Tested Python versions are 3.11, 3.12 and 3.13.

Broker tokens are optional for saved research. Direct collection requires an Upstox analytics token through the supported environment variable or encrypted local store. Export settings in the process environment or point `VOL_LAB_ENV_FILE` at a local file with `KEY=value` lines. Exported values take precedence. Never put credentials in public HTML, URL query strings, committed settings or logs.

## Website

For the optional local HTTP API, install `python -m pip install ".[web]"` and run `python -m volatility_lab.web_server`. The checkout serves the desk from `docs/`. The installed wheel exposes API documentation at `http://127.0.0.1:8000/docs`.

Pages uses **GitHub Actions**, not legacy branch deployment. `.github/workflows/pages.yml` checks browser tests/parity, fetches RBI/NSE inputs into the deployment artifact, uploads `docs/` and deploys with scoped Pages/OIDC permissions.

It runs on master pushes, manual dispatch and daily at 03:00, 12:00 and 15:00 UTC (08:30, 17:30 and 20:30 IST). These are scheduled starts. The publication run records the actual deployment time. Source failure retains dated committed inputs. The browser withholds model calculations after seven days without a verified rate check or after a factsheet ages beyond 62 days. Broker observations remain separate.

`CNAME` and `docs/CNAME` preserve volatilitylab.in. See the [domain guide](../README_DEPLOY_GODADDY.md) for DNS details; use this page for the current deployment route.

Pages publishes the dashboard assets. Cloudflare deploys the invite and landing-page logic separately. For an edge release, record the Worker version and custom-domain route in the operator's deployment record, then open the homepage in a signed-out browser and compare it with the intended landing page. Verify the invited desk in a separate signed-in session.

The public repository contains the committed replay files, research CSVs, dashboard snapshot and the prospective paper feed. Website sign-in applies to the branded site. The current paper publishing job requires a public repository. A change in dataset visibility also requires a publishing plan for that job.

Merge through protected PR checks. Verify deployed HTML, published pricing-input dates and browser status. Roll back by reverting through a checked PR.

## Apps Script

Follow [cloud setup](../volatility_lab/cloud/apps_script/SETUP.md). Tokens and trigger authorization are managed inside Apps Script; a website deployment does not update Apps Script.

After a collector release, record the active script version, trigger owner, recent execution times and remaining runtime quota. Check front-chain quote coverage at 09:20, 09:25 and 15:15 IST and reconcile partial captures against their catalogue dates.

ContentService responses use application status fields; an authorization failure is not necessarily HTTP 401. Public reads must remain separate from privileged collection/configuration actions.
