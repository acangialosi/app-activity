# Azure Artifact Signing trial

This branch integrates [Azure Artifact Signing](https://learn.microsoft.com/azure/artifact-signing/)
with the Windows installer workflow. The signing keys remain in Azure; GitHub
authenticates with OpenID Connect (OIDC), so there is no private certificate file
or Azure client secret to store in the repository.

The workflow signs `AppActivity.exe`, packages that signed executable into the
installer, signs `AppActivity-Setup.exe`, and verifies both signatures before
publishing. Pull request builds remain unsigned so untrusted pull request code
cannot use the signing identity.

## 1. Create the Azure resources

1. Use a paid Azure subscription. Artifact Signing does not support free, trial,
   or sponsored subscriptions.
2. In the Azure portal, create an **Artifact Signing account** in a supported
   region.
3. Complete identity validation. For a personal project, use an individual
   identity if it is available in your country.
4. Create a **Public Trust** certificate profile in the account.
5. Record:
   - The regional endpoint, such as `https://eus.codesigning.azure.net/`.
   - The Artifact Signing account name.
   - The certificate profile name.

The Basic plan is normally sufficient for this project. Current pricing and
regional availability are listed on the
[Artifact Signing pricing page](https://azure.microsoft.com/pricing/details/artifact-signing/).

## 2. Create the GitHub OIDC identity

1. In Microsoft Entra ID, create an app registration and service principal.
2. Record its **Application (client) ID** and **Directory (tenant) ID**.
3. Add a federated credential for this trial branch:
   - Issuer: `https://token.actions.githubusercontent.com`
   - Subject: `repo:acangialosi/app-activity:ref:refs/heads/dev/azure-artifact-signing`
   - Audience: `api://AzureADTokenExchange`
4. On the Artifact Signing account, open **Access control (IAM)** and assign the
   service principal the **Artifact Signing Certificate Profile Signer** role.

Microsoft's detailed OIDC instructions are in
[Authenticate to Azure from GitHub Actions by OpenID Connect](https://learn.microsoft.com/azure/developer/github/connect-from-azure-openid-connect).

## 3. Configure this GitHub repository

In **Settings > Secrets and variables > Actions**, add these repository secrets:

| Secret | Value |
|---|---|
| `AZURE_CLIENT_ID` | App registration's Application (client) ID |
| `AZURE_TENANT_ID` | Microsoft Entra Directory (tenant) ID |
| `AZURE_SUBSCRIPTION_ID` | Azure subscription ID |

Add these repository variables:

| Variable | Value |
|---|---|
| `ARTIFACT_SIGNING_ENABLED` | `true` |
| `ARTIFACT_SIGNING_ENDPOINT` | Regional endpoint recorded above |
| `ARTIFACT_SIGNING_ACCOUNT_NAME` | Artifact Signing account name |
| `ARTIFACT_SIGNING_CERTIFICATE_PROFILE_NAME` | Public Trust profile name |

The three Azure identifiers are stored as secrets to follow Microsoft's GitHub
Actions guidance even though they are identifiers rather than passwords.

## 4. Run and verify the trial

Push or manually run the **Windows installer** workflow on
`dev/azure-artifact-signing`. Confirm these steps succeed in order:

1. `Build application for Artifact Signing`
2. `Log in to Azure`
3. `Sign application with Artifact Signing`
4. `Package signed application`
5. `Sign installer with Artifact Signing`
6. `Verify Artifact Signing signatures`

The verification step prints the certificate subject and fails the build unless
Windows reports both signatures as `Valid`.

Download the manual-run artifact and verify it locally:

```powershell
Get-AuthenticodeSignature .\AppActivity-Setup.exe |
    Format-List Status, StatusMessage, SignerCertificate
```

The expected status is `Valid`. A newly signed publisher can still receive a
SmartScreen reputation warning initially, but Windows should display the
verified publisher rather than `Unknown publisher`.

## 5. Promote or discard

Before merging to `main`, add another federated credential with this subject:

```text
repo:acangialosi/app-activity:ref:refs/heads/main
```

To abandon the experiment, switch back to `main` and delete the branch:

```powershell
git switch main
git branch -D dev/azure-artifact-signing
git push origin --delete dev/azure-artifact-signing
```

Then remove the four `ARTIFACT_SIGNING_*` variables, three `AZURE_*` secrets,
federated credential, role assignment, certificate profile, and Artifact
Signing account if they are no longer needed.
