#!/usr/bin/env bash
#
# cleanup.sh — borra todo lo que desplegaste.
#
# Crear recursos en la nube es la mitad del trabajo; saber apagarlos es la otra
# mitad. Este script hace exactamente dos cosas:
#
#   1. Vacía el bucket del sitio. CloudFormation no puede borrar un bucket que
#      todavía tiene objetos dentro.
#   2. Ejecuta 'sam delete', que borra la stack y con ella el resto de recursos:
#      API, Lambdas, tabla, API key de Location y log groups.
#
# Uso:
#   ./scripts/cleanup.sh          # pide confirmación
#   ./scripts/cleanup.sh --yes    # sin preguntar
#
set -euo pipefail

STACK_NAME="${STACK_NAME:-safe-space}"
AWS_REGION="${AWS_REGION:-us-east-1}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -t 1 ]]; then
  BOLD=$'\033[1m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; DIM=$'\033[2m'; RESET=$'\033[0m'
else
  BOLD=""; GREEN=""; YELLOW=""; DIM=""; RESET=""
fi

printf '\n%s🧹 Safe Space · cleanup%s\n\n' "$BOLD" "$RESET"

if ! aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$AWS_REGION" >/dev/null 2>&1; then
  printf '  No existe la stack "%s" en %s. No hay nada que borrar.\n\n' "$STACK_NAME" "$AWS_REGION"
  exit 0
fi

bucket_name="$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --region "$AWS_REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='WebsiteBucketName'].OutputValue" \
  --output text)"

# Una stack que falló al crearse no llegó a publicar sus Outputs.
if [[ -z "$bucket_name" || "$bucket_name" == "None" ]]; then
  bucket_name=""
fi

printf '%sSe borrarán, de forma irreversible:%s\n' "$YELLOW" "$RESET"
printf '  · la stack de CloudFormation "%s" y todos sus recursos\n' "$STACK_NAME"
if [[ -n "$bucket_name" ]]; then
  printf '  · el contenido del bucket s3://%s\n' "$bucket_name"
  printf '  · los espacios que registraste en DynamoDB\n'
fi
printf '\n'

if [[ "${1:-}" != "--yes" ]]; then
  read -r -p "Escribe 'borrar' para confirmar: " answer
  if [[ "$answer" != "borrar" ]]; then
    printf '\nCancelado. No se borró nada.\n\n'
    exit 0
  fi
  printf '\n'
fi

# 1. Vaciar el bucket del sitio.
if [[ -n "$bucket_name" ]]; then
  aws s3 rm "s3://${bucket_name}" --recursive --region "$AWS_REGION" --only-show-errors || true
  printf '  %s✓%s Bucket del sitio vaciado\n' "$GREEN" "$RESET"
fi

# 2. Borrar la stack.
sam delete --stack-name "$STACK_NAME" --region "$AWS_REGION" --no-prompts >/dev/null
printf '  %s✓%s Stack "%s" eliminada\n' "$GREEN" "$RESET" "$STACK_NAME"

# 3. La configuración local ya no apunta a nada.
rm -f "$REPO_ROOT/frontend/config.js"
printf '  %s✓%s frontend/config.js eliminado\n' "$GREEN" "$RESET"

printf '\n%s✓ Cuenta limpia.%s\n' "$GREEN" "$RESET"
printf '%sCompruébalo en la consola: CloudFormation > Stacks (no debería aparecer "%s").%s\n\n' \
  "$DIM" "$STACK_NAME" "$RESET"
