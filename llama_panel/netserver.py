"""Servidor HTTP que atiende IPv4 e IPv6 en el mismo puerto.

avahi publica el nombre de la máquina (god.local) con sus direcciones IPv4 e IPv6, y Windows prefiere la IPv6. Con un
servidor solo IPv4, el navegador que la elige intenta conectar donde no escucha nadie. Las IPv4 llegan como
::ffff:a.b.c.d y se pasan a a.b.c.d para que auth.py las vea igual que antes (127.0.0.1 sigue siendo local).
"""
import errno
import ipaddress
import socket
from http.server import ThreadingHTTPServer


def plain_ip(ip):
    """'::ffff:192.168.1.5' -> '192.168.1.5'; cualquier otra dirección queda igual."""
    try:
        mapped = getattr(ipaddress.ip_address(ip.split("%")[0]), "ipv4_mapped", None)  # las IPv4 no lo tienen
    except ValueError:
        return ip
    return str(mapped) if mapped else ip


class DualStackServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 64

    def __init__(self, address, handler):
        """`address` como en HTTPServer; ("0.0.0.0", puerto) o ("::", puerto) escuchan en IPv4 e IPv6."""
        host, port = address
        if host not in ("", "0.0.0.0", "::"):
            return super().__init__(address, handler)  # una dirección puntual (los tests usan 127.0.0.1)
        if socket.has_ipv6:
            self.address_family = socket.AF_INET6
            try:
                super().__init__(("::", port), handler)
                return
            except OSError as e:
                # sin IPv6 en la máquina (deshabilitado por sysctl): solo IPv4. Un puerto ocupado sí es un error.
                if e.errno not in (errno.EAFNOSUPPORT, errno.EADDRNOTAVAIL):
                    raise
        self.address_family = socket.AF_INET
        super().__init__(("0.0.0.0", port), handler)

    def server_bind(self):
        if self.address_family == socket.AF_INET6:
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        super().server_bind()

    def get_request(self):
        sock, addr = super().get_request()
        return sock, (plain_ip(addr[0]), *addr[1:])
