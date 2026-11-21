# Private desk preview

The Netlify build publishes only `public/robots.txt`. Dashboard assets are bundled with the server function. Its login page accepts an Identity invitation, and the server issues an opaque cookie only for the configured, confirmed user. The provider handles password storage. Signing out deletes the server session. Remembered sessions expire 30 days after sign-in. The collector trigger runs independently in Google Apps Script.

## Configure a preview

Use an isolated Netlify Free project and the repository's `netlify.toml`. Enable Identity with **Invite only** registration and email confirmation. Invite the owner using **Identity → Users**. Set `LAB_OWNER_EMAIL` and the invited Identity user's UUID in `LAB_OWNER_ID` for the Functions runtime and redeploy. Keep these settings out of the repository. The invite link opens the site's password-setup form.

The private build accepts `LAB_DESK_ASSET_DIR` for a refreshed deployment's docs directory. A branch preview defaults to the checked-in archive and reads the collector for a newer snapshot. To test, run `node prepare.mjs && npm test` inside `private-site`.

Check first-load and reload while signed in, then sign out and request the snapshot JSON, paper ledger and a replay URL. Check the direct URLs on the deployed preview and the logged-out browser back button.

## Production cutover

A preview does not secure the existing GitHub Pages site, public GitHub history, the raw paper feed or the Apps Script deployment. Before switching the domain:

- Feed the private build the same refreshed assets as the scheduled forecast publisher. Move ongoing paper-ledger publication behind an authenticated read path. Existing copies of public files may still exist.
- Configure the collector's existing `WEBHOOK_KEY` and supply it only to the private server as `LAB_COLLECTOR_READ_KEY` and to authorized background readers. Coordinate producer and reader settings so collection and publication continue. The collector's read endpoint can return a JSON authorization error with HTTP 200, so check its body.
- Verify the latest capture timestamp, foreground refresh, forecasts, paper ledger and replay against the existing desk. During a market session, verify that capture timestamps advance while no user is signed in.
- After the preview passes and the owner is ready, retire the old public publication, protect the source and paper feed as agreed with the owner, and verify anonymous requests at all former origins. Point the custom domain at the verified site.
