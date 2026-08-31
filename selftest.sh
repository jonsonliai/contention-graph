#!/usr/bin/env bash
# Validates the pipeline end to end with synthetic inputs. No GPU, no runtime, no network.
# Run this before booking accelerator time.
set -e
python3 -m src.collect --out runs/_selftest_baseline  --mock
python3 -m src.collect --out runs/_selftest_contention --mock
python3 -m src.analyze runs/_selftest_baseline runs/_selftest_contention --report runs/_selftest_report.md
echo
echo "Pipeline OK. runs/_selftest_report.md written."
echo "NOTE: these are synthetic inputs. The verdicts above are a test of the harness,"
echo "      not a finding about any runtime."
