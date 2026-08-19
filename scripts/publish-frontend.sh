#!/usr/bin/env bash
#
# publish-frontend.sh — conecta el frontend con tu stack y lo publica en S3.
#
# Hace tres cosas, en este orden:
#
#   1. Lee los Outputs de la stack de CloudFormation.
#   2. Obtiene el VALOR de la API key de Amazon Location.
#      CloudFormation crea la key pero no devuelve su valor como Output —solo
#      su nombre—, así que hay que pedírselo a la API con describe-key.
#   3. Escribe frontend/config.js y sincroniza la carpeta al bucket del sitio.
#
# Uso:
#   ./scripts/publish-frontend.sh
#
set -euo pipefail

STACK_NAME="${STACK_NAME:-safe-spot}"
AWS_REGION="${AWS_REGION:-us-east-1}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND_DIR="$REPO_ROOT/frontend"
CONFIG_FILE="$FRONTEND_DIR/config.js"

if [[ -t 1 ]]; then
  BOLD=$'\033[1m'; GREEN=$'\033[32m'; DIM=$'\033[2m'; RESET=$'\033[0m'
else
  BOLD=""; GREEN=""; DIM=""; RESET=""
fi

printf '\n%s🌈 Safe Spot · publicar frontend%s\n\n' "$BOLD" "$RESET"

# --------------------------------------------------------------------------
# 1. Outputs de la stack
# --------------------------------------------------------------------------
stack_output() {
  aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --region "$AWS_REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" \
    --output text
}

if ! aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$AWS_REGION" >/dev/null 2>&1; then
  printf '✗ No existe la stack "%s" en %s. Ejecuta primero: sam deploy\n\n' "$STACK_NAME" "$AWS_REGION" >&2
  exit 1
fi

api_url="$(stack_output ApiUrl)"
website_url="$(stack_output WebsiteUrl)"
bucket_name="$(stack_output WebsiteBucketName)"
key_name="$(stack_output MapsApiKeyName)"
allowed_signals="$(stack_output AllowedSignals)"
allowed_categories="$(stack_output AllowedCategories)"

printf '  %s✓%s Outputs leídos de la stack %s\n' "$GREEN" "$RESET" "$STACK_NAME"

# --------------------------------------------------------------------------
# 2. Valor de la API key de Amazon Location
# --------------------------------------------------------------------------
maps_api_key="$(aws location describe-key \
  --key-name "$key_name" \
  --region "$AWS_REGION" \
  --query Key \
  --output text)"

printf '  %s✓%s API key de Amazon Location obtenida (%s)\n' "$GREEN" "$RESET" "$key_name"

# --------------------------------------------------------------------------
# 3. Generar config.js y sincronizar
# --------------------------------------------------------------------------
to_json_array() {
  # "a,b,c" -> ["a", "b", "c"]
  printf '%s' "$1" | awk -F',' '{
    printf "["
    for (i = 1; i <= NF; i++) printf "%s\"%s\"", (i > 1 ? ", " : ""), $i
    printf "]"
  }'
}

cat > "$CONFIG_FILE" <<EOF
// Generado por scripts/publish-frontend.sh — no lo edites a mano.
// Contiene la API key de Amazon Location de tu cuenta: está en .gitignore.
window.SAFE_SPOT_CONFIG = {
  apiUrl: "${api_url}",
  region: "${AWS_REGION}",
  mapsApiKey: "${maps_api_key}",
  allowedSignals: $(to_json_array "$allowed_signals"),
  allowedCategories: $(to_json_array "$allowed_categories"),
};
EOF

printf '  %s✓%s frontend/config.js generado\n' "$GREEN" "$RESET"

# --delete borra del bucket lo que ya no existe en local, para que el sitio
# publicado sea siempre un reflejo exacto de la carpeta frontend/.
aws s3 sync "$FRONTEND_DIR" "s3://${bucket_name}" \
  --region "$AWS_REGION" \
  --delete \
  --exclude "config.example.js" \
  --only-show-errors

printf '  %s✓%s Frontend sincronizado con s3://%s\n' "$GREEN" "$RESET" "$bucket_name"

printf '\n%sAbre tu Safe Spot:%s\n' "$BOLD" "$RESET"
printf '  %s\n' "$website_url"
printf '\n%sAPI: %s%s\n\n' "$DIM" "$api_url" "$RESET"
