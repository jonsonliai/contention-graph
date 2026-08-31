## Baseline stability

  runs/baseline_a              n= 400  p50=    26.2ms  p95=    34.0ms  mean=    27.1ms
  runs/baseline_b              n= 400  p50=    26.2ms  p95=    35.1ms  mean=    27.2ms

  p95 spread across baselines: 1.03x (threshold 1.25x)
  Baselines agree; the environment held still across the two runs.

## Victim degradation vs aggressor intensity

  run                              aggr  served       p50        p95  p95 ratio  KV peak
  runs/contention_low          6@0.25/s     6/6      26.3       39.7      1.17x    0.716
  runs/contention_mid          12@0.5/s   12/12      26.7      104.2      3.06x    0.718
  runs/contention_high         24@1.0/s   24/24      27.1      352.2     10.34x    0.761

  p95 ratio is against the first baseline. `served` shows how many aggressor
  requests actually completed: a point where they did not applied less pressure
  than its label suggests.

