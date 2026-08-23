__all__ = ("AddressInUseError", "serve")


class AddressInUseError(RuntimeError):
    """The requested port is already bound by another process."""


def serve(
    target: str,
    *,
    host: str = "127.0.0.1",
    port: int = 8000,
    reload: bool = False,
    **granian_kwargs,
) -> None:
    """Serve an RSGI target ("module:attr") with granian, blocking until shutdown.

    Extra keyword arguments go to the ``Granian`` constructor verbatim.
    Raises AddressInUseError instead of granian's raw RuntimeError when the
    port is taken, so CLIs can show a clean message.
    """
    from granian import Granian
    from granian.constants import Interfaces

    server = Granian(
        target,
        address=host,
        port=port,
        interface=Interfaces.RSGI,
        reload=reload,
        **granian_kwargs,
    )
    try:
        server.serve()
    except RuntimeError as exc:
        if "Address already in use" in str(exc):
            raise AddressInUseError(
                f"port {port} is already in use; stop the existing process "
                "or pick another port"
            ) from exc
        raise
