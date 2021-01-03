# pip install prometheus-client
try:
    from prometheus_client import Gauge, Histogram, Counter

    prometheus_client_installed = True
    max_lock_live_time = Gauge('livelock_max_lock_live_time_seconds', 'Maximum locked time of all active locks, in seconds')
    latency = Histogram('livelock_operations_latency_seconds', 'Operations latency')
    stats_collection_time = Counter('livelock_stats_collection_time_seconds', 'Time spent collecting stats, in seconds')
    maintenance_time = Counter('livelock_maintenance_time_seconds', 'Time spent doing maintenance, in seconds')
except ImportError:
    prometheus_client_installed = False
    class PrometheusStub(object):
        def set(self, *args, **kwargs):
            pass

        def time(self):
            return self._time

        def _time(self, f):
            return f

        def inc(self, *args, **kwargs):
            pass

    max_lock_live_time = PrometheusStub()
    latency = PrometheusStub()
    stats_collection_time = PrometheusStub()
    maintenance_time = PrometheusStub()
