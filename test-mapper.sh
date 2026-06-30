#!/bin/bash
# Connect Resource Mapper — Test Runner
# Runs unit tests (no AWS creds needed), then optionally runs
# an integration test against a live Connect instance.
#
# Usage:
#   bash test-mapper.sh                              # Unit tests only
#   bash test-mapper.sh --integration               # Unit + integration
#
# For integration tests, set these environment variables:
#   CONNECT_INSTANCE_ID   Your Connect instance ID
#   AWS_REGION            AWS region (default: us-east-1)
#   AWS_PROFILE           AWS credentials profile (optional)

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OUTPUT_DIR="${SCRIPT_DIR}/test-output"
REGION="${AWS_REGION:-us-east-1}"

echo "============================================"
echo "  Connect Resource Mapper — Test Suite"
echo "============================================"
echo ""

# ─── STEP 0: Unit Tests (no AWS creds needed) ───
echo "[0] Running unit tests..."
echo ""
python3 -m pytest "${SCRIPT_DIR}/test-connect-resource-mapper.py" -v --tb=short
UNIT_EXIT=$?
echo ""

if [ $UNIT_EXIT -ne 0 ]; then
  echo "❌ Unit tests FAILED. Fix before running integration test."
  exit 1
fi

echo "✅ Unit tests passed."
echo ""

# ─── STEP 1: Integration Test (optional, requires --integration flag) ───
if [ "$1" != "--integration" ]; then
  echo "============================================"
  echo "  Result: Unit tests ✅"
  echo "  (Pass --integration to run live API tests)"
  echo "============================================"
  exit 0
fi

if [ -z "$CONNECT_INSTANCE_ID" ]; then
  echo "❌ CONNECT_INSTANCE_ID not set. Export it before running integration tests."
  echo "   export CONNECT_INSTANCE_ID=your-instance-id"
  exit 1
fi

mkdir -p "$OUTPUT_DIR"

echo "============================================"
echo "  Integration Test — Live API"
echo "  Instance: $CONNECT_INSTANCE_ID"
echo "  Region:   $REGION"
echo "  Output:   $OUTPUT_DIR"
echo "============================================"
echo ""

# Verify credentials
echo "[1] Verifying credentials..."
if ! aws sts get-caller-identity --output table 2>/dev/null; then
  echo "⚠️  AWS credentials not available. Cannot run integration test."
  echo ""
  echo "============================================"
  echo "  Result: Unit tests ✅ | Integration ❌ (no creds)"
  echo "============================================"
  exit 1
fi
echo ""

# Run the mapper
echo "[2] Running mapper script..."
python3 "$SCRIPT_DIR/connect-resource-mapper.py" \
  --instance-id "$CONNECT_INSTANCE_ID" \
  --region "$REGION" \
  --output-dir "$OUTPUT_DIR"

echo ""
echo "[3] Validating outputs..."

ERRORS=0
for FILE in "connect-resource-map.json" "connect-quota-impact-model.json" "connect-dashboard.html"; do
  if [ ! -s "$OUTPUT_DIR/$FILE" ]; then
    echo "  ❌ $FILE — missing or empty"
    ERRORS=$((ERRORS + 1))
  else
    SIZE=$(wc -c < "$OUTPUT_DIR/$FILE" | tr -d ' ')
    echo "  ✅ $FILE — ${SIZE} bytes"
  fi
done

echo ""
echo "[4] Validating JSON structure..."
python3 -c "
import json, sys

errors = 0

with open('$OUTPUT_DIR/connect-resource-map.json') as f:
    rmap = json.load(f)

for key in ['instance_id', 'region', 'collected_at', 'phone_numbers', 'contact_flows', 'lambda_functions', 'quotas', 'usage_metrics']:
    if key not in rmap:
        print(f'  ❌ resource-map missing key: {key}')
        errors += 1
    else:
        val = rmap[key]
        count = len(val) if isinstance(val, (list, dict)) else 'scalar'
        print(f'  ✅ resource-map.{key}: {count}')

print()

with open('$OUTPUT_DIR/connect-quota-impact-model.json') as f:
    model = json.load(f)

for key in ['generated_at', 'flow_to_lambda_map', 'tdg_number_distribution', 'quota_headroom', 'migration_impact_formulas', 'summary']:
    if key not in model:
        print(f'  ❌ impact-model missing key: {key}')
        errors += 1
    else:
        val = model[key]
        count = len(val) if isinstance(val, (list, dict)) else 'scalar'
        print(f'  ✅ impact-model.{key}: {count}')

print()
if errors > 0:
    print(f'❌ {errors} validation errors')
    sys.exit(1)
else:
    print('✅ All validations passed')
"

echo ""
echo "============================================"
echo "  Result: Unit tests ✅ | Integration ✅"
echo "  Output: $OUTPUT_DIR"
echo "============================================"
