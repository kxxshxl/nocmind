# Router Overload Runbook

# Symptoms
Router overload commonly presents as rising CPU, saturated interfaces, increased queue depth, packet loss and elevated downstream latency.
# Diagnosis
Inspect router CPU, throughput, packet loss and dependent service latency. Correlate the start of saturation with the incident timeline. Check whether traffic is concentrated on one edge router.
# Remediation
For a saturated edge router, redistribute traffic to healthy peers where safe. Preserve routing policy and validate packet loss and latency after the change.
# Validation
Successful recovery should reduce CPU below 70 percent, packet loss below 2 percent and application latency below 150 ms.
