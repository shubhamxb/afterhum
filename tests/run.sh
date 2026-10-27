#!/bin/sh
# run every test file; exit non-zero if any fails. usage: tests/run.sh [python]   (default: python3)
PY="${1:-python3}"; cd "$(dirname "$0")/.."; bad=0
for t in tests/test_*.py; do
  out=$("$PY" "$t" 2>&1); rc=$?
  ok=$(printf '%s\n' "$out" | grep -c '^ok'); fail=$(printf '%s\n' "$out" | grep -c '^FAIL')
  printf '%-22s rc=%s ok=%s FAIL=%s\n' "$(basename "$t")" "$rc" "$ok" "$fail"
  [ "$rc" -ne 0 ] || [ "$fail" -ne 0 ] && { bad=1; printf '%s\n' "$out" | grep -E '^FAIL|Error|Traceback' | head -6; }
done
exit $bad
