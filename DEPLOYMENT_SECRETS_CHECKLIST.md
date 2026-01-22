# Deployment Secrets Checklist

## Strategy: Maximize GitHub Secrets, Minimize GCP

**GitHub Secrets**: All API keys and tokens (15 secrets)  
**GCP Secret Manager**: Only PEM files (6 secrets)

This approach maximizes portability - if you switch cloud providers, you only need to reconfigure the 6 PEM files.

---

## 1. GitHub Secrets (15 secrets)

Go to: **GitHub Repository → Settings → Secrets and variables → Actions**

### GCP Configuration (4 secrets)
| Secret Name | Value | How to Get |
|------------|-------|------------|
| `GCP_PROJECT_ID` | Your GCP project ID | e.g., `financemaker-prod` |
| `GCP_WIF_PROVIDER` | Full Workload Identity Provider path | Format: `projects/YOUR_PROJECT_NUMBER/locations/global/workloadIdentityPools/github-actions-pool/providers/github-provider` |
| `GCP_SA_EMAIL` | Service account email | Format: `financemaker-cicd@YOUR_PROJECT_ID.iam.gserviceaccount.com` |
| `GCP_REGION` | Cloud Run region | e.g., `us-central1` |

**To get your project number:**
```bash
gcloud projects describe YOUR_PROJECT_ID --format="value(projectNumber)"
```

### Paper Trading API Keys (5 secrets)
Extract from `.env` file - **uncommented section** (paper trading):

| Secret Name | Value Source | Example |
|------------|--------------|---------|
| `GROK_API_KEY_PAPER` | `GROK_API_KEY=` from .env | Your Grok API key |
| `GEMINI_API_KEY_PAPER` | `GEMINI_API_KEY=` from .env | Your Gemini API key |
| `IBKR_ACCESS_TOKEN_PAPER` | `IBKR_ACCESS_TOKEN=` from .env | Your IBKR OAuth access token |
| `IBKR_ACCESS_TOKEN_SECRET_PAPER` | `IBKR_ACCESS_TOKEN_SECRET=` from .env | Your IBKR OAuth access token secret |
| `IBKR_CONSUMER_KEY_PAPER` | `IBKR_CONSUMER_KEY=` from .env | Your IBKR OAuth consumer key |

### Real Trading API Keys (5 secrets)
Extract from `.env` file - **commented section after `# Real trading`**:

| Secret Name | Value Source | Example |
|------------|--------------|---------|
| `GROK_API_KEY_REAL` | `# GROK_API_KEY=` from .env | Your real Grok API key |
| `GEMINI_API_KEY_REAL` | `# GEMINI_API_KEY=` from .env | Your real Gemini API key |
| `IBKR_ACCESS_TOKEN_REAL` | `# IBKR_ACCESS_TOKEN=` from .env | Your real IBKR OAuth access token |
| `IBKR_ACCESS_TOKEN_SECRET_REAL` | `# IBKR_ACCESS_TOKEN_SECRET=` from .env | Your real IBKR OAuth access token secret |
| `IBKR_CONSUMER_KEY_REAL` | `# IBKR_CONSUMER_KEY=` from .env | Your real IBKR OAuth consumer key |

---

## 2. GCP Secret Manager (6 secrets - PEM files only)

Upload **only the PEM files** to GCP Secret Manager. These are binary files that need to be mounted as files in Cloud Run.

### Paper Trading PEM Files (3 secrets)

```bash
# Replace YOUR_PROJECT_ID with your actual GCP project ID

# DH Parameter
gcloud secrets create ibkr-paper-dh-param \
  --project=YOUR_PROJECT_ID \
  --data-file=secrets/interactive/paper/dhparam_paper.pem

# Encryption Key
gcloud secrets create ibkr-paper-encryption-key \
  --project=YOUR_PROJECT_ID \
  --data-file=secrets/interactive/paper/private_encryption_paper.pem

# Signature Key
gcloud secrets create ibkr-paper-signature-key \
  --project=YOUR_PROJECT_ID \
  --data-file=secrets/interactive/paper/private_signature_paper.pem
```

### Real Trading PEM Files (3 secrets)

```bash
# DH Parameter
gcloud secrets create ibkr-real-dh-param \
  --project=YOUR_PROJECT_ID \
  --data-file=secrets/interactive/real/dhparam.pem

# Encryption Key
gcloud secrets create ibkr-real-encryption-key \
  --project=YOUR_PROJECT_ID \
  --data-file=secrets/interactive/real/private_encryption.pem

# Signature Key
gcloud secrets create ibkr-real-signature-key \
  --project=YOUR_PROJECT_ID \
  --data-file=secrets/interactive/real/private_signature.pem
```

---

## 3. Quick Reference: All Secret Names

### GitHub Secrets (15 total)
**GCP Config:**
- `GCP_PROJECT_ID`
- `GCP_WIF_PROVIDER`
- `GCP_SA_EMAIL`
- `GCP_REGION`

**Paper Trading:**
- `GROK_API_KEY_PAPER`
- `GEMINI_API_KEY_PAPER`
- `IBKR_ACCESS_TOKEN_PAPER`
- `IBKR_ACCESS_TOKEN_SECRET_PAPER`
- `IBKR_CONSUMER_KEY_PAPER`

**Real Trading:**
- `GROK_API_KEY_REAL`
- `GEMINI_API_KEY_REAL`
- `IBKR_ACCESS_TOKEN_REAL`
- `IBKR_ACCESS_TOKEN_SECRET_REAL`
- `IBKR_CONSUMER_KEY_REAL`

### GCP Secret Manager (6 total - PEM files only)
**Paper Trading:**
- `ibkr-paper-dh-param`
- `ibkr-paper-encryption-key`
- `ibkr-paper-signature-key`

**Real Trading:**
- `ibkr-real-dh-param`
- `ibkr-real-encryption-key`
- `ibkr-real-signature-key`

---

## 4. Verification

### Verify GitHub Secrets
Go to: GitHub Repository → Settings → Secrets and variables → Actions
- Check that all 15 secrets are listed

### Verify GCP Secrets
```bash
# List all secrets
gcloud secrets list --project=YOUR_PROJECT_ID

# Verify a specific secret (without revealing value)
gcloud secrets describe SECRET_NAME --project=YOUR_PROJECT_ID
```

---

## 5. Benefits of This Approach

✅ **Portability**: 15/21 secrets in GitHub (71%) - easy to switch cloud providers  
✅ **Cost**: Only 6 secrets in GCP (minimal Secret Manager costs)  
✅ **Simplicity**: All API keys managed in one place (GitHub)  
✅ **Security**: PEM files remain in GCP for secure file mounting  
✅ **Flexibility**: Easy to update API keys without touching GCP

---

## 6. Notes

- **PEM files**: Must be in GCP Secret Manager because Cloud Run needs to mount them as files
- **API keys/tokens**: Stored in GitHub Secrets and passed as environment variables
- **Secret names**: Must match exactly as shown above (case-sensitive)
- **Project ID**: Replace `YOUR_PROJECT_ID` with your actual GCP project ID in all commands
