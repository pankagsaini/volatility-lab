# Configuration and deployment

## Local desk

Install the package and launch `python -m volatility_lab`. For Streamlit Community Cloud, choose `streamlit_app.py` and Python 3.11. Tested versions are 3.10, 3.11 and 3.12.

The default bind address is loopback; LAN access requires `--lan`. Old launchers are wrappers and must contain no setup logic.

Broker tokens are optional for saved research. Direct collection requires an Upstox analytics token through the supported environment/Streamlit secret or encrypted local store. A `.env` file is not automatically loaded merely because it exists; export variables explicitly until the central settings layer is enabled. Never put credentials in public HTML, URL query strings, committed settings or logs.

## Website

Pages uses **GitHub Actions**, not legacy branch deployment. `.github/workflows/pages.yml` checks browser tests/parity, fetches RBI/NSE inputs into the deployment artifact, uploads `docs/` and deploys with scoped Pages/OIDC permissions.

It runs on master pushes, manual dispatch and weekdays at 03:00 UTC (08:30 IST). Source failure retains dated committed inputs. The browser withholds model calculations after seven days without a verified rate check or after a factsheet ages beyond 62 days. Broker observations remain separate.

`CNAME` and `docs/CNAME` preserve volatilitylab.in. See the [domain guide](../README_DEPLOY_GODADDY.md) for DNS details; use this page for the current deployment route.

Merge through protected PR checks. Verify deployed HTML, published pricing-input dates and browser status. Roll back by reverting through a checked PR.

## Apps Script

Follow [cloud setup](../volatility_lab/cloud/apps_script/SETUP.md). Tokens and trigger authorization are managed inside Apps Script; a website deployment does not update Apps Script.

ContentService responses use application status fields; an authorization failure is not necessarily HTTP 401. Public reads must remain separate from privileged collection/configuration actions.
