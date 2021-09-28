import os
import socket

from livelock.tcp_info import from_socket

ss_bin = os.popen('which ss').read().strip()


def ss(port):
    print(os.popen('%s -t -n -o -a dport = :%s or sport = :%s' % (ss_bin, port, port)).read())


def check_buffer(c):
    acked, in_flight, notsent = socket_info(c)
    print("delivered, acked", acked)
    print("in-flight:", in_flight)
    print("in queue, not in flight:", notsent)


def socket_info(c):
    if isinstance(c, int):
        c = socket.fromfd(c, socket.AF_INET, socket.SOCK_STREAM)
    ti = from_socket(c)
    acked = ti.tcpi_bytes_acked - 1
    in_flight = ti.tcpi_bytes_sent - ti.tcpi_bytes_retrans - ti.tcpi_bytes_acked + 1
    notsent = ti.tcpi_notsent_bytes
    return acked, in_flight, notsent
