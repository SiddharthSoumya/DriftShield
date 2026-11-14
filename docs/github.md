# GitHub setup and coverage

## Public repositories

Add an `owner/repository` name from the overview. Public monitoring uses anonymous GitHub REST calls. GitHub's anonymous limit is shared per outbound IP. Two calls per repository are typical, with more for pagination. Use a GitHub App for larger inventories and for changes that remove public visibility.

## Private and organization repositories

1. Register a [GitHub App](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/registering-a-github-app) for the customer workspace. Select Repository Metadata read access. Disable webhook delivery for this polling release. Choose selected repositories when installing.
2. Copy the App client ID, its numeric installation ID, and a generated RSA private key to your server using a secure transfer. Store the key in a temporary file readable by the `driftshield` service user and accessible only to that user and server administrators.
3. Bind the installation using the server CLI. Remove the temporary key file after successful binding.

   ```bash
   sudo driftshieldctl connectgithub engineering APP_CLIENT_ID INSTALLATION_ID /secure/path/app.pem
   ```

4. Sign in as the workspace owner and select **Import GitHub App repositories**.
5. Repeat the bind command with a newly generated key to rotate it. Verify collection, then revoke the old private key at GitHub.

The server verifies installation identity using an RS256 App JWT. The worker obtains a short-lived token scoped to Metadata read and the selected repository ID. The App private key is encrypted before storage. Use a separate App per trust boundary. A shared App private key carries authority across all installations of that App.

## Supported observation

DriftShield records repository visibility, default branch, archived status, and normalized active ruleset rule types and parameters applying to that branch. Organization rulesets can contribute effective rules. The default branch rules comparison ignores provider bookkeeping and duplicate equivalent rules. It covers the returned configuration, not the effective privileges of a particular user. Classic branch protection, ruleset bypass lists and inactive rules are outside this snapshot. GitHub plan coverage affects the meaning of an empty rules result.

A GitHub HTTP error, partial response, invalid schema, or truncated page set fails the scan. DriftShield preserves the last good observation. Once a repository ID is known, the collector uses it to follow a rename, subject to continued installation access.

[Effective branch rules endpoint](https://docs.github.com/en/rest/repos/rules#get-rules-for-a-branch) · [GitHub App installation authentication](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/authenticating-as-a-github-app-installation) · [Rate limits](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api)
