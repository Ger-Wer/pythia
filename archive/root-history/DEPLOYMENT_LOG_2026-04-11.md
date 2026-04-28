# Deployment Log: 2026-04-11

## Tier-1 Critical Fixes - Production Deployment

**Timestamp:** 2026-04-11, ~17:35 UTC  
**Commit:** 0d6dc1b (feat: tier-1 critical audit fixes)  
**Status:** ✅ SUCCESSFUL - Both projects healthy

### What Was Deployed

1. **research_status enum expansion** (ON → 7 states)
   - `'ON' | 'PARTIAL' | 'TIMEOUT' | 'COST_LIMIT' | 'DATA_STALE' | 'RESOLVER_OFFLINE' | 'ERROR'`
   - Added `research_status_detail` JSON column to signals table
   - Operators now see WHY research failed, not just binary ON/DEGRADED

2. **Cost burn rate alerting**
   - Hourly burn projection in CostTracker
   - Pre-forecast warning if projected daily cost > 110% of limit
   - Post-forecast report showing remaining budget percentage

3. **Position lifecycle automation**
   - Position events now created for: opened, adverse_move, review_due, reviewed, closed
   - Full position timeline visible in dashboard

4. **Brier/log-loss tracking** (verified working)
   - Already implemented; confirmed running daily at 18:00 UTC
   - Weekly calibration reports active

### Deployment Process

```
git fetch && git pull --rebase origin main  [amper]
docker compose build                        [amper]
docker compose up -d                        [amper]
```

### Health Check Results

**Pythia Containers:** ✅ All healthy
- pythia-scheduler: healthy (cron running)
- pythia-dashboard: healthy (web server running)
- pythia-telegram: healthy (bot running)

**SproutSignal Containers:** ✅ All healthy (undisturbed)
- 7 containers verified running with no interruption

### Next Steps

1. Monitor logs for 24h
2. Verify research_status_detail JSON appears in signals table
3. Watch for cost burn rate alerts if forecast approaches budget
4. Confirm position_events populate for new positions opened after deploy

### Rollback Plan

If needed, on amper:
```bash
cd /home/pythia/pythia
git checkout 29b9f35  # Previous commit before Tier-1 changes
docker compose build && docker compose up -d
```

---

**Deployed by:** AI Architect  
**Verified intact:** SproutSignal (7 containers, all healthy, uptime unchanged)  
**Documentation updated:** README.md, WORKSTREAM.md dates set to 2026-04-11
