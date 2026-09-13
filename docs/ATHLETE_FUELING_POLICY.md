# Athlete fueling policy

Policy version: `2026-09-12.1`

Glycofy calculates a stable profile baseline first, then applies completed-workout recovery and upcoming-workout fueling. Generated meal totals are never reused as the next baseline, which prevents repeated planning from ratcheting targets upward.

## Workload response

- Daily carbohydrate is the primary training-load lever. Planned demand rises monotonically from light/short sessions through long race sessions, bounded at 3–8 g/kg/day for the supported planning range.
- Protein stays within 1.4–2.2 g/kg/day. The normal complete-profile target is 1.8 g/kg/day; demanding strength, HIIT/HYROX, hard/race, recovery, and very long sessions may increase it to 1.9–2.0 g/kg/day.
- Workout energy is conservatively supported even when the baseline carbohydrate amount already clears the minimum band. Remaining energy is carbohydrate-forward, with an 8 g/kg carbohydrate ceiling in the current product scope.
- Implausible device calorie reports are bounded at 14 MET-hours before they influence meal targets.
- Protein is distributed across every selected eating occasion. Extra snacks divide the same daily prescription rather than adding unbounded calories.

## Evidence base and limits

The implementation follows the broad ranges and periodized approach described by the ACSM/Academy/Dietitians of Canada joint position, World Athletics consensus, IOC RED-S consensus, AIS athlete guidance, and ISSN protein positions. These are planning estimates, not medical nutrition therapy. Individual gastrointestinal tolerance, environmental conditions, menstrual status, body composition, illness, and clinical needs can require an individualized sports-dietitian plan.

## Automated validation

`tests/test_training_nutrition.py` exercises 960 planned athlete/workout combinations across four body weights, five sports, four intensities, and twelve duration boundaries. It asserts monotonic workload response, carbohydrate and protein bounds, calorie/macro reconciliation, completed-workout response, future/past time handling, and corrupt-device-data protection. Meal allocation tests verify exact daily reconciliation and protein spacing across multiple snack schedules.
