# pip install prometheus-client
try:
    from prometheus_client import Gauge, Histogram, Counter

    prometheus_client_installed = True
    lock_count = Gauge('livelock_lock_count', 'Active locks count')
    max_lock_live_time = Gauge('livelock_max_lock_live_time_seconds', 'Maximum locked time of all active locks, in seconds')
    latency = Histogram('livelock_operations_latency_seconds', 'Operations latency', labelnames=('verb',))
    stats_collection_time = Counter('livelock_stats_collection_time_seconds', 'Time spent collecting stats, in seconds')
    stats_collection_count = Counter('livelock_stats_collection_count', 'Stats collection calls')
    maintenance_time = Counter('livelock_maintenance_time_seconds', 'Time spent doing maintenance, in seconds')
    maintenance_count = Counter('livelock_maintenance_count', 'Maintenance calls counter')
except ImportError:
    prometheus_client_installed = False
    class PrometheusStub(object):
        def set(self, *args, **kwargs):
            pass

        def time(self):
            # Histogram.time decorator stub
            return self._time

        def _time(self, f):
            return f

        def inc(self, *args, **kwargs):
            pass

        def labels(self, *args, **kwargs):
            return self

        def observe(self, *args, **kwargs):
            pass

    lock_count = PrometheusStub()
    max_lock_live_time = PrometheusStub()
    latency = PrometheusStub()
    stats_collection_time = PrometheusStub()
    stats_collection_count = PrometheusStub()
    maintenance_time = PrometheusStub()
    maintenance_count = PrometheusStub()
