"""
Great multiplatform code from https://github.com/HazyResearch/numbskull project
"""
import socket

def set_tcp_keepalive(sock, opts):
    """Ensure that TCP keepalives are set for the socket."""
    if hasattr(socket, 'SO_KEEPALIVE'):
        if opts.get('tcp_keepalive', False):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            if hasattr(socket, 'SOL_TCP'):
                if hasattr(socket, 'TCP_KEEPIDLE'):
                    tcp_keepalive_idle = opts.get('tcp_keepalive_idle', -1)
                    if tcp_keepalive_idle > 0:
                        sock.setsockopt(
                            socket.SOL_TCP, socket.TCP_KEEPIDLE,
                            int(tcp_keepalive_idle))
                if hasattr(socket, 'TCP_KEEPCNT'):
                    tcp_keepalive_cnt = opts.get('tcp_keepalive_cnt', -1)
                    if tcp_keepalive_cnt > 0:
                        sock.setsockopt(
                            socket.SOL_TCP, socket.TCP_KEEPCNT,
                            int(tcp_keepalive_cnt))
                if hasattr(socket, 'TCP_KEEPINTVL'):
                    tcp_keepalive_intvl = opts.get('tcp_keepalive_intvl', -1)
                    if tcp_keepalive_intvl > 0:
                        sock.setsockopt(
                            socket.SOL_TCP, socket.TCP_KEEPINTVL,
                            int(tcp_keepalive_intvl))
            if hasattr(socket, 'SIO_KEEPALIVE_VALS'):
                # Windows doesn't support TCP_KEEPIDLE, TCP_KEEPCNT, nor
                # TCP_KEEPINTVL. Instead, it has its own proprietary
                # SIO_KEEPALIVE_VALS.
                tcp_keepalive_idle = opts.get('tcp_keepalive_idle', -1)
                tcp_keepalive_intvl = opts.get('tcp_keepalive_intvl', -1)
                # Windows doesn't support changing something equivalent to
                # TCP_KEEPCNT.
                if tcp_keepalive_idle > 0 or tcp_keepalive_intvl > 0:
                    # Windows defaults may be found by using the link below.
                    # Search for 'KeepAliveTime' and 'KeepAliveInterval'.
                    # https://technet.microsoft.com/en-us/library/bb726981.aspx#EDAA
                    # If one value is set and the other isn't, we still need
                    # to send both values to SIO_KEEPALIVE_VALS and they both
                    # need to be valid. So in that case, use the Windows
                    # default.
                    if tcp_keepalive_idle <= 0:
                        tcp_keepalive_idle = 7200
                    if tcp_keepalive_intvl <= 0:
                        tcp_keepalive_intvl = 1
                    # The values expected are in milliseconds, so multiply by
                    # 1000.
                    sock.ioctl(socket.SIO_KEEPALIVE_VALS, (
                        1, int(tcp_keepalive_idle * 1000),
                        int(tcp_keepalive_intvl * 1000)))
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 0)