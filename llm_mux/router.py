from __future__ import annotations

from .config import Upstream


class UpstreamRegistry:
    def __init__(self, upstreams: tuple[Upstream, ...]) -> None:
        self._upstreams = {upstream.name: upstream for upstream in upstreams}

    def list_upstreams(self) -> list[str]:
        return sorted(self._upstreams)

    def get_upstream(self, public_model: str) -> tuple[Upstream, str]:
        prefix, separator, upstream_model = public_model.partition("-")
        if not separator or not upstream_model:
            raise UnknownModelError(public_model, self.list_upstreams())

        try:
            upstream = self._upstreams[prefix]
        except KeyError as exc:
            raise UnknownModelError(public_model, self.list_upstreams()) from exc

        return upstream, upstream_model


class UnknownModelError(ValueError):
    def __init__(self, model: str, available_upstreams: list[str]) -> None:
        super().__init__(f"Unknown model '{model}'")
        self.model = model
        self.available_upstreams = available_upstreams
