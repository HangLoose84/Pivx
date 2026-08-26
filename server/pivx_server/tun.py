"""Interfaz TUN de Linux para el plano de datos de Pivx.

Crea un dispositivo TUN (capa 3) `/dev/net/tun`, lo pone en modo raw-IP
(IFF_TUN | IFF_NO_PI: sin cabecera de 4 bytes), lo levanta y permite anadir/quitar
rutas que dirijan subredes internas hacia esta interfaz.

Requiere privilegios de root (o CAP_NET_ADMIN). Pensado para Linux/WSL2.

NOTA de division de responsabilidades: aqui NO hay pila TCP/IP. Solo se leen y
escriben paquetes IP crudos. La terminacion TCP/UDP ocurre en el agente (gVisor).
"""

from __future__ import annotations

import fcntl
import os
import struct
import subprocess

# ioctl para configurar la interfaz TUN.
TUNSETIFF = 0x400454CA
IFF_TUN = 0x0001    # dispositivo TUN (capa 3, paquetes IP crudos)
IFF_NO_PI = 0x1000  # CRITICO: sin los 4 bytes de metadatos (flags+proto) que el
#                     kernel antepondria a cada paquete. Con PI activado, el
#                     netstack de gVisor del agente leeria basura al inicio del
#                     paquete y el nibble de version IP no cuadraria -> se romperia
#                     la inyeccion (InjectInbound). NO_PI = paquete IP puro.

TUN_CLONE_DEV = "/dev/net/tun"

# MTU del tunel. 1350 deja holgura para el overhead del framing WebSocket (y de
# cualquier TLS/wss futuro) por debajo del 1500 tipico de Ethernet, evitando que
# el kernel tenga que fragmentar los paquetes que entran a pivx0. DEBE coincidir
# con tunMTU en agent/netstack.go.
DEFAULT_MTU = 1350


class TunDevice:
    """Envoltorio de una interfaz TUN en modo raw-IP."""

    def __init__(self, name: str = "pivx0", mtu: int = DEFAULT_MTU):
        self.name = name
        self.mtu = mtu
        self.fd = os.open(TUN_CLONE_DEV, os.O_RDWR)
        ifr = struct.pack("16sH", name.encode(), IFF_TUN | IFF_NO_PI)
        try:
            fcntl.ioctl(self.fd, TUNSETIFF, ifr)
        except OSError:
            os.close(self.fd)
            raise
        # Levantar la interfaz y fijar MTU.
        self._ip("link", "set", "dev", name, "up")
        self._ip("link", "set", "dev", name, "mtu", str(mtu))

    @staticmethod
    def _ip(*args: str) -> None:
        subprocess.run(["ip", *args], check=True,
                       capture_output=True, text=True)

    # --- E/S de paquetes ---------------------------------------------------

    def read(self, n: int = 65535) -> bytes:
        return os.read(self.fd, n)

    def write(self, data: bytes) -> int:
        return os.write(self.fd, data)

    # --- gestion de rutas --------------------------------------------------

    def add_route(self, cidr: str) -> None:
        self._ip("route", "replace", cidr, "dev", self.name)

    def del_route(self, cidr: str) -> None:
        # No fallar si la ruta ya no existe.
        subprocess.run(["ip", "route", "del", cidr, "dev", self.name],
                       check=False, capture_output=True, text=True)

    def close(self) -> None:
        try:
            os.close(self.fd)
        except OSError:
            pass
