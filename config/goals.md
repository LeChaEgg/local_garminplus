---
profile:
  name: Hong
  timezone: Asia/Tokyo
  sex: M
  height_cm: 175
  birth_year: 1990

goal_weights:
  sleep_recovery: 0.30
  aerobic: 0.30
  strength: 0.25
  body_shape: 0.15

sleep_recovery:
  sleep_duration_range_h: [7.0, 8.5]
  garmin_sleep_score_min: 75
  hrv_baseline_window_days: 28
  rhr_baseline_window_days: 28
  sleep_latency_target_min: 20

aerobic:
  weekly_minutes_min: 240
  weekly_z2_minutes_min: 150
  weekly_hard_sessions_max: 2
  long_run_every_n_days: 7
  include_cycling: true

strength:
  weekly_sessions_min: 2
  movement_patterns: [squat, hinge, push, pull, carry, core]
  progressive_overload: true

body_shape:
  weight_trend: maintain
  waist_cm_target: 80
  monthly_photo_check: true
  subjective_score_min: 7

guardrails:
  never_hard_training_if_readiness_below: 60
  avoid_hard_training_after_bad_sleep: true
  max_consecutive_training_days: 6
---

# Narrative goals (free-form)

- Stay healthy and consistent. No injuries.
- Be able to run a relaxed 10K any weekend.
- Keep VO2max trend flat or improving year over year.
- Body shape: stay lean enough to see abdominal definition without aggressive dieting.
- Sleep is the highest priority recovery lever; do not trade sleep for training.
