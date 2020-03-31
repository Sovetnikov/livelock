from prometheus_client import Gauge, Histogram

max_lock_live_time = Gauge('livelock_max_lock_live_time_seconds', 'Maximum locked time of all active locks, in seconds')
latency = Histogram('livelock_operations_latency_seconds', 'Operations latency')
