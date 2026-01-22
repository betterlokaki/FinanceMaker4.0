#!/bin/bash
# Script to upload PEM files to GCP Secret Manager
# Usage: ./upload_secrets.sh YOUR_PROJECT_ID
#
# Note: API keys should be added to GitHub Secrets, not GCP Secret Manager
# This script only uploads PEM files (binary files that need file mounting)

set -e

if [ -z "$1" ]; then
    echo "Usage: $0 YOUR_PROJECT_ID"
    echo "Example: $0 financemaker-prod"
    exit 1
fi

PROJECT_ID="$1"
echo "Uploading PEM files to GCP Secret Manager for project: $PROJECT_ID"
echo ""

# Check if PEM files exist
if [ ! -f "secrets/interactive/paper/dhparam_paper.pem" ]; then
    echo "ERROR: secrets/interactive/paper/dhparam_paper.pem not found"
    exit 1
fi

if [ ! -f "secrets/interactive/real/dhparam.pem" ]; then
    echo "ERROR: secrets/interactive/real/dhparam.pem not found"
    exit 1
fi

echo "=== Uploading Paper Trading PEM Files ==="
gcloud secrets create ibkr-paper-dh-param \
  --project="$PROJECT_ID" \
  --data-file=secrets/interactive/paper/dhparam_paper.pem 2>/dev/null || \
  gcloud secrets versions add ibkr-paper-dh-param \
  --project="$PROJECT_ID" \
  --data-file=secrets/interactive/paper/dhparam_paper.pem

gcloud secrets create ibkr-paper-encryption-key \
  --project="$PROJECT_ID" \
  --data-file=secrets/interactive/paper/private_encryption_paper.pem 2>/dev/null || \
  gcloud secrets versions add ibkr-paper-encryption-key \
  --project="$PROJECT_ID" \
  --data-file=secrets/interactive/paper/private_encryption_paper.pem

gcloud secrets create ibkr-paper-signature-key \
  --project="$PROJECT_ID" \
  --data-file=secrets/interactive/paper/private_signature_paper.pem 2>/dev/null || \
  gcloud secrets versions add ibkr-paper-signature-key \
  --project="$PROJECT_ID" \
  --data-file=secrets/interactive/paper/private_signature_paper.pem

echo ""
echo "=== Uploading Real Trading PEM Files ==="
gcloud secrets create ibkr-real-dh-param \
  --project="$PROJECT_ID" \
  --data-file=secrets/interactive/real/dhparam.pem 2>/dev/null || \
  gcloud secrets versions add ibkr-real-dh-param \
  --project="$PROJECT_ID" \
  --data-file=secrets/interactive/real/dhparam.pem

gcloud secrets create ibkr-real-encryption-key \
  --project="$PROJECT_ID" \
  --data-file=secrets/interactive/real/private_encryption.pem 2>/dev/null || \
  gcloud secrets versions add ibkr-real-encryption-key \
  --project="$PROJECT_ID" \
  --data-file=secrets/interactive/real/private_encryption.pem

gcloud secrets create ibkr-real-signature-key \
  --project="$PROJECT_ID" \
  --data-file=secrets/interactive/real/private_signature.pem 2>/dev/null || \
  gcloud secrets versions add ibkr-real-signature-key \
  --project="$PROJECT_ID" \
  --data-file=secrets/interactive/real/private_signature.pem

echo ""
echo "✅ PEM files uploaded successfully!"
echo ""
echo "=== Next Steps ==="
echo "1. Add API keys to GitHub Secrets (see DEPLOYMENT_SECRETS_CHECKLIST.md)"
echo "   - Go to: GitHub Repository → Settings → Secrets and variables → Actions"
echo "   - Add all 15 GitHub secrets listed in the checklist"
echo ""
echo "2. Verify GCP secrets:"
echo "   gcloud secrets list --project=$PROJECT_ID"
echo ""
echo "3. Configure GitHub Secrets (15 total):"
echo "   - 4 GCP config secrets"
echo "   - 5 Paper trading API keys"
echo "   - 5 Real trading API keys"
