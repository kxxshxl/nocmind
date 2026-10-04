# DNS Failure Runbook

# Symptoms
DNS incidents present as elevated name resolution latency, DNS timeout errors and cascading application request failures.
# Diagnosis
Inspect resolver health, timeout rate, CPU and recent resolver logs. Check whether application latency closely follows DNS latency.
# Remediation
Restart an unhealthy resolver when policy allows. Verify that the resolver returns successful queries and application latency recovers.
