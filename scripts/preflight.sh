#!/usr/bin/env bash
#
# preflight.sh — comprueba que tu entorno puede desplegar Safe Spot.
#
# Este script SOLO LEE. No crea, no modifica y no borra nada, y en particular
# nunca relaja la seguridad de tu cuenta: si encuentra un bloqueo, te lo dice
# para que tú decidas qué hacer.
#
# Uso:
#   ./scripts/preflight.sh
#
set -euo pipefail

STACK_NAME="${STACK_NAME:-safe-spot}"
AWS_REGION="${AWS_REGION:-us-east-1}"
EXPECTED_REGION="us-east-1"
MODEL_ID="${MODEL_ID:-amazon.nova-micro-v1:0}"

if [[ -t 1 ]]; then
  BOLD=$'\033[1m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RED=$'\033[31m'; DIM=$'\033[2m'; RESET=$'\033[0m'
else
  BOLD=""; GREEN=""; YELLOW=""; RED=""; DIM=""; RESET=""
fi

failures=0
warnings=0

pass()  { printf '  %s✓%s %s\n' "$GREEN" "$RESET" "$1"; }
warn()  { printf '  %s!%s %s\n' "$YELLOW" "$RESET" "$1"; warnings=$((warnings + 1)); }
fail()  { printf '  %s✗%s %s\n' "$RED" "$RESET" "$1"; failures=$((failures + 1)); }
hint()  { printf '    %s%s%s\n' "$DIM" "$1" "$RESET"; }

printf '\n%s🌈 Safe Spot · preflight%s\n' "$BOLD" "$RESET"
printf '%sRegión objetivo: %s · Stack: %s%s\n\n' "$DIM" "$AWS_REGION" "$STACK_NAME" "$RESET"

# --------------------------------------------------------------------------
printf '%sHerramientas%s\n' "$BOLD" "$RESET"
# --------------------------------------------------------------------------
if command -v aws >/dev/null 2>&1; then
  pass "AWS CLI $(aws --version 2>&1 | cut -d' ' -f1)"
else
  fail "No se encontró la AWS CLI."
  hint "En AWS CloudShell viene preinstalada. ¿Estás dentro de CloudShell?"
fi

if command -v sam >/dev/null 2>&1; then
  pass "AWS SAM CLI $(sam --version 2>&1 | awk '{print $NF}')"
else
  fail "No se encontró la SAM CLI."
  hint "En AWS CloudShell viene preinstalada. ¿Estás dentro de CloudShell?"
fi

if command -v python3 >/dev/null 2>&1; then
  pass "Python $(python3 --version 2>&1 | awk '{print $2}')"
  if python3 -c "import boto3" >/dev/null 2>&1; then
    pass "boto3 disponible (lo necesita scripts/seed.py)"
  else
    fail "boto3 no está instalado para python3."
    hint "Instálalo con: pip3 install --user boto3"
  fi
else
  fail "No se encontró python3."
fi

# --------------------------------------------------------------------------
printf '\n%sCuenta y región%s\n' "$BOLD" "$RESET"
# --------------------------------------------------------------------------
if identity="$(aws sts get-caller-identity --output json 2>/dev/null)"; then
  account="$(printf '%s' "$identity" | python3 -c 'import json,sys; print(json.load(sys.stdin)["Account"])')"
  arn="$(printf '%s' "$identity" | python3 -c 'import json,sys; print(json.load(sys.stdin)["Arn"])')"
  pass "Credenciales activas · cuenta ${account}"
  hint "$arn"
else
  fail "No hay credenciales de AWS utilizables."
  hint "En CloudShell las credenciales son automáticas. Fuera de CloudShell: aws configure"
  account=""
fi

effective_region="$(aws configure get region 2>/dev/null || true)"
effective_region="${AWS_REGION:-$effective_region}"
if [[ "$effective_region" == "$EXPECTED_REGION" ]]; then
  pass "Región ${EXPECTED_REGION}"
else
  fail "La región efectiva es '${effective_region:-vacía}', pero el workshop usa ${EXPECTED_REGION}."
  hint "Cambia la región en la esquina superior derecha de la consola y reabre CloudShell,"
  hint "o exporta AWS_REGION=${EXPECTED_REGION} antes de continuar."
fi

# --------------------------------------------------------------------------
printf '\n%sServicios%s\n' "$BOLD" "$RESET"
# --------------------------------------------------------------------------
if aws bedrock-runtime converse \
      --region "$AWS_REGION" \
      --model-id "$MODEL_ID" \
      --messages '[{"role":"user","content":[{"text":"ping"}]}]' \
      --inference-config '{"maxTokens":5,"temperature":0}' >/dev/null 2>&1; then
  pass "Amazon Bedrock · ${MODEL_ID} responde"
else
  warn "No se pudo invocar ${MODEL_ID} en ${AWS_REGION}."
  hint "El workshop continúa igual: la búsqueda cae al plan B por palabras clave."
  hint "Revisa Bedrock > Model access en la consola si quieres la búsqueda con IA."
fi

if aws location list-keys --region "$AWS_REGION" >/dev/null 2>&1; then
  pass "Amazon Location accesible"
else
  fail "No se pudo consultar Amazon Location en ${AWS_REGION}."
  hint "Sin Location no hay mapa. Revisa permisos del rol o usuario que estás usando."
fi

if [[ -n "$account" ]]; then
  bpa="$(aws s3control get-public-access-block --account-id "$account" --output json 2>/dev/null || true)"
  if [[ -z "$bpa" ]]; then
    pass "S3 Block Public Access · sin bloqueo a nivel de cuenta"
  elif printf '%s' "$bpa" | grep -q '"BlockPublicPolicy": true\|"RestrictPublicBuckets": true'; then
    fail "Tu cuenta bloquea las policies públicas de S3 a nivel de cuenta."
    hint "El sitio del workshop se publica con el endpoint de sitio web de S3 y no podrá desplegarse."
    hint "Este script NO cambia ese ajuste por ti: es una protección de tu cuenta y la decisión es tuya."
    hint "Alternativa sin tocarlo: pídele a quien facilita el workshop el frontend de rescate."
  else
    pass "S3 Block Public Access · no bloquea policies de bucket"
  fi
fi

# --------------------------------------------------------------------------
printf '\n%sEstado previo%s\n' "$BOLD" "$RESET"
# --------------------------------------------------------------------------
if aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$AWS_REGION" >/dev/null 2>&1; then
  status="$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$AWS_REGION" \
            --query 'Stacks[0].StackStatus' --output text)"
  warn "Ya existe una stack '${STACK_NAME}' en estado ${status}."
  hint "'sam deploy' la actualizará en vez de crearla. Para empezar de cero: ./scripts/cleanup.sh"
else
  pass "No hay una stack '${STACK_NAME}' previa · deploy limpio"
fi

# --------------------------------------------------------------------------
printf '\n%s─────────────────────────────────────────%s\n' "$DIM" "$RESET"
if (( failures > 0 )); then
  printf '%s✗ %d bloqueo(s) y %d aviso(s).%s Resuelve los bloqueos antes de continuar.\n\n' \
    "$RED" "$failures" "$warnings" "$RESET"
  exit 1
fi

if (( warnings > 0 )); then
  printf '%s! %d aviso(s), ningún bloqueo.%s Puedes continuar con: sam build && sam deploy\n\n' \
    "$YELLOW" "$warnings" "$RESET"
  exit 0
fi

printf '%s✓ Todo listo.%s Continúa con: sam build && sam deploy\n\n' "$GREEN" "$RESET"
