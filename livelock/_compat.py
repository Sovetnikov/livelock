# This code completely or it's parts is originates from redis-py package
# and distributed under original redis-py license
#
# Copyright (c) 2012 Andy McCurdy
#
#  Permission is hereby granted, free of charge, to any person
#  obtaining a copy of this software and associated documentation
#  files (the "Software"), to deal in the Software without
#  restriction, including without limitation the rights to use,
#  copy, modify, merge, publish, distribute, sublicense, and/or sell
#  copies of the Software, and to permit persons to whom the
#  Software is furnished to do so, subject to the following
#  conditions:
#
#  The above copyright notice and this permission notice shall be
#  included in all copies or substantial portions of the Software.
#
#  THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
#  EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES
#  OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
#  NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
#  HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
#  WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
#  FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR
#  OTHER DEALINGS IN THE SOFTWARE.

import errno
import sys

# For Python older than 3.5, retry EINTR.
if sys.version_info[0] < 3 or (sys.version_info[0] == 3 and
                               sys.version_info[1] < 5):
    # Adapted from https://bugs.python.org/review/23863/patch/14532/54418
    import socket
    import time

    # Wrapper for handling interruptable system calls.
    def _retryable_call(s, func, *args, **kwargs):
        # Some modules (SSL) use the _fileobject wrapper directly and
        # implement a smaller portion of the socket interface, thus we
        # need to let them continue to do so.
        timeout, deadline = None, 0.0
        attempted = False
        try:
            timeout = s.gettimeout()
        except AttributeError:
            pass

        if timeout:
            deadline = time.time() + timeout

        try:
            while True:
                if attempted and timeout:
                    now = time.time()
                    if now >= deadline:
                        raise socket.error(errno.EWOULDBLOCK, "timed out")
                    else:
                        # Overwrite the timeout on the socket object
                        # to take into account elapsed time.
                        s.settimeout(deadline - now)
                try:
                    attempted = True
                    return func(*args, **kwargs)
                except socket.error as e:
                    if e.args[0] == errno.EINTR:
                        continue
                    raise
        finally:
            # Set the existing timeout back for future
            # calls.
            if timeout:
                s.settimeout(timeout)

    def recv(sock, *args, **kwargs):
        return _retryable_call(sock, sock.recv, *args, **kwargs)

    def recv_into(sock, *args, **kwargs):
        return _retryable_call(sock, sock.recv_into, *args, **kwargs)

else:  # Python 3.5 and above automatically retry EINTR
    def recv(sock, *args, **kwargs):
        return sock.recv(*args, **kwargs)

    def recv_into(sock, *args, **kwargs):
        return sock.recv_into(*args, **kwargs)